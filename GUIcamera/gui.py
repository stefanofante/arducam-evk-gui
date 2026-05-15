"""
Arducam EVK camera GUI.

Sensor configuration is read from:
- configs/*.cfg    (TYPE, SIZE, BIT_WIDTH, FORMAT, I2C_*)
- sensors/*.json   (register map and UI hints)

Add a new sensor by dropping matching .cfg + .json files; no code change.
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
    """Capture statistics."""
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
    """
    Manages SDK Camera object and capture thread.

    Responsibilities:
    * Keeps DeviceList and Camera alive throughout session
    * Wraps Camera.open/init/start/stop/close for the GUI thread
    * Runs daemon capture thread that pulls frames and updates stats
    * Maintains thread-safe Stats object for GUI reads

    Threading:
    * GUI calls public methods from Tk main thread
    * Capture thread only writes to frame_queue and stats (under lock)
    * Worker never mutates SDK objects from capture thread
    """

    def __init__(self, log_callback):
        self.log_callback = log_callback
        self.camera: Optional[Camera] = None
        self.device_list: Optional[DeviceList] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.frame_queue: queue.Queue = queue.Queue(maxsize=2)
        self.stats = Stats()
        self.lock = threading.Lock()

    def _log(self, msg: str) -> None:
        if self.log_callback:
            self.log_callback(msg)

    def enumerate_devices(self) -> List[str]:
        """Enumerate connected USB devices."""
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
        """
        Open camera with specified config file.

        Args:
            cfg_path: Path to .cfg configuration file
            device_index: USB device index
            dma: Enable DMA mode

        Returns:
            True if successful, False otherwise
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
                try:
                    self.camera.set_transfer_option(MemType.BUFFER_TRANSFER_MODE, 1, 128)
                except:
                    pass

            self._log(f"Opened {cfg_path} @ device {device_index}")
            return True
        except Exception as e:
            self._log(f"Error opening camera: {e}")
            self.camera = None
            return False

    def close(self) -> None:
        """Close camera and clean up."""
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=2)
            self._thread = None
        if self.camera:
            try:
                self.camera.close()
            except:
                pass
            self.camera = None
        self._log("Camera closed")

    def start(self) -> bool:
        """Start streaming."""
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
        """Stop streaming."""
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=2)
            self._thread = None
        if self.camera:
            try:
                self.camera.stop()
            except:
                pass
        self._log("Streaming stopped")

    def _capture_loop(self) -> None:
        """Capture loop (runs in worker thread)."""
        last_tick = time.time()
        frame_count = 0

        while not self._stop.is_set():
            try:
                frame = self.camera.capture(1000)
                if frame is None:
                    with self.lock:
                        self.stats.none_frames += 1
                    continue

                with self.lock:
                    self.stats.attempts += 1
                    self.stats.frames += 1

                # Convert image
                try:
                    display_img = from_image(frame)
                    if display_img is not None:
                        self.frame_queue.put(display_img, block=False)
                except:
                    with self.lock:
                        self.stats.bad_convert += 1

                # Update FPS
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
        """Read a single sensor register."""
        try:
            if not self.camera:
                return None
            return self.camera.read_register(addr)
        except:
            return None

    def write_register(self, addr: int, value: int) -> bool:
        """Write a single sensor register."""
        try:
            if not self.camera:
                return False
            self.camera.write_register(addr, value)
            return True
        except:
            return False


# ============================================================================
# Main GUI Application
# ============================================================================

class CameraGUI:
    """Main Tkinter application."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Arducam EVK Camera")
        self.root.geometry("1280x720")

        # Configuration manager
        self.config_manager = ConfigManager(Path(__file__).parent)
        self.config_manager.discover_configurations()
        self.config_manager.discover_sensor_profiles()

        # Worker
        self.worker = CameraWorker(self._log)

        # State
        self.current_config: Optional[Path] = None
        self.camera_open = False
        self.streaming = False
        self.sensor_profile = None
        self.log_queue: queue.Queue = queue.Queue()
        self._photo_ref = None

        # Build UI
        self._build_ui()

        # Periodic updates
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
    """Launch the GUI application."""
    root = tk.Tk()

    try:
        # Set window icon (if available)
        root.iconbitmap(default="")
    except tk.TclError:
        pass

    app = CameraGUI(root)
    root.protocol("WM_DELETE_WINDOW", app._on_close_window)
    root.mainloop()


if __name__ == "__main__":
    main()
