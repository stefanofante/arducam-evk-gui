"""
Arducam EVK camera GUI.

This module implements the Tkinter front-end of the GUIcamera
application. The GUI is split in three cooperating components:

* :class:`CameraWorker` -- thin wrapper around the SDK ``Camera``
  object. It owns the USB capture thread and pushes the latest
  frame onto a bounded queue together with simple capture statistics.
* :class:`CameraGUI` -- the Tkinter application. It builds the widget
  tree, drives the user interaction, periodically polls the worker's
  frame queue and updates the preview canvas, status bar and log
  console using ``Tk.after`` timers (so the main loop is never
  blocked).
* Module-level helpers -- ``main()`` boots a Tk root, instantiates
  :class:`CameraGUI` and starts the main loop.

Sensor configuration is discovered at run-time and is therefore not
hard-coded in this file:

* ``configs/*.cfg`` -- SDK camera configurations. Only the ``TYPE``,
  ``SIZE``, ``BIT_WIDTH``, ``FORMAT`` and ``I2C_*`` fields are read by
  the GUI metadata parser; the SDK itself consumes the full file.
* ``sensors/*.json`` -- per-sensor register maps and UI hints. A
  configuration is bound to a profile through the case-insensitive
  ``match`` list contained in the JSON file.

Adding a new sensor therefore amounts to dropping a matching pair of
``.cfg`` and ``.json`` files in the appropriate directories; no Python
change is required.

Author:  Stefano Fante - STLINE srl
License: MIT (see ../LICENSE)
"""

from __future__ import annotations

import os
import sys
import queue
import threading
import time
import traceback
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    print("Tkinter is required. On Windows it ships with the standard installer.")
    sys.exit(1)

try:
    from PIL import Image, ImageTk
except ImportError:
    print("Pillow is required. Install with:  pip install Pillow")
    sys.exit(1)

import cv2
import numpy as np

try:
    from ArducamEvkSDK import (
        Camera,
        DeviceList,
        LoggerLevel,
        MemType,
        Param,
        get_error_name,
    )
except ImportError:
    print("ArducamEvkSDK is required. Install with:  pip install ArducamEvkSDK")
    sys.exit(1)

# Import configuration manager and utilities
from config_manager import ConfigManager
from utils.image_converter import convert_image, from_image


# ============================================================================
# Constants
# ============================================================================

REPO_ROOT = Path(__file__).resolve().parent.parent
PREVIEW_MAX_W = 800
PREVIEW_MAX_H = 600
LOG_MAX_LINES = 1000


# ============================================================================
# Statistics & Worker Dataclasses
# ============================================================================

@dataclass
class Stats:
    """Capture statistics shared between the worker and the GUI.

    Instances are mutated by the capture thread under
    :attr:`CameraWorker.lock` and read by the Tk main loop in
    :meth:`CameraGUI._tick_status`. All fields are plain Python
    scalars so reads are inherently atomic on CPython, but the lock
    is still used to guarantee a consistent snapshot.

    Attributes:
        fps: Nominal frame-rate declared by the configuration
            (currently unused, kept for forward compatibility).
        fps_measured: Rolling average computed once per second from
            the actual frame arrival timestamps.
        bw_mb: Effective USB bandwidth in megabytes per second
            (reserved for future use).
        seq: Sequence number of the last frame received.
        width: Width in pixels of the last frame received.
        height: Height in pixels of the last frame received.
        frames: Total number of frames successfully pulled from the
            SDK since :meth:`CameraWorker.start`.
        dropped: Number of frames discarded because the GUI queue was
            full.
        missed: Number of frames the SDK reported as missing.
        bad_convert: Number of frames whose conversion to a display
            image raised an exception.
        attempts: Number of capture attempts performed by the worker
            (frames + errors).
        none_frames: Number of times the SDK returned ``None`` within
            the configured timeout.
    """

    fps: float = 0.0
    fps_measured: float = 0.0
    bw_mb: float = 0.0
    seq: int = 0
    width: int = 0
    height: int = 0
    frames: int = 0
    dropped: int = 0
    missed: int = 0
    bad_convert: int = 0
    attempts: int = 0
    none_frames: int = 0


# ============================================================================
# Camera Worker (SDK Wrapper)
# ============================================================================

class CameraWorker:
    """Owns the SDK :class:`Camera` object and the capture thread.

    The worker hides every interaction with the EVK SDK from the GUI
    layer. It is responsible for:

    * enumerating the USB devices that the SDK can see,
    * opening / initialising / closing the camera against a given
      ``.cfg`` file,
    * starting and stopping the streaming loop,
    * running the capture loop on a daemon thread and publishing the
      latest frame on :attr:`frame_queue` together with the running
      :class:`Stats`,
    * exposing single-register read/write helpers for the GUI
      register editor.

    Threading model
    ---------------
    All public methods are called from the Tk main thread. The capture
    loop runs in a dedicated daemon thread which only writes to
    :attr:`frame_queue` (bounded to two slots to bound memory and
    drop stale frames automatically) and to :attr:`stats` while
    holding :attr:`lock`. The worker never mutates the SDK objects
    from the capture thread.

    Log messages produced by the SDK callbacks and by error paths are
    forwarded to the GUI through the ``log_callback`` provided at
    construction time.
    """

    def __init__(self, log_callback):
        """Create an idle worker.

        Args:
            log_callback: Callable accepting a single ``str`` argument.
                It is invoked from arbitrary threads, so the callback
                must be thread-safe (the GUI uses a :class:`queue.Queue`
                drained by a Tk ``after`` timer).
        """
        self.log_callback = log_callback
        self.camera: Optional[Camera] = None
        self.device_list: Optional[DeviceList] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # ``maxsize=2`` keeps memory bounded while still letting the
        # GUI display the freshest available frame even when the Tk
        # main loop temporarily falls behind the capture thread.
        self.frame_queue: queue.Queue = queue.Queue(maxsize=2)
        self.stats = Stats()
        self.lock = threading.Lock()

    def _log(self, msg: str) -> None:
        """Forward a message to the user-supplied log callback."""
        if self.log_callback:
            self.log_callback(msg)

    def enumerate_devices(self) -> List[str]:
        """Return a human-readable list of currently attached EVK devices.

        A fresh :class:`DeviceList` is allocated on every call so the
        result reflects hot-plug events that occurred after the GUI was
        started.

        Returns:
            One ``"[index] name"`` entry per device, or an empty list
            when no device is present (or the SDK raised).
        """
        try:
            self.device_list = DeviceList()
            devices = []
            for i in range(self.device_list.device_count):
                dev_name = self.device_list.device_name(i)
                devices.append(f"[{i}] {dev_name}")
            return devices
        except Exception as e:
            self._log(f"Error enumerating devices: {e}")
            return []

    def open(self, cfg_path: str, device_index: int = 0, dma: bool = False) -> bool:
        """Open the camera against a specific configuration file.

        Args:
            cfg_path: Absolute or relative path to the ``.cfg`` file
                that describes the desired sensor mode. ``.bin``
                configuration blobs are detected by extension.
            device_index: Zero-based index in the cached
                :class:`DeviceList`. Call :meth:`enumerate_devices`
                beforehand.
            dma: When ``True`` the worker enables the SDK
                ``BUFFER_TRANSFER_MODE`` with a 128-buffer ring, which
                lowers per-frame latency at the cost of a larger
                kernel-side allocation.

        Returns:
            ``True`` on success, ``False`` if any SDK call raised. The
            caller can inspect the log callback for the exact error.
        """
        try:
            if not self.device_list or device_index >= self.device_list.device_count:
                self._log("Invalid device selection")
                return False

            self.camera = Camera()
            param = Param()
            param.device_index = device_index
            param.config_file_name = cfg_path
            param.bin_config = cfg_path.lower().endswith(".bin")

            self.camera.open(param)
            self.camera.init()

            if dma:
                # The DMA path is optional and not supported by every
                # firmware revision; failures are swallowed on
                # purpose to keep the open flow tolerant.
                try:
                    self.camera.set_transfer_option(MemType.BUFFER_TRANSFER_MODE, 1, 128)
                except Exception:
                    pass

            self._log(f"Opened {cfg_path} @ device {device_index}")
            return True
        except Exception as e:
            self._log(f"Error opening camera: {e}")
            self.camera = None
            return False

    def close(self) -> None:
        """Stop streaming (if active) and release the SDK handle.

        Always safe to call: missing thread/camera objects are handled
        gracefully so the GUI can use this as a teardown primitive.
        """
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=2)
            self._thread = None
        if self.camera:
            try:
                self.camera.close()
            except Exception:
                pass
            self.camera = None
        self._log("Camera closed")

    def start(self) -> bool:
        """Start the SDK stream and spawn the capture thread.

        Returns:
            ``True`` if the stream was successfully started, ``False``
            otherwise (no camera open, SDK error, ...).
        """
        try:
            if not self.camera:
                return False
            self.camera.start()
            self._stop.clear()
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()
            self._log("Streaming started")
            return True
        except Exception as e:
            self._log(f"Error starting stream: {e}")
            return False

    def stop(self) -> None:
        """Signal the capture thread to exit and stop the SDK stream."""
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=2)
            self._thread = None
        if self.camera:
            try:
                self.camera.stop()
            except Exception:
                pass
        self._log("Streaming stopped")

    def _capture_loop(self) -> None:
        """Main capture loop, executed on the worker thread.

        Pulls frames from the SDK with a 1-second timeout, converts
        them to a displayable :class:`numpy.ndarray` (BGR) and pushes
        the result on the bounded frame queue. Statistics are updated
        atomically under :attr:`lock`. The loop exits on the first
        unhandled exception or when :attr:`_stop` is set by
        :meth:`stop`.
        """
        last_tick = time.time()
        frame_count = 0

        while not self._stop.is_set():
            try:
                frame = self.camera.capture(1000)
                if frame is None:
                    # Timeout: the SDK did not produce a frame within
                    # the requested window. This is not necessarily an
                    # error (e.g. external trigger mode), so the loop
                    # keeps running.
                    with self.lock:
                        self.stats.none_frames += 1
                    continue

                with self.lock:
                    self.stats.attempts += 1
                    self.stats.frames += 1

                # Convert RAW/YUV/RGB565/JPEG payloads to a BGR ndarray
                # suitable for ``cv2`` and Tk display. Conversion
                # errors are counted but do not stop the stream.
                try:
                    display_img = from_image(frame)
                    if display_img is not None:
                        self.frame_queue.put(display_img, block=False)
                except Exception:
                    with self.lock:
                        self.stats.bad_convert += 1

                # Measured FPS over a sliding one-second window. The
                # value is published to :attr:`stats` so the GUI can
                # poll it at its own cadence without doing any timing
                # of its own.
                now = time.time()
                frame_count += 1
                if now - last_tick > 1.0:
                    fps_measured = frame_count / (now - last_tick)
                    with self.lock:
                        self.stats.fps_measured = fps_measured
                    frame_count = 0
                    last_tick = now

            except Exception as e:
                self._log(f"Capture error: {e}")
                break

    def read_register(self, addr: int) -> Optional[int]:
        """Read a single sensor register through the SDK CCI helper.

        Args:
            addr: 16-bit register address.

        Returns:
            The register value, or ``None`` if the camera is not open
            or the SDK raised.
        """
        try:
            if not self.camera:
                return None
            return self.camera.read_register(addr)
        except Exception:
            return None

    def write_register(self, addr: int, value: int) -> bool:
        """Write a single sensor register through the SDK CCI helper.

        Args:
            addr: 16-bit register address.
            value: Value to write; width is sensor-specific.

        Returns:
            ``True`` on success, ``False`` otherwise.
        """
        try:
            if not self.camera:
                return False
            self.camera.write_register(addr, value)
            return True
        except Exception:
            return False


# ============================================================================
# Main GUI Application
# ============================================================================

class CameraGUI:
    """Top-level Tkinter application.

    Builds the widget tree, owns a single :class:`CameraWorker` and
    schedules three periodic ``Tk.after`` callbacks that drive the
    live UI updates:

    * :meth:`_tick_preview` -- 10 Hz; drains the worker frame queue
      and refreshes the preview canvas.
    * :meth:`_tick_status` -- 2 Hz; updates the bottom status bar with
      the latest :class:`Stats` snapshot.
    * :meth:`_drain_logs` -- 5 Hz; moves log records from the worker
      log queue into the visible :class:`tkinter.Text` widget while
      enforcing :data:`LOG_MAX_LINES` to avoid unbounded growth.

    The class exposes plain ``_on_*`` methods bound to the toolbar
    buttons so the same actions can be triggered programmatically by
    integration tests if needed.
    """

    def __init__(self, root: tk.Tk):
        """Build the widget tree and arm the periodic UI timers.

        Args:
            root: An already-created :class:`tkinter.Tk` instance. The
                caller is responsible for running ``root.mainloop()``
                (see :func:`main`).
        """
        self.root = root
        self.root.title("Arducam EVK Camera")
        self.root.geometry("1280x720")

        # Discover available sensors and configurations once at
        # start-up. The user can refresh the device list at any time,
        # but adding a new .cfg/.json pair currently requires
        # restarting the GUI.
        self.config_manager = ConfigManager(Path(__file__).parent)
        self.config_manager.discover_configurations()
        self.config_manager.discover_sensor_profiles()

        # Single worker shared across the whole session. The worker is
        # idle until the user clicks ``Open`` / ``Start``.
        self.worker = CameraWorker(self._log)

        # GUI-side state mirroring the worker lifecycle.
        self.current_config: Optional[Path] = None
        self.camera_open = False
        self.streaming = False
        self.sensor_profile = None
        # Thread-safe channel used by :meth:`_log` to forward worker
        # messages to the Tk main loop (see :meth:`_drain_logs`).
        self.log_queue: queue.Queue = queue.Queue()
        # Strong reference to the currently displayed PhotoImage;
        # without it Tk would garbage-collect the underlying buffer
        # while the canvas is still showing it.
        self._photo_ref = None

        self._build_ui()

        # Arm the periodic UI timers. The intervals are coarse enough
        # for a smooth user experience (~10 Hz preview) while keeping
        # the Tk event loop almost entirely idle.
        self.root.after(100, self._tick_preview)
        self.root.after(500, self._tick_status)
        self.root.after(200, self._drain_logs)

    def _build_ui(self) -> None:
        """Build UI layout."""
        # Top bar
        top_frame = ttk.Frame(self.root)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        ttk.Label(top_frame, text="Device:").pack(side=tk.LEFT)
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            top_frame, textvariable=self.device_var, state="readonly", width=30
        )
        self.device_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="Refresh", command=self._refresh_devices).pack(side=tk.LEFT)

        # Sensor selector
        ttk.Label(top_frame, text="Sensor:").pack(side=tk.LEFT, padx=(20, 0))
        self.sensor_var = tk.StringVar()
        self.sensor_combo = ttk.Combobox(
            top_frame, textvariable=self.sensor_var, state="readonly", width=20
        )
        self.sensor_combo.pack(side=tk.LEFT, padx=5)
        self.sensor_combo.bind("<<ComboboxSelected>>", self._on_sensor_selected)

        # Config selector
        ttk.Label(top_frame, text="Config:").pack(side=tk.LEFT)
        self.config_var = tk.StringVar()
        self.config_combo = ttk.Combobox(
            top_frame, textvariable=self.config_var, state="readonly", width=20
        )
        self.config_combo.pack(side=tk.LEFT, padx=5)

        # Buttons
        ttk.Button(top_frame, text="Open", command=self._on_open).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_frame, text="Close", command=self._on_close).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_frame, text="Start", command=self._on_start).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_frame, text="Stop", command=self._on_stop).pack(side=tk.LEFT, padx=2)

        # Main notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Preview tab
        preview_frame = ttk.Frame(self.notebook)
        self.notebook.add(preview_frame, text="Preview")
        self.preview_canvas = tk.Canvas(
            preview_frame, bg="black", width=PREVIEW_MAX_W, height=PREVIEW_MAX_H
        )
        self.preview_canvas.pack()

        # Info tab
        info_frame = ttk.Frame(self.notebook)
        self.notebook.add(info_frame, text="Info / Log")
        log_scroll = ttk.Scrollbar(info_frame)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = tk.Text(
            info_frame, height=20, yscrollcommand=log_scroll.set, state=tk.DISABLED
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.config(command=self.log_text.yview)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Populate sensor combo
        self._refresh_sensors()

    def _refresh_devices(self) -> None:
        """Refresh USB device list."""
        devices = self.worker.enumerate_devices()
        self.device_combo["values"] = devices
        if devices:
            self.device_combo.current(0)

    def _refresh_sensors(self) -> None:
        """Populate sensor dropdown from discovered configurations."""
        sensors = self.config_manager.get_all_sensor_types()
        self.sensor_combo["values"] = sensors
        if sensors:
            self.sensor_combo.current(0)
            self._on_sensor_selected()

    def _on_sensor_selected(self, evt=None) -> None:
        """Update config dropdown when sensor changes."""
        sensor = self.sensor_var.get()
        if not sensor:
            return

        configs = self.config_manager.get_configs_for_sensor(sensor)
        config_names = [cfg.display_name for cfg in configs]
        self.config_combo["values"] = config_names

        if configs:
            self.config_combo.current(0)
            self.current_config = configs[0].path

            # Load sensor profile
            profile = self.config_manager.get_profile_for_sensor_type(sensor)
            self.sensor_profile = profile
            if profile:
                self._log(f"Loaded profile: {profile.name}")

    def _on_open(self) -> None:
        """Open camera."""
        if not self.current_config:
            messagebox.showerror("Error", "Please select a configuration")
            return

        device_idx = self.device_combo.current()
        if device_idx < 0:
            messagebox.showerror("Error", "Please select a device")
            return

        if self.worker.open(str(self.current_config), device_idx):
            self.camera_open = True
            self._log(f"Camera opened with {self.current_config.name}")
        else:
            messagebox.showerror("Error", "Failed to open camera")

    def _on_close(self) -> None:
        """Close camera."""
        if self.streaming:
            self._on_stop()
        self.worker.close()
        self.camera_open = False
        self._log("Camera closed")

    def _on_start(self) -> None:
        """Start streaming."""
        if not self.camera_open:
            messagebox.showerror("Error", "Camera not open")
            return
        if self.worker.start():
            self.streaming = True

    def _on_stop(self) -> None:
        """Stop streaming."""
        self.worker.stop()
        self.streaming = False

    def _tick_preview(self) -> None:
        """Update preview canvas."""
        try:
            frame = self.worker.frame_queue.get(block=False)
            # Convert frame to PhotoImage
            h, w = frame.shape[:2]
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            photo = ImageTk.PhotoImage(img)
            self._photo_ref = photo
            self.preview_canvas.create_image(0, 0, image=photo, anchor=tk.NW)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._tick_preview)

    def _tick_status(self) -> None:
        """Update status bar."""
        with self.worker.lock:
            fps = self.worker.stats.fps_measured
            frames = self.worker.stats.frames
        status = f"FPS: {fps:.1f} | Frames: {frames} | {'Streaming' if self.streaming else 'Idle'}"
        self.status_var.set(status)
        self.root.after(500, self._tick_status)

    def _drain_logs(self) -> None:
        """Drain log messages from worker thread."""
        while True:
            try:
                msg = self.log_queue.get(block=False)
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, msg + "\n")
                # Trim to LOG_MAX_LINES
                lines = int(self.log_text.index(tk.END).split(".")[0])
                if lines > LOG_MAX_LINES:
                    self.log_text.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
            except queue.Empty:
                break
        self.root.after(200, self._drain_logs)

    def _log(self, msg: str) -> None:
        """Log a message (thread-safe)."""
        self.log_queue.put(msg)

    def _on_close_window(self) -> None:
        """Handle window close."""
        if self.streaming:
            self._on_stop()
        if self.camera_open:
            self._on_close()
        self.root.destroy()


# ============================================================================
# Main Entry Point
# ============================================================================

def main() -> None:
    """Boot a Tk root and run the GUI main loop.

    This function is the documented entry-point of the package. It is
    invoked both by :mod:`launch_gui` and when the module is executed
    directly (``python gui.py``).
    """
    root = tk.Tk()

    try:
        # Empty string falls back to the default Tk icon while still
        # exercising the call so the catch-all below keeps the GUI
        # portable on platforms without ``iconbitmap`` support.
        root.iconbitmap(default="")
    except tk.TclError:
        pass

    app = CameraGUI(root)
    # Route the window manager close button through our shutdown path
    # so the capture thread and SDK handles are released cleanly.
    root.protocol("WM_DELETE_WINDOW", app._on_close_window)
    root.mainloop()


if __name__ == "__main__":
    main()
