"""
Arducam EVK Camera GUI
======================

Unified Tkinter GUI that merges every standalone example under
`python/function` and `python/project` into one tool. It lets you:

* List USB devices and open one by index.
* Load a `.cfg` / `.bin` configuration file (auto-discovered from the
  repository root or `python/`).
* Start / stop a live preview with FPS and bandwidth read-out.
* Drive every control declared by the cfg (e.g. `Framerate`,
  `Exp(us)`) via auto-generated sliders.
* Read / write individual sensor registers, save snapshots and dump
  whole register ranges in a `.cfg`-compatible format. Per-sensor
  register maps are loaded from JSON profiles in ``python/sensors/``.
* Capture single frames or sequences to disk as PNG.
* Pipe SDK log messages into an in-app console.

The GUI keeps the camera thread off the Tk main loop. Capture runs in
a worker thread; UI updates happen via `Tk.after`.

Run from the repository:

    cd python
    .venv\\Scripts\\python.exe gui_camera.py
"""

from __future__ import annotations

import os
import queue
import sys
import collections
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:  # pragma: no cover
    print("Tkinter is required. On Windows it ships with the standard installer.")
    sys.exit(1)

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover
    print("Pillow is required. Install with:  pip install Pillow")
    sys.exit(1)

import ArducamEvkSDK
from ArducamEvkSDK import (
    Camera,
    DeviceList,
    LoggerLevel,
    MemType,
    Param,
    get_error_name,
)

# Local helper for color conversion; lives next to the project examples.
sys.path.insert(0, str(Path(__file__).parent / "project"))
from img_cvt_utils import convert_image, from_image  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _discover_default_cfg() -> Path:
    """Pick the first ``*.cfg`` found in the repo root (then ``python/``).

    Keeps the GUI sensor-agnostic: any cfg shipped alongside the project
    becomes the default. Returns a path that may not exist if no cfg is
    bundled, in which case the user must browse manually.
    """
    for folder in (REPO_ROOT, Path(__file__).parent):
        try:
            cfgs = sorted(folder.glob("*.cfg"))
        except Exception:
            cfgs = []
        if cfgs:
            return cfgs[0]
    return REPO_ROOT / "camera.cfg"


DEFAULT_CFG = _discover_default_cfg()
FALLBACK_CFG = DEFAULT_CFG


# ---------------------------------------------------------------------------
# Sensor register profiles (loaded from python/sensors/*.json)
# ---------------------------------------------------------------------------
import json as _json


def _coerce_addr(v) -> int:
    """Accept ints or strings like '0x100C' / '4108' for ``addr``."""
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        return int(s, 16) if s.lower().startswith("0x") else int(s, 0)
    raise ValueError(f"bad addr: {v!r}")


def _normalize_reg(r: dict) -> dict:
    """Sanity-check + normalise a JSON-loaded register entry.

    See the schema documentation in ``python/sensors/README.md`` for
    the full list of supported fields. The few semantic conversions
    happen here:

    * Addresses (``addr``, ``reads[*].addr``, ``parts[*].addr``) accept
      either ``int`` or hex/dec ``str`` and are coerced to ``int`` so
      the SDK CCI helpers can be called directly.
    * ``kind='computed'`` aggregates several reads into one read-only
      value; ``addr`` is set from the first sub-read for display.
    * ``kind='multi'`` describes an editable multi-byte field whose
      bits may be split arbitrarily across registers (e.g. VSTART1
      11-bit = 0x107D[7:0] | 0x107E[2:0]). Each ``part`` carries its
      own ``reg_lsb`` (default 0) and ``width``.
    * Conversion expressions (``convert``/``compose``/``expr``) stay
      as strings; they are evaluated later in :func:`App._eval_convert`
      with a restricted namespace.
    """
    out = dict(r)
    if out.get("kind") == "computed":
        reads = []
        for sub in out.get("reads", []):
            s = dict(sub)
            s["addr"] = _coerce_addr(s.get("addr", 0))
            reads.append(s)
        out["reads"] = reads
        out["addr"] = reads[0]["addr"] if reads else 0
    elif out.get("kind") == "multi":
        parts = []
        total_width = 0
        for sub in out.get("parts", []):
            s = dict(sub)
            s["addr"] = _coerce_addr(s.get("addr", 0))
            s.setdefault("reg_lsb", 0)
            s.setdefault("value_lsb", total_width)
            s.setdefault("width", 8)
            total_width = max(
                total_width,
                int(s["value_lsb"]) + int(s["width"]),
            )
            parts.append(s)
        out["parts"] = parts
        out["addr"] = parts[0]["addr"] if parts else 0
        # Sensible default range for the combined value.
        if "hi" not in out:
            out["hi"] = (1 << total_width) - 1
        if "lo" not in out:
            out["lo"] = 0
    else:
        out["addr"] = _coerce_addr(out.get("addr", 0))
    out.setdefault("group", "Misc")
    out.setdefault("kind", "u8")
    out.setdefault("name", f"reg_0x{out['addr']:04X}")
    out.setdefault("desc", "")
    return out


def _discover_sensor_profiles() -> dict[str, dict]:
    """Return ``{label: {description, guide, tabs, match, registers}}``.

    Profiles are loaded from ``python/sensors/*.json`` (skipping files
    starting with ``_`` or ``.``). The returned dict is empty if no JSON
    profile is present.
    """
    profiles: dict[str, dict] = {}
    sensors_dir = Path(__file__).parent / "sensors"
    if sensors_dir.is_dir():
        for path in sorted(sensors_dir.glob("*.json")):
            if path.name.startswith(("_", ".")):
                continue
            try:
                data = _json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[gui] failed to load {path.name}: {exc}")
                continue
            if not isinstance(data, dict):
                continue
            label = str(data.get("name") or path.stem)
            regs_raw = data.get("registers") or []
            try:
                regs = [_normalize_reg(r) for r in regs_raw]
            except Exception as exc:
                print(f"[gui] {path.name}: invalid register entry: {exc}")
                continue
            tabs = data.get("tabs") if isinstance(data.get("tabs"), dict) else {}
            match = data.get("match")
            if isinstance(match, str):
                match = [match]
            elif not isinstance(match, list):
                match = []
            profiles[label] = {
                "description": str(data.get("description", "")),
                "guide": str(data.get("guide", "")),
                "tabs": {str(k): str(v) for k, v in tabs.items()},
                "match": [str(m) for m in match],
                "registers": regs,
            }
    return profiles


# ---------------------------------------------------------------------------
# Camera worker
# ---------------------------------------------------------------------------


@dataclass
class Stats:
    fps: float = 0.0           # FPS reported by the SDK (capture_fps)
    fps_measured: float = 0.0  # FPS measured at the python side
    bw_mb: float = 0.0
    seq: int = 0
    width: int = 0
    height: int = 0
    frames: int = 0            # total good frames received since start()
    dropped: int = 0           # frame.bad count
    missed: int = 0            # gaps in frame.seq
    bad_convert: int = 0       # convert_image errors
    attempts: int = 0          # total camera.capture() calls
    none_frames: int = 0       # capture() returned None (timeout)


class CameraWorker:
    """Owns the SDK :class:`Camera` object and the capture thread.

    Responsibilities
    ----------------
    * Enumerate the USB devices via :class:`DeviceList` and keep both
      the list and the picked device alive for the whole camera
      lifetime (releasing the list invalidates ``param.device`` and
      causes ``open()`` to fail with *Unknown device type*).
    * Wrap :meth:`Camera.open` / :meth:`Camera.init` / :meth:`start` /
      :meth:`stop` / :meth:`close` so the GUI thread never blocks on
      USB I/O.
    * Run a daemon capture thread (:meth:`_loop`) that pulls frames,
      converts them to numpy via :func:`convert_image`, optionally
      applies an auto-stretch, and pushes the latest frame onto a
      bounded :class:`queue.Queue` so the Tk preview tick can pick it
      up without falling behind.
    * Maintain a :class:`Stats` snapshot updated under ``self.lock`` so
      the GUI can render fps/bandwidth/dropped-frame counts safely.

    Threading model
    ---------------
    All public methods are designed to be called from the Tk main
    thread. The capture thread only writes to ``frame_queue``,
    ``last_raw`` / ``raw_history`` and ``self.stats`` (the latter under
    ``self.lock``). The GUI never mutates SDK objects from the worker
    thread.
    """

    def __init__(self, log_cb):
        self.log_cb = log_cb
        self.camera: Optional[Camera] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.frame_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=2)
        self.last_raw: Optional[np.ndarray] = None  # last raw (pre-stretch) frame
        # Ring buffer of recent raw frames for averaged histogram analysis
        self.raw_history: collections.deque = collections.deque(maxlen=16)
        self.stats = Stats()
        self.lock = threading.Lock()
        self._streaming = False
        self.auto_stretch = False  # if True, preview is contrast-stretched
        # Keep DeviceList alive: its destructor frees the underlying device
        # handles, so we must hold the same instance used to acquire them.
        self._device_list: Optional[DeviceList] = None
        self._devices: list = []

    # ---- helpers ----------------------------------------------------------
    def _log(self, msg: str) -> None:
        try:
            self.log_cb(msg)
        except Exception:
            pass

    @property
    def is_open(self) -> bool:
        return self.camera is not None and self.camera.is_opened

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    # ---- lifecycle --------------------------------------------------------
    def open(self, cfg_path: str, device_index: int = 0, dma: bool = False) -> None:
        if self.is_open:
            raise RuntimeError("Camera already open. Close it first.")
        # Keep both DeviceList and the devices tuple alive for the whole
        # camera lifetime — releasing them invalidates param.device.
        self._device_list = DeviceList()
        self._devices = self._device_list.devices()
        if not self._devices:
            raise RuntimeError("No Arducam EVK devices found.")
        if device_index >= len(self._devices):
            raise RuntimeError(
                f"Device index {device_index} out of range "
                f"(found {len(self._devices)})."
            )

        cam = Camera()
        param = Param()
        param.config_file_name = cfg_path
        param.bin_config = cfg_path.lower().endswith(".bin")
        param.device = self._devices[device_index]
        if dma:
            param.mem_type = MemType.DMA

        cam.set_message_callback(lambda level, msg: self._log(msg))
        cam.log_level = LoggerLevel.Info

        if not cam.open(param):
            raise RuntimeError(
                f"open camera error: {get_error_name(cam.last_error)}"
            )
        if not cam.init():
            err = get_error_name(cam.last_error)
            cam.close()
            raise RuntimeError(f"init camera error: {err}")
        self.camera = cam
        cfg = cam.config
        self.stats.width = cfg.width
        self.stats.height = cfg.height
        self._log(
            f"Opened {cam.usb_type} device, resolution {cfg.width}x{cfg.height}"
        )

    def close(self) -> None:
        self.stop()
        if self.camera is not None:
            try:
                self.camera.close()
            except Exception as exc:  # pragma: no cover
                self._log(f"close error: {exc}")
            self.camera = None
            self._log("Camera closed.")

    # ---- streaming --------------------------------------------------------
    def start(self) -> None:
        if not self.is_open:
            raise RuntimeError("Open the camera first.")
        if self._streaming:
            return
        # Reset counters every time the user starts streaming.
        with self.lock:
            self.stats.frames = 0
            self.stats.dropped = 0
            self.stats.missed = 0
            self.stats.bad_convert = 0
            self.stats.fps_measured = 0.0
            self.stats.attempts = 0
            self.stats.none_frames = 0
        self.camera.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._streaming = True
        self._log("Streaming started.")

    def stop(self) -> None:
        if not self._streaming:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            if self.camera is not None:
                self.camera.stop()
        except Exception:
            pass
        self._streaming = False
        self._log("Streaming stopped.")

    def _loop(self) -> None:
        last_fps_t = time.monotonic()
        last_frames = 0
        last_seq: Optional[int] = None
        while not self._stop.is_set():
            with self.lock:
                self.stats.attempts += 1
            try:
                frame = self.camera.capture(1000)
            except Exception as exc:
                self._log(f"capture exception: {exc}")
                time.sleep(0.05)
                continue
            if frame is None:
                with self.lock:
                    self.stats.none_frames += 1
                continue
            if getattr(frame, "bad", False):
                with self.lock:
                    self.stats.dropped += 1
                continue
            try:
                img = convert_image(frame.data, frame.format)
            except Exception as exc:
                with self.lock:
                    self.stats.bad_convert += 1
                self._log(f"convert error: {exc}")
                continue
            if img is None:
                with self.lock:
                    self.stats.bad_convert += 1
                continue
            # For mono >8-bit formats, convert_image() already applies a
            # `>> (bitWidth-8)` to fit in uint8, which makes low-amplitude
            # signals (e.g. sensor gradient test patterns that fill only the
            # low 8 bits of a 12-bit frame) look almost black. Re-derive a
            # uint16 view from the raw buffer so the preview can pick the
            # right shift adaptively.
            try:
                fmt = frame.format
                fmt_mode = fmt.format_code >> 8
                if (fmt_mode == ArducamEvkSDK.MON
                        and 8 < fmt.bit_depth <= 16):
                    img = np.frombuffer(frame.data,
                                         dtype=np.uint16).reshape(
                        fmt.height, fmt.width).copy()
            except Exception:
                pass
            # Keep a copy of the un-stretched frame for histogram/analysis.
            raw_for_hist = img.copy()
            # Optional auto-stretch (disabled by default) for better visibility.
            if self.auto_stretch and img.dtype == np.uint8 and img.ndim in (2, 3):
                mn, mx = float(img.min()), float(img.max())
                if mx - mn >= 1.0:
                    img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)
            with self.lock:
                self.stats.frames += 1
                self.stats.seq = frame.seq
                if last_seq is not None and frame.seq > last_seq + 1:
                    self.stats.missed += frame.seq - last_seq - 1
                last_seq = frame.seq
                now = time.monotonic()
                dt = now - last_fps_t
                if dt > 0.4:
                    delta = self.stats.frames - last_frames
                    self.stats.fps_measured = delta / dt if dt > 0 else 0.0
                    last_frames = self.stats.frames
                    try:
                        self.stats.fps = float(self.camera.capture_fps)
                        self.stats.bw_mb = float(self.camera.bandwidth) / 1024.0 / 1024.0
                    except Exception:
                        pass
                    last_fps_t = now
            # keep the queue shallow; drop oldest if backed up
            try:
                self.frame_queue.put_nowait(img)
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.frame_queue.put_nowait(img)
                except queue.Full:
                    pass
            # remember the latest pre-stretch frame for on-demand histogram
            self.last_raw = raw_for_hist
            with self.lock:
                self.raw_history.append(raw_for_hist)

    # ---- ad-hoc operations -----------------------------------------------
    def list_controls(self):
        if not self.is_open:
            return []
        return list(self.camera.controls)

    def set_control(self, name: str, value: int) -> bool:
        if not self.is_open:
            return False
        return bool(self.camera.set_control(name, int(value)))

    def set_transfer(self, count: int, size: int) -> None:
        if not self.is_open:
            return
        self.camera.set_transfer(int(count), int(size))

    def read_sensor_reg(self, addr: int) -> Optional[int]:
        if not self.is_open:
            return None
        return self.camera.read_sensor_reg(int(addr))

    def write_sensor_reg(self, addr: int, val: int) -> bool:
        if not self.is_open:
            return False
        return bool(self.camera.write_sensor_reg(int(addr), int(val)))

    def dump_info(self) -> dict:
        if not self.is_open:
            return {}
        info: dict = {}
        try:
            CPLD = 0x46
            ver = self.camera.read_reg_8_8(CPLD, 0x00)
            year = self.camera.read_reg_8_8(CPLD, 0x05)
            mouth = self.camera.read_reg_8_8(CPLD, 0x06)
            day = self.camera.read_reg_8_8(CPLD, 0x07)
            info["CPLD version"] = f"v{ver >> 4}.{ver & 0x0F}"
            info["CPLD date"] = f"20{year:02d}-{mouth:02d}-{day:02d}"
        except Exception as exc:
            info["CPLD"] = f"read error: {exc}"
        try:
            data = self.camera.read_board_config(0x80, 0x00, 0x00, 2)
            if data:
                info["FW version"] = f"v{data[0] & 0xFF}.{data[1] & 0xFF}"
        except Exception as exc:
            info["FW"] = f"read error: {exc}"
        try:
            cfg = self.camera.config
            info["Resolution"] = f"{cfg.width} x {cfg.height}"
            info["Bit depth"] = cfg.bit_depth
            info["USB type"] = self.camera.usb_type
        except Exception:
            pass
        try:
            sn = "".join(chr(i) for i in self.camera.device.serial_number)
            info["Serial"] = sn
            info["VID:PID"] = (
                f"{self.camera.device.id_vendor:04X}:"
                f"{self.camera.device.id_product:04X}"
            )
        except Exception:
            pass
        return info


# ---------------------------------------------------------------------------
# Main GUI
# ---------------------------------------------------------------------------


class App:
    """Top-level Tk application.

    Layout
    ------
    A vertical stack of:

    * Top bar with the cfg picker, device combobox and
      Open/Close/Start/Stop buttons.
    * A :class:`ttk.Notebook` with the following tabs:

      ``Preview``
          Live preview canvas, snapshot/sequence buttons, histogram
          panel and software AE/AGC controls.
      ``Controls``
          Auto-generated sliders for every SDK control declared by the
          loaded cfg (typically Framerate, Exp(us)).
      ``Sensor regs``
          Friendly per-register editor driven by the active sensor
          JSON profile (``python/sensors/<sensor>.json``). Supports
          scalar (u8/u16/u24), boolean, enum and synthetic
          ``computed`` registers (e.g. die temperature combining
          VPTAT and VREF).
      ``Registers (raw)``
          Generic single-register read/write with bit checkboxes and a
          range dump tool that produces ``REG = addr,val`` lines
          compatible with the cfg format.
      ``Advanced``
          USB transfer tuning, sensor mode switch, SDK log level.
      ``Info / Log``
          Camera info dump and the SDK message console.

    State
    -----
    * ``self.worker`` is the :class:`CameraWorker` doing all the heavy
      lifting; the App only orchestrates UI events.
    * ``self.sensor_profiles`` is the dictionary of sensor profiles
      discovered at start-up. The active profile drives both the
      Sensor-regs tab and the per-tab hint banners (``tabs`` field of
      the JSON profile).
    * ``self._photo_ref`` keeps a reference to the currently displayed
      :class:`PIL.ImageTk.PhotoImage`; without it Tk would garbage
      collect the underlying bitmap and the canvas would go black.
    """

    PREVIEW_MAX_W = 800
    PREVIEW_MAX_H = 600

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Arducam EVK Camera GUI")
        root.geometry("1180x780")

        self.worker = CameraWorker(self._log_threadsafe)
        self.control_widgets: dict[str, ttk.Scale] = {}
        self.control_value_lbls: dict[str, ttk.Label] = {}
        self._photo_ref = None  # keep a reference to ImageTk.PhotoImage

        self._build_top_bar()
        self._build_main_area()
        self._build_status_bar()

        self.refresh_devices()
        self._update_tab_states()
        # Auto-pick a default cfg if it exists
        cfg = DEFAULT_CFG if DEFAULT_CFG.exists() else FALLBACK_CFG
        if cfg.exists():
            self.cfg_var.set(str(cfg))

        # Periodic UI updaters
        self.root.after(33, self._tick_preview)
        self.root.after(500, self._tick_status)
        self.root.after(1000, self._tick_histogram)
        self.root.after(1000, self._tick_ae)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- layout --------------------------------------------------------
    def _build_top_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=6)
        bar.pack(fill=tk.X)

    def _build_top_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=6)
        bar.pack(fill=tk.X)

        ttk.Label(bar, text="Config:").pack(side=tk.LEFT)
        self.cfg_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.cfg_var, width=50).pack(
            side=tk.LEFT, padx=4, fill=tk.X, expand=True
        )
        ttk.Button(bar, text="Browse…", command=self._browse_cfg).pack(side=tk.LEFT)

        # Second row: device + actions
        bar2 = ttk.Frame(self.root, padding=(6, 0, 6, 6))
        bar2.pack(fill=tk.X)

        ttk.Label(bar2, text="Device:").pack(side=tk.LEFT)
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            bar2, textvariable=self.device_var, width=36, state="readonly"
        )
        self.device_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(bar2, text="↻", width=3, command=self.refresh_devices).pack(
            side=tk.LEFT
        )

        self.dma_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="DMA", variable=self.dma_var).pack(
            side=tk.LEFT, padx=8
        )

        self.open_btn = ttk.Button(bar2, text="Open", command=self._on_open)
        self.open_btn.pack(side=tk.LEFT, padx=(8, 2))
        self.close_btn = ttk.Button(
            bar2, text="Close", command=self._on_close_camera, state=tk.DISABLED
        )
        self.close_btn.pack(side=tk.LEFT, padx=2)
        self.start_btn = ttk.Button(
            bar2, text="▶ Start", command=self._on_start, state=tk.DISABLED
        )
        self.start_btn.pack(side=tk.LEFT, padx=(8, 2))
        self.stop_btn = ttk.Button(
            bar2, text="■ Stop", command=self._on_stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=2)

    def _build_main_area(self) -> None:
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.notebook = nb
        # Optional one-line hint text shown at the top of selected tabs;
        # populated from the sensor profile's "tabs" dict.
        self._tab_hint_vars: dict[str, tk.StringVar] = {}
        self._tab_hint_widgets: dict[str, ttk.Label] = {}

        # ---- Preview tab --------------------------------------------------
        preview = ttk.Frame(nb)
        nb.add(preview, text="Preview")
        self._make_tab_hint(preview, "preview")

        self.canvas = tk.Canvas(
            preview, bg="#202020", width=self.PREVIEW_MAX_W, height=self.PREVIEW_MAX_H
        )
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)

        side = ttk.Frame(preview, padding=8)
        side.pack(side=tk.RIGHT, fill=tk.Y)
        self.live_info = tk.StringVar(value="—")
        ttk.Label(side, textvariable=self.live_info, font=("Consolas", 10)).pack(
            anchor=tk.W
        )
        ttk.Separator(side).pack(fill=tk.X, pady=6)

        # Save row: snapshot + N + sequence inline
        srow = ttk.Frame(side); srow.pack(anchor=tk.W, fill=tk.X)
        ttk.Button(srow, text="Snap", width=6,
                   command=self._save_snapshot).pack(side=tk.LEFT)
        ttk.Label(srow, text=" N=").pack(side=tk.LEFT)
        self.batch_n = tk.IntVar(value=10)
        ttk.Spinbox(srow, from_=1, to=100000, textvariable=self.batch_n,
                    width=6).pack(side=tk.LEFT)
        ttk.Button(srow, text="Seq…", width=6,
                   command=self._save_sequence).pack(side=tk.LEFT, padx=(4, 0))

        self.auto_stretch_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            side, text="Auto-stretch preview",
            variable=self.auto_stretch_var,
            command=lambda: setattr(self.worker, "auto_stretch",
                                    self.auto_stretch_var.get()),
        ).pack(anchor=tk.W, pady=(4, 0))

        ttk.Separator(side).pack(fill=tk.X, pady=6)
        ttk.Label(side, text="Histogram:",
                  font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        self.hist_canvas = tk.Canvas(side, width=300, height=140,
                                     bg="#101010", highlightthickness=1,
                                     highlightbackground="#444")
        self.hist_canvas.pack(anchor=tk.W, pady=2)
        self.hist_info = tk.StringVar(value="(no frame)")
        ttk.Label(side, textvariable=self.hist_info,
                  font=("Consolas", 9), justify=tk.LEFT).pack(anchor=tk.W)
        hrow = ttk.Frame(side); hrow.pack(anchor=tk.W, fill=tk.X, pady=2)
        self.hist_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(hrow, text="Auto 1 s",
                        variable=self.hist_auto).pack(side=tk.LEFT)
        ttk.Button(hrow, text="Refresh", width=8,
                   command=self._compute_histogram).pack(side=tk.LEFT, padx=4)

        # ---- Software auto-exposure / auto-gain ---------------------------
        ttk.Separator(side).pack(fill=tk.X, pady=6)
        ttk.Label(side, text="Software AE / AGC:",
                  font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        self.ae_enable = tk.BooleanVar(value=False)
        self.ae_use_gain = tk.BooleanVar(value=False)
        aerow = ttk.Frame(side); aerow.pack(anchor=tk.W, fill=tk.X)
        ttk.Checkbutton(aerow, text="On",
                        variable=self.ae_enable).pack(side=tk.LEFT)
        ttk.Checkbutton(aerow, text="+gain",
                        variable=self.ae_use_gain).pack(side=tk.LEFT, padx=(8, 0))
        trow = ttk.Frame(side); trow.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(trow, text="Target%").pack(side=tk.LEFT)
        self.ae_target_pct = tk.DoubleVar(value=45.0)
        ttk.Spinbox(trow, from_=5, to=90, increment=1, width=5,
                    textvariable=self.ae_target_pct).pack(side=tk.LEFT, padx=2)
        ttk.Label(trow, text="Tol%").pack(side=tk.LEFT, padx=(8, 0))
        self.ae_tol_pct = tk.DoubleVar(value=3.0)
        ttk.Spinbox(trow, from_=1, to=20, increment=1, width=4,
                    textvariable=self.ae_tol_pct).pack(side=tk.LEFT, padx=2)
        self.ae_status = tk.StringVar(value="(disabled)")
        ttk.Label(side, textvariable=self.ae_status,
                  font=("Consolas", 9), foreground="#4ec9b0",
                  wraplength=280).pack(anchor=tk.W)

        # ---- Controls tab -------------------------------------------------
        self.controls_tab = ttk.Frame(nb)
        nb.add(self.controls_tab, text="Controls")
        self._make_tab_hint(self.controls_tab, "controls")
        self.controls_body = ttk.Frame(self.controls_tab)
        self.controls_body.pack(fill=tk.BOTH, expand=True)
        self._build_controls_placeholder()

        # ---- Friendly per-sensor register editor (driven by JSON profile)
        sensor_tab = ttk.Frame(nb, padding=8)
        nb.add(sensor_tab, text="Sensor regs")
        self._build_sensor_tab(sensor_tab)

        # ---- Registers tab ------------------------------------------------
        regs = ttk.Frame(nb, padding=8)
        nb.add(regs, text="Registers (raw)")
        self._build_regs_tab(regs)

        # Tabs that require an open camera; tracked so we can disable them.
        self._camera_only_tabs = [self.controls_tab, sensor_tab, regs]
        self._sensor_tab_widget = sensor_tab
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ---- Transfer / Open tab -----------------------------------------
        adv = ttk.Frame(nb, padding=8)
        nb.add(adv, text="Advanced")
        self._build_adv_tab(adv)

        # ---- Info tab ------------------------------------------------------
        info = ttk.Frame(nb, padding=8)
        nb.add(info, text="Info / Log")
        self._build_info_tab(info)

        # Now that every tab has registered its hint label, populate them
        # from the active sensor profile.
        self._apply_profile_hints()
        # Populate the picker in the Registers (raw) tab and resolve the
        # default address description.
        if hasattr(self, "regs_pick_combo"):
            self._regs_refresh_picker()
            self._regs_resolve_addr()

    def _update_tab_states(self) -> None:
        """Enable Controls/Sensor regs/Registers tabs only when the camera is open."""
        state = tk.NORMAL if self.worker.is_open else tk.DISABLED
        for tab in getattr(self, "_camera_only_tabs", []):
            try:
                self.notebook.tab(tab, state=state)
            except tk.TclError:
                pass

    def _on_tab_changed(self, _evt=None) -> None:
        """Auto-refresh sensor registers when entering the Sensor regs tab."""
        try:
            current = self.notebook.select()
            if not current:
                return
            sensor_tab_id = str(self._sensor_tab_widget)
            if current == sensor_tab_id and self.worker.is_open:
                self._sensor_read_all()
        except Exception as exc:
            self._log(f"tab change error: {exc}")

    # ------------------------------------------------------------------
    # Profile-driven tab hints + sensor guide window
    # ------------------------------------------------------------------
    def _make_tab_hint(self, parent: ttk.Frame, key: str) -> None:
        """Create an empty hint label at the top of *parent* for *key*."""
        var = tk.StringVar(value="")
        lbl = ttk.Label(
            parent, textvariable=var, foreground="#666",
            wraplength=1100, justify="left",
            padding=(8, 4, 8, 4),
        )
        # Pack now so subsequent widgets sit below it; visibility is then
        # toggled in _apply_profile_hints based on the text content.
        lbl.pack(side=tk.TOP, fill=tk.X)
        self._tab_hint_vars[key] = var
        self._tab_hint_widgets[key] = lbl
        self._tab_hint_parents = getattr(self, "_tab_hint_parents", {})
        self._tab_hint_parents[key] = parent

    def _apply_profile_hints(self) -> None:
        """Push hint text from the current profile into each tab's label."""
        prof = self.sensor_profiles.get(self.sensor_profile_name, {}) or {}
        tabs = prof.get("tabs", {}) if isinstance(prof.get("tabs"), dict) else {}
        for key, var in self._tab_hint_vars.items():
            txt = tabs.get(key, "").strip()
            var.set(txt)
            lbl = self._tab_hint_widgets.get(key)
            if lbl is None:
                continue
            if txt:
                if not lbl.winfo_ismapped():
                    # Re-pack at the top of its parent.
                    lbl.pack(side=tk.TOP, fill=tk.X,
                             before=lbl.master.winfo_children()[0])
            else:
                lbl.pack_forget()
        # Update Guide button state
        if hasattr(self, "sensor_guide_btn"):
            has_guide = bool(prof.get("guide", "").strip())
            self.sensor_guide_btn.configure(
                state=tk.NORMAL if has_guide else tk.DISABLED
            )

    def _show_sensor_guide(self) -> None:
        """Open a window showing the long-form guide of the active profile."""
        prof = self.sensor_profiles.get(self.sensor_profile_name, {}) or {}
        text = (prof.get("guide") or "").strip()
        if not text:
            messagebox.showinfo(
                "Sensor guide",
                f"No guide provided for profile '{self.sensor_profile_name}'.",
            )
            return
        win = tk.Toplevel(self.root)
        win.title(f"Guide — {self.sensor_profile_name}")
        win.geometry("720x520")
        body = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10),
                       padx=10, pady=8)
        body.pack(fill=tk.BOTH, expand=True)
        body.insert(tk.END, text)
        body.configure(state=tk.DISABLED)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=6)

    def _build_controls_placeholder(self) -> None:
        for w in self.controls_body.winfo_children():
            w.destroy()
        ttk.Label(
            self.controls_body,
            text="Open the camera to discover its controls.",
            padding=20,
        ).pack()

    def _populate_controls(self) -> None:
        for w in self.controls_body.winfo_children():
            w.destroy()
        self.control_widgets.clear()
        self.control_value_lbls.clear()
        self._ctrl_meta: dict[str, dict] = {}

        ctrls = self.worker.list_controls()
        if not ctrls:
            ttk.Label(self.controls_body, text="No controls exposed by this cfg.").pack(
                padx=20, pady=20
            )
            return

        header = ttk.Frame(self.controls_body, padding=6)
        header.pack(fill=tk.X)
        ttk.Label(
            header,
            text=(
                "Sliders write to the SDK control by name. The Exp(us) upper "
                "bound follows the current Framerate (max exp ≈ 1 000 000 / fps)."
            ),
            foreground="#555",
        ).pack(anchor=tk.W)

        body = ttk.Frame(self.controls_body, padding=6)
        body.pack(fill=tk.BOTH, expand=True)

        for ct in ctrls:
            row = ttk.Frame(body, padding=(0, 4))
            row.pack(fill=tk.X)
            ttk.Label(row, text=f"{ct.name}", width=20, anchor="w").pack(side=tk.LEFT)
            range_lbl = ttk.Label(
                row,
                text=f"[{ct.min}..{ct.max} step {ct.step}]",
                foreground="#666",
                width=26,
                anchor="w",
            )
            range_lbl.pack(side=tk.LEFT)
            current = tk.IntVar(value=ct.default)
            scale = ttk.Scale(
                row,
                from_=ct.min,
                to=ct.max,
                orient=tk.HORIZONTAL,
                length=420,
            )
            scale.set(ct.default)
            scale.pack(side=tk.LEFT, padx=6)
            value_lbl = ttk.Label(row, text=str(ct.default), width=8)
            value_lbl.pack(side=tk.LEFT)
            apply_btn = ttk.Button(
                row,
                text="Apply",
                width=8,
                command=lambda n=ct.name, f=getattr(ct, "func", "") or ct.name,
                                s=scale: self._apply_control(
                    n, int(round(s.get())), f
                ),
            )
            apply_btn.pack(side=tk.LEFT)

            def _on_move(_e=None, s=scale, lbl=value_lbl, var=current):
                v = int(round(s.get()))
                var.set(v)
                lbl.configure(text=str(v))

            scale.configure(command=lambda *_a, h=_on_move: h())
            self.control_widgets[ct.name] = scale
            self.control_value_lbls[ct.name] = value_lbl
            self._ctrl_meta[ct.name] = dict(
                min=ct.min, max=ct.max, step=ct.step, default=ct.default,
                range_lbl=range_lbl,
                func=getattr(ct, "func", "") or ct.name,
            )

        # Initial coupling of Exp(us) max to the default Framerate.
        self._sync_exp_to_fps()

    # ------------------------------------------------------------------
    # Sensor friendly tab
    # ------------------------------------------------------------------
    def _build_sensor_tab(self, parent: ttk.Frame) -> None:
        self.sensor_widgets: dict[str, dict] = {}
        self.sensor_rows: list[tuple[str, ttk.Frame]] = []
        self.sensor_profiles = _discover_sensor_profiles()
        if self.sensor_profiles:
            default_profile = next(iter(self.sensor_profiles))
        else:
            default_profile = "(none)"
            # Insert a placeholder so the rest of the code has something to
            # reference; it has zero registers and empty hints/guide.
            self.sensor_profiles[default_profile] = {
                "description": "No sensor profile JSON found in python/sensors/.",
                "guide": "",
                "tabs": {},
                "match": [],
                "registers": [],
            }
        self.sensor_profile_name = default_profile
        self.sensor_regs = list(
            self.sensor_profiles[default_profile]["registers"]
        )

        intro = ttk.Label(
            parent,
            text=(
                "Friendly view of sensor registers. Values are shown in human "
                "units; the GUI handles addresses and byte-ordering. Drop a "
                "JSON file in 'python/sensors/' to add a new sensor profile "
                "(see sensors/README.md for the format)."
            ),
            foreground="#555",
            wraplength=1000,
            justify="left",
        )
        intro.pack(fill=tk.X, pady=(0, 6))

        # ----- Top toolbar: sensor selector + actions ---------------------
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="Sensor:").pack(side=tk.LEFT, padx=(0, 4))
        self.sensor_profile_var = tk.StringVar(value=default_profile)
        self.sensor_profile_combo = ttk.Combobox(
            toolbar, textvariable=self.sensor_profile_var,
            values=list(self.sensor_profiles.keys()),
            width=18, state="readonly",
        )
        self.sensor_profile_combo.pack(side=tk.LEFT)
        self.sensor_profile_combo.bind(
            "<<ComboboxSelected>>",
            lambda *_: self._sensor_set_profile(self.sensor_profile_var.get()),
        )
        ttk.Button(
            toolbar, text="↻", width=3,
            command=self._sensor_reload_profiles,
        ).pack(side=tk.LEFT, padx=(2, 8))

        self.sensor_guide_btn = ttk.Button(
            toolbar, text="Guide", command=self._show_sensor_guide,
        )
        self.sensor_guide_btn.pack(side=tk.LEFT, padx=2)

        ttk.Button(
            toolbar, text="Read all", command=self._sensor_read_all
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            toolbar, text="Write all changes", command=self._sensor_write_all
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            toolbar, text="Reload from cfg",
            command=self._sensor_reload_cfg_defaults,
        ).pack(side=tk.LEFT, padx=2)

        # View-mode toggle: when checked (default) the GUI shows the
        # combined ``multi`` rows and hides the individual LSB/MSB byte
        # rows that contribute to them; when unchecked the byte rows
        # become visible and the combined rows are hidden, mirroring
        # the raw datasheet layout.
        self.sensor_show_combined = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            toolbar, text="Combined multi-byte",
            variable=self.sensor_show_combined,
            command=self._apply_sensor_filter,
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(toolbar, text="  Filter:").pack(side=tk.LEFT, padx=(12, 2))
        self.sensor_filter = tk.StringVar()
        ent = ttk.Entry(toolbar, textvariable=self.sensor_filter, width=24)
        ent.pack(side=tk.LEFT)
        self.sensor_filter.trace_add("write", lambda *_: self._apply_sensor_filter())
        ttk.Label(toolbar, text="  Group:").pack(side=tk.LEFT, padx=(12, 2))
        self.sensor_group_var = tk.StringVar(value="(all)")
        self.sensor_group_combo = ttk.Combobox(
            toolbar, textvariable=self.sensor_group_var, values=["(all)"],
            width=20, state="readonly",
        )
        self.sensor_group_combo.pack(side=tk.LEFT)
        self.sensor_group_var.trace_add("write", lambda *_: self._apply_sensor_filter())
        self.sensor_status = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.sensor_status, foreground="#0a0").pack(
            side=tk.LEFT, padx=12
        )

        # Scrollable canvas containing all rows
        wrap = ttk.Frame(parent)
        wrap.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.sensor_canvas = tk.Canvas(wrap, highlightthickness=0)
        vbar = ttk.Scrollbar(wrap, orient=tk.VERTICAL,
                             command=self.sensor_canvas.yview)
        self.sensor_canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.sensor_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.sensor_inner = ttk.Frame(self.sensor_canvas)
        self.sensor_canvas.create_window((0, 0), window=self.sensor_inner,
                                       anchor="nw")
        self.sensor_inner.bind(
            "<Configure>",
            lambda e: self.sensor_canvas.configure(
                scrollregion=self.sensor_canvas.bbox("all")
            ),
        )
        # Mouse-wheel scrolling
        self.sensor_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.sensor_canvas.yview_scroll(
                int(-1 * (e.delta / 120)), "units"
            ),
        )

        self._sensor_build_rows()
        # Apply the initial visibility rules (Combined multi-byte = on).
        self._apply_sensor_filter()

    def _sensor_build_rows(self) -> None:
        """(Re)create the per-register rows from ``self.sensor_regs``."""
        # Clear previous rows
        for _name, w in getattr(self, "sensor_rows", []):
            try:
                w.destroy()
            except Exception:
                pass
        self.sensor_rows = []
        self.sensor_widgets = {}

        cur_group = None
        for entry in self.sensor_regs:
            if entry["group"] != cur_group:
                cur_group = entry["group"]
                hdr = ttk.Label(
                    self.sensor_inner, text=cur_group,
                    font=("Segoe UI", 10, "bold"),
                    foreground="#fff", background="#306",
                    padding=(8, 3),
                )
                hdr.pack(fill=tk.X, pady=(8, 2))
                self.sensor_rows.append(("__header__", hdr))
            row = self._build_sensor_row(self.sensor_inner, entry)
            self.sensor_rows.append((entry["name"], row))

        # Refresh group combobox and status
        groups = ["(all)"] + sorted({r["group"] for r in self.sensor_regs})
        if hasattr(self, "sensor_group_combo"):
            self.sensor_group_combo.configure(values=groups)
            if self.sensor_group_var.get() not in groups:
                self.sensor_group_var.set("(all)")
        if hasattr(self, "sensor_status"):
            self.sensor_status.set(
                f"{self.sensor_profile_name}: {len(self.sensor_regs)} registers"
            )

    def _sensor_set_profile(self, name: str) -> None:
        """Switch to a different sensor profile and rebuild rows."""
        if name not in self.sensor_profiles:
            return
        self.sensor_profile_name = name
        self.sensor_regs = list(self.sensor_profiles[name]["registers"])
        self._sensor_build_rows()
        self._apply_sensor_filter()
        self._apply_profile_hints()
        if hasattr(self, "regs_pick_combo"):
            self._regs_refresh_picker()
            self._regs_resolve_addr()
        if self.worker.is_open:
            try:
                self._sensor_read_all()
            except Exception as exc:
                self._log(f"sensor read after profile switch: {exc}")

    def _sensor_reload_profiles(self) -> None:
        """Re-scan ``sensors/`` directory for new/edited JSON profiles."""
        self.sensor_profiles = _discover_sensor_profiles()
        names = list(self.sensor_profiles.keys())
        self.sensor_profile_combo.configure(values=names)
        if self.sensor_profile_name not in self.sensor_profiles:
            self.sensor_profile_name = names[0]
            self.sensor_profile_var.set(self.sensor_profile_name)
        self.sensor_regs = list(
            self.sensor_profiles[self.sensor_profile_name]["registers"]
        )
        self._sensor_build_rows()
        self._apply_sensor_filter()
        self._apply_profile_hints()
        if hasattr(self, "regs_pick_combo"):
            self._regs_refresh_picker()
            self._regs_resolve_addr()
        self._log(f"sensor profiles reloaded ({len(names)} found)")

    def _build_sensor_row(self, parent, entry: dict) -> ttk.Frame:
        name = entry["name"]
        kind = entry["kind"]
        row = ttk.Frame(parent, padding=(2, 1))
        row.pack(fill=tk.X)

        # column 0: name + addr
        addr = entry["addr"]
        title = f"{name}"
        ttk.Label(row, text=title, width=30, anchor="w").pack(side=tk.LEFT)
        ttk.Label(
            row, text=f"0x{addr:04X}", width=8, anchor="w", foreground="#888",
        ).pack(side=tk.LEFT)

        widget_data: dict = {"entry": entry, "frame": row}

        if kind == "ro":
            ttk.Label(row, text="(read-only)", foreground="#888", width=14).pack(
                side=tk.LEFT, padx=2
            )
            widget_data["var"] = None
        elif kind == "computed":
            # Synthetic register that aggregates several physical reads
            # (e.g. die temperature combining VPTAT + VREF). It is
            # always read-only; the value is shown in cur_lbl plus an
            # optional decoded form (degrees C, volts, ...).
            ttk.Label(row, text="(computed)", foreground="#888", width=14).pack(
                side=tk.LEFT, padx=2
            )
            widget_data["var"] = None
        elif kind == "multi":
            # Editable multi-byte field with arbitrary bit splits.
            # Behaves like a wide u8/u16 spinbox; on write the helper
            # performs read-modify-write on each contributing byte.
            lo = int(entry.get("lo", 0))
            hi = int(entry.get("hi", 0xFFFF))
            var = tk.IntVar(value=lo)
            sb = ttk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=10)
            sb.pack(side=tk.LEFT, padx=2)
            ttk.Label(row, text=f"[{lo}..{hi}]", foreground="#666",
                      width=18, anchor="w").pack(side=tk.LEFT)
            widget_data["var"] = var
        elif kind in ("u8", "u16be", "u16le", "u24be"):
            default_hi = {"u8": 0xFF, "u16be": 0xFFFF, "u16le": 0xFFFF,
                          "u24be": 0xFFFFFF}[kind]
            lo = int(entry.get("lo", 0))
            hi = int(entry.get("hi", default_hi))
            var = tk.IntVar(value=lo)
            sb = ttk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=10)
            sb.pack(side=tk.LEFT, padx=2)
            ttk.Label(row, text=f"[{lo}..{hi}]", foreground="#666",
                      width=18, anchor="w").pack(side=tk.LEFT)
            widget_data["var"] = var
        elif kind == "bool":
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(row, variable=var, text="enabled").pack(
                side=tk.LEFT, padx=2
            )
            ttk.Label(row, text="bit 0", foreground="#666", width=18,
                      anchor="w").pack(side=tk.LEFT)
            widget_data["var"] = var
        elif kind == "enum":
            choices = list(entry["choices"].keys())
            var = tk.StringVar(value=choices[0] if choices else "")
            ttk.Combobox(
                row, textvariable=var, values=choices, state="readonly",
                width=22,
            ).pack(side=tk.LEFT, padx=2)
            ttk.Label(row, text="enum", foreground="#666", width=8,
                      anchor="w").pack(side=tk.LEFT)
            widget_data["var"] = var
        else:
            ttk.Label(row, text=f"(unsupported kind={kind})",
                      foreground="#a00").pack(side=tk.LEFT)
            widget_data["var"] = None

        # Wider current-value label: needed to display both the raw
        # register value and the decoded one (e.g. "cur=0x07A (16.4 °C)").
        cur_lbl = ttk.Label(row, text="cur=?", foreground="#888", width=42,
                            anchor="w")
        cur_lbl.pack(side=tk.LEFT, padx=8)
        widget_data["cur_lbl"] = cur_lbl

        ttk.Button(row, text="Read", width=6,
                   command=lambda n=name: self._sensor_read_one(n)
                   ).pack(side=tk.LEFT, padx=1)
        if kind != "ro" and kind != "computed":
            ttk.Button(row, text="Write", width=6,
                       command=lambda n=name: self._sensor_write_one(n)
                       ).pack(side=tk.LEFT, padx=1)
        ttk.Button(row, text="i", width=2,
                   command=lambda e=entry: self._show_register_help(e)
                   ).pack(side=tk.LEFT, padx=1)

        ttk.Label(row, text=entry.get("desc", ""),
                  foreground="#888").pack(side=tk.LEFT, padx=8)

        self.sensor_widgets[name] = widget_data
        return row

    def _show_register_help(self, entry: dict) -> None:
        """Open a detailed register documentation dialog.

        Renders, in this order, every piece of structured information
        present in the JSON profile:

        * Header line with name, primary address, kind and group.
        * Short ``desc`` (single-line summary).
        * Properties block: range (``lo``/``hi``), default/reset value,
          unit, datasheet reference (``datasheet`` / ``ref``).
        * For ``kind=='enum'``: every choice, sorted by raw value, in
          decimal, hex and binary.
        * For ``kind=='computed'``: the list of sub-reads (address +
          mask + name), the ``compose`` expression and the optional
          ``convert`` formula / ``unit`` / ``format`` template.
        * Bit-field decomposition (``bits[]``) printed as a fixed-pitch
          table including LSB, MSB, width and per-field description.
        * Long-form ``help`` text verbatim.
        * Closing notes: original JSON path, key sequence to copy the
          value as a cfg-style line.
        """
        win = tk.Toplevel(self.root)
        win.title(f"{entry['name']}  -  0x{entry['addr']:04X}")
        win.geometry("720x560")

        # Header strip
        head_frame = ttk.Frame(win, padding=(10, 8))
        head_frame.pack(fill=tk.X)
        ttk.Label(
            head_frame,
            text=entry["name"],
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")
        sub = (
            f"address 0x{entry['addr']:04X}"
            f"   |   kind = {entry['kind']}"
            f"   |   group = {entry.get('group', '?')}"
        )
        ttk.Label(head_frame, text=sub, foreground="#555").pack(anchor="w")

        # Body: scrollable rich text
        body_frame = ttk.Frame(win)
        body_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        body = tk.Text(body_frame, wrap=tk.WORD, font=("Segoe UI", 10),
                       padx=8, pady=6, height=24)
        sb = ttk.Scrollbar(body_frame, orient=tk.VERTICAL,
                           command=body.yview)
        body.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        body.tag_configure(
            "h", font=("Segoe UI", 10, "bold"), foreground="#1a4d8f",
            spacing1=8, spacing3=2,
        )
        body.tag_configure("mono", font=("Consolas", 10))
        body.tag_configure("dim", foreground="#666")
        body.tag_configure("warn", foreground="#a00")

        def _h(text: str) -> None:
            body.insert(tk.END, text + "\n", "h")

        def _p(text: str = "", tag: str | None = None) -> None:
            if tag:
                body.insert(tk.END, text + "\n", tag)
            else:
                body.insert(tk.END, text + "\n")

        # 1. Description
        desc = entry.get("desc", "")
        if desc:
            _h("Description")
            _p(desc)

        # 2. Properties
        kind = entry["kind"]
        props: list[tuple[str, str]] = []
        if "lo" in entry or "hi" in entry:
            lo = int(entry.get("lo", 0))
            hi = int(entry.get("hi", 0))
            props.append((
                "Range",
                f"{lo} .. {hi}   (0x{lo:X} .. 0x{hi:X})",
            ))
        for key, label in (
            ("default", "Default"),
            ("reset", "Reset value"),
            ("unit", "Unit"),
            ("datasheet", "Datasheet"),
            ("ref", "Reference"),
            ("access", "Access"),
        ):
            v = entry.get(key)
            if v not in (None, ""):
                if isinstance(v, int):
                    v = f"{v}   (0x{v:X})"
                props.append((label, str(v)))
        if props:
            _h("Properties")
            for k, v in props:
                _p(f"  {k:<14}{v}", "mono")

        # 3. Enum choices
        if kind == "enum":
            choices = entry.get("choices", {})
            if choices:
                _h("Possible values")
                items = sorted(choices.items(), key=lambda kv: int(kv[1]))
                for label, raw in items:
                    raw_int = int(raw)
                    width = max(8, raw_int.bit_length())
                    _p(
                        f"  0x{raw_int:02X}  ({raw_int:>3d}, "
                        f"0b{raw_int:0{width}b})   {label}",
                        "mono",
                    )

        # 4. Computed register: reads + compose + convert
        if kind == "computed":
            reads = entry.get("reads", [])
            if reads:
                _h("Sub-reads")
                for sub_r in reads:
                    a = int(sub_r["addr"])
                    nm = sub_r.get("name", f"r_{a:04x}")
                    mask = sub_r.get("mask")
                    line = f"  0x{a:04X}  -> {nm}"
                    if mask is not None:
                        line += f"  (mask {mask})"
                    _p(line, "mono")
            if entry.get("compose"):
                _h("Compose expression")
                _p(f"  raw = {entry['compose']}", "mono")
            if entry.get("convert"):
                _h("Decoded conversion")
                _p(f"  value = {entry['convert']}", "mono")
                if entry.get("unit"):
                    _p(f"  unit  = {entry['unit']}", "mono")
            if entry.get("format"):
                _h("Display format")
                _p(f"  {entry['format']}", "mono")
        else:
            # 4b. Conversion on a scalar register
            if entry.get("convert"):
                _h("Decoded conversion")
                _p(f"  value = {entry['convert']}   "
                   f"(raw = current register value)", "mono")
                if entry.get("unit"):
                    _p(f"  unit  = {entry['unit']}", "mono")
            if entry.get("format") and kind != "computed":
                _h("Display format")
                _p(f"  {entry['format']}", "mono")

        # 5. Bit-field decomposition
        bits = entry.get("bits") or []
        if bits:
            _h("Bit fields")
            _p("  bits        name                  description", "mono")
            _p("  ----        ----                  -----------", "mono")
            for bf in bits:
                lsb = int(bf.get("lsb", 0))
                width = int(bf.get("width", 1))
                msb = lsb + width - 1
                rng = f"[{msb}:{lsb}]" if width > 1 else f"[{lsb}]"
                nm = bf.get("name", "")
                d = bf.get("desc", "")
                _p(f"  {rng:<10}  {nm:<20}  {d}", "mono")

        # 6. Long-form help
        help_text = entry.get("help", "")
        if help_text:
            _h("Datasheet notes")
            _p(help_text)

        # 7. Footer hint
        _p("")
        _p(
            "Tip: copy the current value as a cfg line with "
            "Registers (raw) -> Read range, then paste into the .cfg.",
            "dim",
        )

        body.configure(state=tk.DISABLED)

        # Buttons
        btn_frame = ttk.Frame(win, padding=(10, 4, 10, 8))
        btn_frame.pack(fill=tk.X)

        def _copy_cfg_line() -> None:
            # Best-effort: only meaningful for scalar single-byte regs.
            try:
                a = int(entry["addr"])
                cur = self.worker.read_sensor_reg(a) if (
                    self.worker.is_open and kind != "computed") else None
                if cur is None:
                    line = f"REG = 0x{a:04X},0x00"
                else:
                    line = f"REG = 0x{a:04X},0x{cur:02X}"
                self.root.clipboard_clear()
                self.root.clipboard_append(line)
                self.sensor_status.set(f"copied: {line}")
            except Exception as exc:
                self.sensor_status.set(f"copy failed: {exc}")

        ttk.Button(btn_frame, text="Copy cfg line",
                   command=_copy_cfg_line).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Close",
                   command=win.destroy).pack(side=tk.RIGHT)

    def _apply_sensor_filter(self) -> None:
        needle = self.sensor_filter.get().strip().lower()
        group = self.sensor_group_var.get()
        show_combined = getattr(self, "sensor_show_combined", None)
        show_combined = (show_combined.get() if show_combined is not None
                         else True)

        # Build the set of byte-level addresses that are already covered
        # by a ``multi`` parent. A scalar row whose addr is in this set
        # is hidden in 'combined' mode and shown in 'split' mode.
        multi_part_addrs: set[int] = set()
        multi_names: set[str] = set()
        for e in self.sensor_regs:
            if e.get("kind") == "multi":
                multi_names.add(e["name"])
                for p in e.get("parts", []):
                    try:
                        multi_part_addrs.add(int(p["addr"]))
                    except (KeyError, TypeError, ValueError):
                        pass

        for name, w in self.sensor_rows:
            if name == "__header__":
                # group headers shown only when (all) is selected
                w.pack_forget()
                if group == "(all)":
                    w.pack(fill=tk.X, pady=(8, 2))
                continue
            entry = next(
                (e for e in self.sensor_regs if e["name"] == name), None
            )
            if entry is None:
                continue
            visible = True
            if group != "(all)" and entry["group"] != group:
                visible = False
            if needle:
                hay = (
                    f"{entry['name']} 0x{entry['addr']:04X} "
                    f"{entry.get('desc','')} {entry['group']}".lower()
                )
                if needle not in hay:
                    visible = False
            # Combined / split visibility rule
            kind = entry.get("kind")
            if visible:
                if show_combined:
                    # Hide the byte components that already feed a multi
                    if (kind != "multi"
                            and int(entry.get("addr", -1)) in multi_part_addrs):
                        visible = False
                else:
                    # In split-view hide the synthetic ``multi`` rows
                    if kind == "multi":
                        visible = False
            w.pack_forget()
            if visible:
                w.pack(fill=tk.X)

    # ---- read/write helpers for the Sensor regs tab ------------------
    # Restricted namespace used to evaluate conversion expressions
    # declared in the JSON profile. Only pure-math callables are exposed;
    # ``__builtins__`` is removed to block file/network access.
    _CONVERT_GLOBALS: dict = {
        "__builtins__": {},
        "abs": abs, "min": min, "max": max, "round": round,
        "int": int, "float": float, "pow": pow,
    }
    try:
        import math as _math  # noqa: E402
        _CONVERT_GLOBALS.update({
            "sqrt": _math.sqrt, "log": _math.log, "log2": _math.log2,
            "log10": _math.log10, "exp": _math.exp,
            "floor": _math.floor, "ceil": _math.ceil,
            "pi": _math.pi,
        })
    except Exception:
        pass

    def _eval_convert(self, expr: str, locals_: dict):
        """Evaluate a JSON-supplied conversion expression in a sandbox.

        ``expr`` must be a Python *expression* (no statements). The
        ``locals_`` dict typically contains ``raw`` (the integer just read
        from the register) plus any per-sub-read names declared in a
        ``computed`` entry.

        Returns ``None`` and logs the failure if evaluation raises.
        """
        if not expr:
            return None
        try:
            return eval(expr, self._CONVERT_GLOBALS, dict(locals_))
        except Exception as exc:
            self._log(f"convert eval failed for {expr!r}: {exc}")
            return None

    def _format_decoded(self, entry: dict, value, components: dict | None = None) -> str:
        """Build the secondary text shown next to ``cur=`` for a register.

        Honours, in order of precedence:

        1. ``entry['format']`` -- a Python ``str.format`` template that
           may reference ``value`` and any key from ``components`` (used
           for ``computed`` registers where each sub-read gets a name).
        2. ``entry['unit']``   -- appended after the converted value.

        Falls back to ``str(value)`` when neither is provided.
        """
        if value is None:
            return ""
        fmt = entry.get("format")
        if isinstance(fmt, str):
            try:
                ctx = dict(components or {})
                ctx["value"] = value
                return fmt.format(**ctx)
            except Exception as exc:
                self._log(f"format {fmt!r} failed: {exc}")
        unit = entry.get("unit", "")
        if isinstance(value, float):
            txt = f"{value:.3f}".rstrip("0").rstrip(".")
        else:
            txt = str(value)
        return f"{txt} {unit}".strip()

    def _sensor_read_value(self, entry: dict):
        """Read the raw integer value of an ``entry`` from the sensor.

        Handles all bit-width / endianness flavours plus ``kind='computed'``,
        which performs N independent ``read_sensor_reg`` calls and returns
        a ``(combined_int, components_dict)`` tuple. For all other kinds
        the return is just the integer (or ``None`` on failure).
        """
        if not self.worker.is_open:
            return None
        addr = entry["addr"]
        kind = entry["kind"]
        try:
            if kind == "computed":
                # Aggregate several reads into a dict of named components,
                # then evaluate ``expr`` (or compose a default sum if no
                # expression is given). Returns (combined_value, components).
                comps: dict = {}
                ok = True
                for sub in entry.get("reads", []):
                    raw = self.worker.read_sensor_reg(int(sub["addr"]))
                    if raw is None:
                        ok = False
                        break
                    if "mask" in sub:
                        try:
                            mask = int(sub["mask"], 0) if isinstance(
                                sub["mask"], str) else int(sub["mask"])
                        except Exception:
                            mask = 0xFF
                        raw &= mask
                    raw &= 0xFF
                    nm = sub.get("name")
                    if nm:
                        comps[nm] = raw
                if not ok:
                    return None
                combined = self._eval_convert(
                    entry.get("compose", "") or " + ".join(comps.keys()),
                    comps,
                )
                return (combined, comps)
            if kind in ("u8", "bool", "enum", "ro"):
                v = self.worker.read_sensor_reg(addr)
                return None if v is None else v & 0xFF
            if kind == "u16be":
                hi = self.worker.read_sensor_reg(addr)
                lo = self.worker.read_sensor_reg(addr + 1)
                if hi is None or lo is None:
                    return None
                return ((hi & 0xFF) << 8) | (lo & 0xFF)
            if kind == "u16le":
                lo = self.worker.read_sensor_reg(addr)
                hi = self.worker.read_sensor_reg(addr + 1)
                if hi is None or lo is None:
                    return None
                return ((hi & 0xFF) << 8) | (lo & 0xFF)
            if kind == "u24be":
                b2 = self.worker.read_sensor_reg(addr)
                b1 = self.worker.read_sensor_reg(addr + 1)
                b0 = self.worker.read_sensor_reg(addr + 2)
                if None in (b0, b1, b2):
                    return None
                return ((b2 & 0xFF) << 16) | ((b1 & 0xFF) << 8) | (b0 & 0xFF)
            if kind == "multi":
                # Multi-byte field with arbitrary bit splits. Each part
                # describes a window inside a single 8-bit register and
                # the corresponding window inside the combined value.
                # Returns a (combined, parts_dict) tuple; ``parts_dict``
                # maps the raw byte read from each part address (used
                # for the per-byte breakdown shown in ``cur_lbl``).
                combined = 0
                parts_raw: dict = {}
                for p in entry.get("parts", []):
                    pa = int(p["addr"])
                    byte = self.worker.read_sensor_reg(pa)
                    if byte is None:
                        return None
                    byte &= 0xFF
                    parts_raw[f"0x{pa:04X}"] = byte
                    reg_lsb = int(p.get("reg_lsb", 0))
                    width = int(p["width"])
                    val_lsb = int(p["value_lsb"])
                    field = (byte >> reg_lsb) & ((1 << width) - 1)
                    combined |= field << val_lsb
                return (combined, parts_raw)
        except Exception as exc:
            self._log(f"sensor reg read {entry['name']}: {exc}")
        return None

    def _sensor_write_value(self, entry: dict, value: int) -> bool:
        """Write ``value`` back to the sensor for ``entry``.

        Returns False for read-only kinds (``ro`` and ``computed``); the
        caller is expected to gate write attempts on the entry kind, but
        we double-check here so a stray Write button click never reaches
        the bus. Multi-byte kinds are split into per-address writes; on
        any sub-write failure the upper layer simply reports FAIL — the
        sensor is left in a partially-written state, which is acceptable
        because the user can re-issue the write.
        """
        if not self.worker.is_open:
            return False
        if entry["kind"] in ("ro", "computed"):
            return False
        addr = entry["addr"]
        kind = entry["kind"]
        try:
            if kind in ("u8", "bool", "enum"):
                return self.worker.write_sensor_reg(addr, value & 0xFF)
            if kind == "u16be":
                ok1 = self.worker.write_sensor_reg(addr, (value >> 8) & 0xFF)
                ok2 = self.worker.write_sensor_reg(addr + 1, value & 0xFF)
                return ok1 and ok2
            if kind == "u16le":
                ok1 = self.worker.write_sensor_reg(addr, value & 0xFF)
                ok2 = self.worker.write_sensor_reg(addr + 1, (value >> 8) & 0xFF)
                return ok1 and ok2
            if kind == "u24be":
                ok1 = self.worker.write_sensor_reg(addr, (value >> 16) & 0xFF)
                ok2 = self.worker.write_sensor_reg(addr + 1, (value >> 8) & 0xFF)
                ok3 = self.worker.write_sensor_reg(addr + 2, value & 0xFF)
                return ok1 and ok2 and ok3
            if kind == "multi":
                # Per part: read-modify-write so we don't trample the
                # bits in the same byte that belong to a different
                # field (e.g. VSTART1[10:8] shares 0x107E with
                # VSTART2[4:0]). If the write target has reg_lsb=0 and
                # width=8 the read step is skipped (full-byte write).
                for p in entry.get("parts", []):
                    pa = int(p["addr"])
                    reg_lsb = int(p.get("reg_lsb", 0))
                    width = int(p["width"])
                    val_lsb = int(p["value_lsb"])
                    field_mask = ((1 << width) - 1)
                    field = (value >> val_lsb) & field_mask
                    if reg_lsb == 0 and width == 8:
                        new_byte = field & 0xFF
                    else:
                        cur = self.worker.read_sensor_reg(pa)
                        if cur is None:
                            return False
                        cur &= 0xFF
                        cur &= ~(field_mask << reg_lsb) & 0xFF
                        new_byte = cur | ((field & field_mask) << reg_lsb)
                    if not self.worker.write_sensor_reg(pa, new_byte):
                        return False
                return True
        except Exception as exc:
            self._log(f"sensor reg write {entry['name']}: {exc}")
        return False

    def _sensor_set_widget(self, name: str, raw) -> None:
        """Update the row UI after a successful read.

        ``raw`` is either an integer (most kinds) or a
        ``(combined, components)`` tuple for ``kind='computed'``. The
        method renders both the raw value and -- when the JSON entry
        contains ``convert``/``unit``/``format`` -- a decoded display
        (e.g. temperature in degrees Celsius).
        """
        wd = self.sensor_widgets[name]
        entry = wd["entry"]
        kind = entry["kind"]
        if kind == "computed":
            if isinstance(raw, tuple):
                combined, comps = raw
            else:
                combined, comps = raw, {}
            decoded = None
            if entry.get("convert"):
                ctx = dict(comps)
                ctx["raw"] = combined
                decoded = self._eval_convert(entry["convert"], ctx)
            display = self._format_decoded(
                entry, decoded if decoded is not None else combined, comps,
            )
            wd["cur_lbl"].configure(text=display or f"raw={combined}")
            return
        if kind == "ro":
            wd["cur_lbl"].configure(text=f"= 0x{raw:X} ({raw})")
        elif kind == "bool":
            if wd["var"] is not None:
                wd["var"].set(bool(raw & 0x01))
            wd["cur_lbl"].configure(text=f"cur={'on' if raw & 1 else 'off'}")
        elif kind == "enum":
            label = next(
                (k for k, v in entry["choices"].items() if v == raw),
                f"raw=0x{raw:X}",
            )
            if wd["var"] is not None:
                # Always reflect the actual value: if the read value is not
                # one of the known choices, expose it as a transient extra
                # entry so the dropdown shows what the sensor returned.
                if not any(v == raw for v in entry["choices"].values()):
                    cb = wd.get("combo")
                    if cb is not None:
                        cur = list(cb["values"])
                        if label not in cur:
                            cb["values"] = [*cur, label]
                wd["var"].set(label)
            wd["cur_lbl"].configure(text=f"cur={label}")
        else:
            # For ``kind='multi'`` the read returns a (combined, parts)
            # tuple; for the scalar kinds it's a plain integer.
            if kind == "multi":
                if isinstance(raw, tuple):
                    combined, parts_raw = raw
                else:
                    combined, parts_raw = int(raw), {}
            else:
                combined, parts_raw = None, {}
            if wd["var"] is not None:
                try:
                    wd["var"].set(int(combined if kind == "multi" else raw))
                except tk.TclError:
                    pass
            # Render the cumulative value in dec + hex; for multi-byte
            # kinds also list the individual bytes so the user can
            # cross-check what is actually on the bus.
            if kind == "multi":
                v = combined
                width_bits = max(1, v.bit_length())
                hex_w = max(2, (width_bits + 3) // 4)
                base = f"cur={v} (0x{v:0{hex_w}X})"
                if isinstance(parts_raw, dict) and parts_raw:
                    parts_txt = ", ".join(
                        f"{a}=0x{b:02X}" for a, b in parts_raw.items()
                    )
                    base += f"  [{parts_txt}]"
            elif kind in ("u16be", "u16le", "u24be"):
                v = int(raw)
                hex_w = 6 if kind == "u24be" else 4
                base = f"cur={v} (0x{v:0{hex_w}X})"
                if kind == "u24be":
                    b2 = (v >> 16) & 0xFF
                    b1 = (v >> 8) & 0xFF
                    b0 = v & 0xFF
                    base += f"  [b2=0x{b2:02X} b1=0x{b1:02X} b0=0x{b0:02X}]"
                elif kind == "u16be":
                    hi = (v >> 8) & 0xFF
                    lo = v & 0xFF
                    base += f"  [hi=0x{hi:02X} lo=0x{lo:02X}]"
                else:  # u16le
                    hi = (v >> 8) & 0xFF
                    lo = v & 0xFF
                    base += f"  [lo=0x{lo:02X} hi=0x{hi:02X}]"
            else:
                base = f"cur={raw}"
            if entry.get("convert"):
                conv_in = combined if kind == "multi" else raw
                decoded = self._eval_convert(
                    entry["convert"], {"raw": conv_in},
                )
                if decoded is not None:
                    base += "  (" + self._format_decoded(
                        entry, decoded, {"raw": conv_in}
                    ) + ")"
            wd["cur_lbl"].configure(text=base)

    def _sensor_get_widget_value(self, name: str):
        wd = self.sensor_widgets[name]
        entry = wd["entry"]
        kind = entry["kind"]
        if kind == "ro" or wd["var"] is None:
            return None
        try:
            if kind == "bool":
                return 1 if wd["var"].get() else 0
            if kind == "enum":
                return int(entry["choices"][wd["var"].get()])
            return int(wd["var"].get())
        except (KeyError, tk.TclError, ValueError):
            return None

    def _sensor_read_one(self, name: str) -> None:
        if not self.worker.is_open:
            self.sensor_status.set("camera closed")
            return
        wd = self.sensor_widgets[name]
        v = self._sensor_read_value(wd["entry"])
        if v is None:
            self.sensor_status.set(f"read {name}: FAIL")
            return
        self._sensor_set_widget(name, v)
        # ``v`` may be a (combined, components) tuple for kind=computed.
        if isinstance(v, tuple):
            shown = v[0]
        else:
            shown = v
        self.sensor_status.set(
            f"read {name} @0x{wd['entry']['addr']:04X} = {shown}"
        )

    def _sensor_write_one(self, name: str) -> None:
        if not self.worker.is_open:
            self.sensor_status.set("camera closed")
            return
        wd = self.sensor_widgets[name]
        v = self._sensor_get_widget_value(name)
        if v is None:
            self.sensor_status.set(f"{name}: invalid value")
            return
        ok = self._sensor_write_value(wd["entry"], v)
        self.sensor_status.set(
            f"write {name} = {v}: {'OK' if ok else 'FAIL'}"
        )
        if ok:
            wd["cur_lbl"].configure(text=f"set={v}")

    def _sensor_read_all(self) -> None:
        if not self.worker.is_open:
            self.sensor_status.set("camera closed")
            return
        ok = fail = 0
        for name, wd in self.sensor_widgets.items():
            v = self._sensor_read_value(wd["entry"])
            if v is None:
                fail += 1
            else:
                ok += 1
                self._sensor_set_widget(name, v)
        self.sensor_status.set(f"read all: ok={ok} fail={fail}")

    def _sensor_write_all(self) -> None:
        if not self.worker.is_open:
            self.sensor_status.set("camera closed")
            return
        ok = fail = skip = 0
        for name, wd in self.sensor_widgets.items():
            if wd["entry"]["kind"] == "ro":
                skip += 1
                continue
            v = self._sensor_get_widget_value(name)
            if v is None:
                fail += 1
                continue
            if self._sensor_write_value(wd["entry"], v):
                ok += 1
                wd["cur_lbl"].configure(text=f"set={v}")
            else:
                fail += 1
        self.sensor_status.set(f"write all: ok={ok} fail={fail} (skipped {skip} read-only)")

    def _sensor_reload_cfg_defaults(self) -> None:
        """Re-apply every `REG = addr,value` line from the active cfg file.

        Useful to restore the documented defaults after experimenting in the
        Sensor regs tab without having to close+reopen the camera.
        """
        if not self.worker.is_open:
            self.sensor_status.set("camera closed")
            return
        cfg = self.cfg_var.get().strip()
        if not cfg or not os.path.isfile(cfg):
            self.sensor_status.set("cfg file not found")
            return
        ok = fail = 0
        last_err = ""
        try:
            with open(cfg, "r", encoding="utf-8", errors="ignore") as fh:
                for raw in fh:
                    line = raw.split(";", 1)[0].split("//", 1)[0].strip()
                    if not line.upper().startswith("REG"):
                        continue
                    # syntax: "REG = 0xADDR,0xVAL"
                    try:
                        _, body = line.split("=", 1)
                        addr_s, val_s = body.split(",", 1)
                        addr = int(addr_s.strip(), 0)
                        val = int(val_s.strip(), 0) & 0xFF
                    except Exception:
                        continue
                    try:
                        if self.worker.write_sensor_reg(addr, val):
                            ok += 1
                        else:
                            fail += 1
                    except Exception as exc:
                        fail += 1
                        last_err = str(exc)
        except Exception as exc:
            self.sensor_status.set(f"cfg read error: {exc}")
            return
        self.sensor_status.set(
            f"reload cfg: wrote ok={ok} fail={fail}"
            + (f"  (last err: {last_err})" if last_err else "")
        )
        # Refresh the displayed values for visible registers.
        try:
            self._sensor_read_all()
        except Exception:
            pass

    def _build_regs_tab(self, parent: ttk.Frame) -> None:
        self._make_tab_hint(parent, "registers")

        # Pick from profile
        pick = ttk.LabelFrame(
            parent, text="Pick from sensor profile", padding=8
        )
        pick.pack(fill=tk.X)
        ttk.Label(pick, text="Register:").pack(side=tk.LEFT)
        self.regs_pick_var = tk.StringVar()
        self.regs_pick_combo = ttk.Combobox(
            pick, textvariable=self.regs_pick_var, width=60, state="readonly",
        )
        self.regs_pick_combo.pack(side=tk.LEFT, padx=4)
        self.regs_pick_combo.bind(
            "<<ComboboxSelected>>", lambda *_: self._regs_pick_apply()
        )
        ttk.Button(pick, text="↻", width=3,
                   command=self._regs_refresh_picker).pack(side=tk.LEFT, padx=2)

        # Single read / write
        single = ttk.LabelFrame(parent, text="Single register (sensor)", padding=8)
        single.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(single, text="Addr (hex):").grid(row=0, column=0, sticky=tk.W)
        self.reg_addr = tk.StringVar(value="0x1012")
        addr_entry = ttk.Entry(single, textvariable=self.reg_addr, width=10)
        addr_entry.grid(row=0, column=1)
        # Auto-resolve description on address change.
        self.reg_addr.trace_add(
            "write", lambda *_: self._regs_resolve_addr()
        )

        ttk.Label(single, text="Value (hex):").grid(row=0, column=2, padx=(12, 0))
        self.reg_val = tk.StringVar(value="0x00")
        ttk.Entry(single, textvariable=self.reg_val, width=10).grid(row=0, column=3)
        # When the value is changed (manually or via Read), refresh bit panel.
        self.reg_val.trace_add(
            "write", lambda *_: self._regs_sync_bits_from_value()
        )

        ttk.Button(single, text="Read", command=self._reg_read).grid(
            row=0, column=4, padx=(12, 4)
        )
        ttk.Button(single, text="Write", command=self._reg_write).grid(
            row=0, column=5, padx=4
        )
        self.reg_log = tk.StringVar(value="")
        ttk.Label(single, textvariable=self.reg_log, foreground="#0a0").grid(
            row=1, column=0, columnspan=6, sticky=tk.W, pady=(4, 0)
        )

        # Bit-level view
        bits_frame = ttk.LabelFrame(parent, text="Bits (b7..b0 of Value)", padding=8)
        bits_frame.pack(fill=tk.X, pady=(8, 0))
        self._regs_bit_vars: list[tk.IntVar] = []
        self._regs_bit_lbls: list[ttk.Label] = []
        bit_row = ttk.Frame(bits_frame)
        bit_row.pack(fill=tk.X)
        # Suppress recursion when the value field updates the checkboxes.
        self._regs_bit_updating = False
        for i in range(8):
            col = ttk.Frame(bit_row)
            col.pack(side=tk.RIGHT, padx=4)  # b7 leftmost (RIGHT-pack reverses)
            ttk.Label(col, text=f"b{i}", font=("Consolas", 9),
                      foreground="#888").pack()
            v = tk.IntVar(value=0)
            cb = ttk.Checkbutton(
                col, variable=v,
                command=lambda i=i: self._regs_toggle_bit(i),
            )
            cb.pack()
            lbl = ttk.Label(col, text="", font=("Consolas", 8),
                            foreground="#4ec9b0", wraplength=80,
                            justify="center")
            lbl.pack()
            self._regs_bit_vars.append(v)
            self._regs_bit_lbls.append(lbl)

        # Description / decoded panel
        info_frame = ttk.LabelFrame(parent, text="Register description", padding=8)
        info_frame.pack(fill=tk.BOTH, expand=False, pady=(8, 0))
        self.regs_info_text = tk.Text(
            info_frame, height=8, wrap=tk.WORD, font=("Segoe UI", 9),
        )
        self.regs_info_text.pack(fill=tk.BOTH, expand=True)
        self.regs_info_text.insert(tk.END, "(select a register or type an address)")
        self.regs_info_text.configure(state=tk.DISABLED)

        # Range dump
        dump = ttk.LabelFrame(parent, text="Sensor register dump", padding=8)
        dump.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        opt = ttk.Frame(dump)
        opt.pack(fill=tk.X)
        ttk.Label(opt, text="Start (hex):").pack(side=tk.LEFT)
        self.dump_start = tk.StringVar(value="0x1000")
        ttk.Entry(opt, textvariable=self.dump_start, width=10).pack(side=tk.LEFT, padx=4)
        ttk.Label(opt, text="End (hex):").pack(side=tk.LEFT)
        self.dump_end = tk.StringVar(value="0x6FFF")
        ttk.Entry(opt, textvariable=self.dump_end, width=10).pack(side=tk.LEFT, padx=4)

        ttk.Button(opt, text="Dump…", command=self._dump_regs_to_file).pack(
            side=tk.LEFT, padx=12
        )
        self.dump_progress = ttk.Progressbar(opt, mode="determinate", length=260)
        self.dump_progress.pack(side=tk.LEFT, padx=8)
        self.dump_status = tk.StringVar(value="")
        ttk.Label(opt, textvariable=self.dump_status).pack(side=tk.LEFT, padx=8)

        # Quick presets aligned with the active sensor profile address map
        presets = ttk.Frame(dump, padding=(0, 8))
        presets.pack(fill=tk.X)
        ttk.Label(presets, text="Quick ranges:").pack(side=tk.LEFT)
        for label, s, e in (
            ("Exp/Frame 0x1000-0x10FF", 0x1000, 0x10FF),
            ("Window 0x107D-0x108B", 0x107D, 0x108B),
            ("MIPI 0x6000-0x60FF", 0x6000, 0x60FF),
            ("Full 0x1000-0x6FFF", 0x1000, 0x6FFF),
        ):
            ttk.Button(
                presets,
                text=label,
                command=lambda s=s, e=e: (
                    self.dump_start.set(f"0x{s:04X}"),
                    self.dump_end.set(f"0x{e:04X}"),
                ),
            ).pack(side=tk.LEFT, padx=2)

    def _build_adv_tab(self, parent: ttk.Frame) -> None:
        self._make_tab_hint(parent, "advanced")
        # USB transfer
        tx = ttk.LabelFrame(parent, text="USB transfer", padding=8)
        tx.pack(fill=tk.X)
        ttk.Label(tx, text="Count:").grid(row=0, column=0, sticky=tk.W)
        self.tx_count = tk.IntVar(value=30)
        ttk.Spinbox(tx, from_=1, to=4096, textvariable=self.tx_count, width=8).grid(
            row=0, column=1
        )
        ttk.Label(tx, text="Size (bytes):").grid(row=0, column=2, padx=(12, 0))
        self.tx_size = tk.IntVar(value=512 * 1024)
        ttk.Spinbox(
            tx, from_=4096, to=8 * 1024 * 1024, textvariable=self.tx_size, width=12
        ).grid(row=0, column=3)
        ttk.Button(tx, text="Apply", command=self._apply_transfer).grid(
            row=0, column=4, padx=12
        )
        ttk.Label(
            tx,
            text="Apply after open() and before start(). Larger size → fewer transfers.",
            foreground="#666",
        ).grid(row=1, column=0, columnspan=5, sticky=tk.W, pady=(4, 0))

        # Mode switch
        mode = ttk.LabelFrame(parent, text="Sensor mode (binary cfg)", padding=8)
        mode.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(mode, text="Mode ID:").grid(row=0, column=0, sticky=tk.W)
        self.mode_id = tk.IntVar(value=0)
        ttk.Spinbox(mode, from_=0, to=63, textvariable=self.mode_id, width=6).grid(
            row=0, column=1
        )
        ttk.Button(mode, text="Switch", command=self._switch_mode).grid(
            row=0, column=2, padx=12
        )

        # Logging
        log = ttk.LabelFrame(parent, text="SDK logging", padding=8)
        log.pack(fill=tk.X, pady=(8, 0))
        self.console_log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            log,
            text="Enable console log",
            variable=self.console_log_var,
            command=self._toggle_console_log,
        ).pack(side=tk.LEFT)
        self.log_level_var = tk.StringVar(value="Info")
        ttk.Label(log, text=" Level:").pack(side=tk.LEFT, padx=(12, 0))
        ttk.Combobox(
            log,
            textvariable=self.log_level_var,
            values=["Trace", "Debug", "Info", "Warn", "Error", "Critical", "Off"],
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT)
        ttk.Button(log, text="Apply level", command=self._apply_log_level).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(log, text="Add log file…", command=self._add_log_file).pack(
            side=tk.LEFT, padx=4
        )

    def _build_info_tab(self, parent: ttk.Frame) -> None:
        self._make_tab_hint(parent, "info")
        top = ttk.Frame(parent)
        top.pack(fill=tk.X)
        ttk.Button(top, text="Refresh info", command=self._refresh_info).pack(
            side=tk.LEFT
        )
        ttk.Button(top, text="Clear log", command=self._clear_log).pack(
            side=tk.LEFT, padx=8
        )

        info_frame = ttk.LabelFrame(parent, text="Camera info", padding=8)
        info_frame.pack(fill=tk.X, pady=(8, 0))
        self.info_text = tk.Text(info_frame, height=10, font=("Consolas", 10))
        self.info_text.pack(fill=tk.X)
        self.info_text.configure(state=tk.DISABLED)

        log_frame = ttk.LabelFrame(parent, text="SDK log", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.log_text = tk.Text(log_frame, font=("Consolas", 9), bg="#101010", fg="#d0d0d0")
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

        self._log_pending: "queue.Queue[str]" = queue.Queue()
        self.root.after(100, self._drain_logs)

    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=4, relief=tk.SUNKEN)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self.status_var, anchor=tk.W).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )

    # ----- handlers ------------------------------------------------------
    def _browse_cfg(self) -> None:
        path = filedialog.askopenfilename(
            title="Select camera config",
            filetypes=[("Config", "*.cfg *.bin"), ("All files", "*.*")],
            initialdir=str(DEFAULT_CFG.parent if DEFAULT_CFG.exists() else REPO_ROOT),
        )
        if path:
            self.cfg_var.set(path)

    def refresh_devices(self) -> None:
        try:
            devs = DeviceList().devices()
        except Exception as exc:
            self._log(f"DeviceList error: {exc}")
            devs = []
        labels = []
        for i, d in enumerate(devs):
            try:
                sn = "".join(chr(c) for c in d.serial_number).strip("\x00 ")
            except Exception:
                sn = "?"
            labels.append(
                f"{i}: VID 0x{d.id_vendor:04X} PID 0x{d.id_product:04X}  SN={sn}"
            )
        self.device_combo["values"] = labels
        if labels:
            self.device_combo.current(0)
        else:
            self.device_var.set("(no devices)")

    def _selected_device_index(self) -> int:
        idx = self.device_combo.current()
        return idx if idx >= 0 else 0

    def _on_open(self) -> None:
        cfg = self.cfg_var.get().strip()
        if not cfg or not Path(cfg).exists():
            messagebox.showerror("Config", f"Config file not found:\n{cfg}")
            return
        try:
            self.worker.open(cfg, self._selected_device_index(), self.dma_var.get())
        except Exception as exc:
            messagebox.showerror("Open", str(exc))
            self._log("".join(traceback.format_exc()))
            return
        self.open_btn.configure(state=tk.DISABLED)
        self.close_btn.configure(state=tk.NORMAL)
        self.start_btn.configure(state=tk.NORMAL)
        # Auto-select sensor profile from cfg before populating things that
        # depend on it (so hints / sensor read-all use the right profile).
        self._auto_select_profile_from_cfg(cfg)
        self._populate_controls()
        self._refresh_info()
        self._update_tab_states()
        try:
            self._sensor_read_all()
        except Exception:
            pass
        self.status_var.set(
            f"Opened: {self.worker.stats.width}x{self.worker.stats.height}"
        )

    # ------------------------------------------------------------------
    # cfg → sensor profile auto-association
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_cfg_type(cfg_path: str) -> str:
        """Return the TYPE field of the cfg file (uppercased), or ''."""
        try:
            for line in Path(cfg_path).read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines():
                s = line.strip()
                if not s or s.startswith(";") or s.startswith("#"):
                    continue
                if "=" not in s:
                    continue
                key, _, val = s.partition("=")
                if key.strip().upper() == "TYPE":
                    # Strip inline comments (';' or '#') and whitespace.
                    for sep in (";", "#"):
                        if sep in val:
                            val = val.split(sep, 1)[0]
                    return val.strip().upper()
        except Exception:
            pass
        return ""

    def _match_profile(self, cfg_type: str, cfg_path: str) -> Optional[str]:
        """Pick the best profile name for *cfg_type* / *cfg_path*."""
        if not getattr(self, "sensor_profiles", None):
            return None
        cfg_type = (cfg_type or "").upper()
        cfg_stem = Path(cfg_path).stem.upper()
        best: Optional[str] = None
        # 1) explicit "match" list inside the JSON profile.
        for name, prof in self.sensor_profiles.items():
            patterns = prof.get("match") if isinstance(prof, dict) else None
            if isinstance(patterns, list):
                for p in patterns:
                    p_up = str(p).upper()
                    if p_up and (p_up in cfg_type or p_up in cfg_stem):
                        return name
        # 2) profile name appears in cfg TYPE.
        for name in self.sensor_profiles:
            if name.upper() in cfg_type and cfg_type:
                return name
        # 3) profile name appears in cfg filename.
        for name in self.sensor_profiles:
            if name.upper() in cfg_stem:
                best = name
                break
        return best

    def _auto_select_profile_from_cfg(self, cfg_path: str) -> None:
        cfg_type = self._parse_cfg_type(cfg_path)
        match = self._match_profile(cfg_type, cfg_path)
        if not match or match == self.sensor_profile_name:
            if cfg_type:
                self._log(
                    f"cfg TYPE={cfg_type} → profile '{self.sensor_profile_name}' "
                    "(unchanged)"
                )
            return
        self._log(f"cfg TYPE={cfg_type} → switching profile to '{match}'")
        self.sensor_profile_var.set(match)
        self._sensor_set_profile(match)

    def _on_close_camera(self) -> None:
        try:
            self.worker.close()
        except Exception as exc:
            self._log(f"close error: {exc}")
        self.open_btn.configure(state=tk.NORMAL)
        self.close_btn.configure(state=tk.DISABLED)
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.DISABLED)
        self._build_controls_placeholder()
        self._update_tab_states()
        self.status_var.set("Closed.")

    def _on_start(self) -> None:
        try:
            self.worker.start()
            self.start_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)
        except Exception as exc:
            messagebox.showerror("Start", str(exc))

    def _on_stop(self) -> None:
        self.worker.stop()
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    def _apply_control(self, name: str, value: int, func: Optional[str] = None) -> None:
        # Prefer the SDK's controller "func" name (e.g. setFramerate); fall
        # back to the friendly name if not provided.
        target = func or self._ctrl_meta.get(name, {}).get("func") or name
        ok = self.worker.set_control(target, value)
        msg = f"set_control {name} ({target})={value} → {'OK' if ok else 'FAIL'}"
        self._log(msg)
        self.status_var.set(msg)
        # Couple Exp(us) max to current Framerate so lowering the framerate
        # immediately allows longer exposures (max ≈ 1e6 / fps).
        if ok and name.lower().startswith("framerate"):
            self._sync_exp_to_fps(int(value))

    def _sync_exp_to_fps(self, fps: Optional[int] = None) -> None:
        meta = getattr(self, "_ctrl_meta", None)
        if not meta:
            return
        # Find the framerate and exposure controls case-insensitively.
        fps_key = next(
            (k for k in meta if k.lower().startswith("framerate")), None
        )
        exp_key = next(
            (k for k in meta if "exp" in k.lower()), None
        )
        if exp_key is None:
            return
        if fps is None:
            if fps_key is None:
                return
            fps = int(round(self.control_widgets[fps_key].get()))
        if fps <= 0:
            return
        exp_meta = meta[exp_key]
        # Cap at 1e6 / fps minus a tiny margin for readout overhead, but never
        # below the cfg-declared minimum / maximum.
        new_max = max(exp_meta["min"], int(1_000_000 / fps) - 5)
        new_max = min(new_max, max(exp_meta["max"] * 4, 1_000_000))
        scale = self.control_widgets[exp_key]
        scale.configure(to=new_max)
        # If the current value is now out of range, clamp it.
        cur = int(round(scale.get()))
        if cur > new_max:
            scale.set(new_max)
            self.control_value_lbls[exp_key].configure(text=str(new_max))
        exp_meta["range_lbl"].configure(
            text=f"[{exp_meta['min']}..{new_max} step {exp_meta['step']}] (fps={fps})"
        )

    def _apply_transfer(self) -> None:
        try:
            self.worker.set_transfer(self.tx_count.get(), self.tx_size.get())
            self.status_var.set(
                f"Transfer set count={self.tx_count.get()} size={self.tx_size.get()}"
            )
        except Exception as exc:
            messagebox.showerror("Transfer", str(exc))

    def _switch_mode(self) -> None:
        if not self.worker.is_open:
            return
        try:
            ok = self.worker.camera.switch_mode(int(self.mode_id.get()))
            self._log(
                f"switch_mode({self.mode_id.get()}) → {'OK' if ok else 'FAIL'}"
            )
        except Exception as exc:
            messagebox.showerror("Mode", str(exc))

    def _toggle_console_log(self) -> None:
        if self.worker.is_open:
            self.worker.camera.enable_console_log(self.console_log_var.get())

    def _apply_log_level(self) -> None:
        if not self.worker.is_open:
            return
        level = getattr(LoggerLevel, self.log_level_var.get(), LoggerLevel.Info)
        self.worker.camera.log_level = level
        self._log(f"log level → {self.log_level_var.get()}")

    def _add_log_file(self) -> None:
        if not self.worker.is_open:
            return
        path = filedialog.asksaveasfilename(
            title="SDK log file",
            defaultextension=".log",
            filetypes=[("Log", "*.log *.txt"), ("All files", "*.*")],
        )
        if path:
            self.worker.camera.add_log_file(path)
            self._log(f"SDK log → {path}")

    # ----- registers -----------------------------------------------------
    @staticmethod
    def _parse_int(s: str) -> int:
        s = s.strip()
        return int(s, 0)

    def _reg_read(self) -> None:
        if not self.worker.is_open:
            return
        try:
            addr = self._parse_int(self.reg_addr.get())
        except ValueError:
            self.reg_log.set("invalid address")
            return
        val = self.worker.read_sensor_reg(addr)
        if val is None:
            self.reg_log.set("read failed")
            return
        self.reg_val.set(f"0x{val & 0xFF:02X}")
        self.reg_log.set(f"read 0x{addr:04X} = 0x{val & 0xFF:02X}")

    def _reg_write(self) -> None:
        if not self.worker.is_open:
            return
        try:
            addr = self._parse_int(self.reg_addr.get())
            val = self._parse_int(self.reg_val.get())
        except ValueError:
            self.reg_log.set("invalid hex value")
            return
        ok = self.worker.write_sensor_reg(addr, val)
        self.reg_log.set(
            f"write 0x{addr:04X} = 0x{val & 0xFF:02X} → {'OK' if ok else 'FAIL'}"
        )

    # ------------------------------------------------------------------
    # Registers (raw) tab — profile picker + bit panel + description
    # ------------------------------------------------------------------
    def _regs_refresh_picker(self) -> None:
        """Populate the picker combobox with one entry per byte of every
        register in the active sensor profile."""
        items: list[tuple[str, int]] = []
        for e in getattr(self, "sensor_regs", []):
            kind = e.get("kind", "u8")
            base = e["addr"]
            name = e.get("name", "?")
            grp = e.get("group", "")
            if kind in ("u8", "bool", "enum", "ro"):
                items.append((f"0x{base:04X}  {name}  [{grp}]", base))
            elif kind == "u16be":
                items.append((f"0x{base:04X}  {name}  (MSB) [{grp}]", base))
                items.append((f"0x{base + 1:04X}  {name}  (LSB) [{grp}]", base + 1))
            elif kind == "u16le":
                items.append((f"0x{base:04X}  {name}  (LSB) [{grp}]", base))
                items.append((f"0x{base + 1:04X}  {name}  (MSB) [{grp}]", base + 1))
            elif kind == "u24be":
                items.append((f"0x{base:04X}  {name}  (MSB) [{grp}]", base))
                items.append((f"0x{base + 1:04X}  {name}  (mid) [{grp}]", base + 1))
                items.append((f"0x{base + 2:04X}  {name}  (LSB) [{grp}]", base + 2))
            else:
                items.append((f"0x{base:04X}  {name}  [{grp}]", base))
        items.sort(key=lambda it: it[1])
        self._regs_pick_items = items
        self.regs_pick_combo.configure(values=[lbl for lbl, _ in items])

    def _regs_pick_apply(self) -> None:
        sel = self.regs_pick_combo.current()
        items = getattr(self, "_regs_pick_items", [])
        if sel < 0 or sel >= len(items):
            return
        addr = items[sel][1]
        self.reg_addr.set(f"0x{addr:04X}")
        if self.worker.is_open:
            try:
                self._reg_read()
            except Exception as exc:
                self._log(f"reg read on pick: {exc}")

    def _regs_lookup(self, addr: int) -> tuple[Optional[dict], int]:
        """Return ``(entry, byte_offset)`` for *addr* in the active profile.

        ``byte_offset`` is 0 for a u8 / bool / enum / ro register, and the
        position within a multi-byte register (0=MSB or LSB depending on
        endianness) when *addr* falls inside one.
        """
        for e in getattr(self, "sensor_regs", []):
            base = e["addr"]
            kind = e.get("kind", "u8")
            if kind in ("u8", "bool", "enum", "ro"):
                if addr == base:
                    return e, 0
            elif kind in ("u16be", "u16le"):
                if base <= addr <= base + 1:
                    return e, addr - base
            elif kind == "u24be":
                if base <= addr <= base + 2:
                    return e, addr - base
        return None, 0

    def _regs_resolve_addr(self) -> None:
        """Update the description panel based on the current address."""
        try:
            addr = self._parse_int(self.reg_addr.get())
        except ValueError:
            self._regs_set_info("(invalid address)")
            self._regs_apply_bit_names(None)
            return
        entry, offset = self._regs_lookup(addr)
        self._regs_apply_bit_names(entry)
        if entry is None:
            self._regs_set_info(
                f"0x{addr:04X}: not described in the '"
                f"{getattr(self, 'sensor_profile_name', '?')}' profile.\n"
                "Reading is still allowed."
            )
            return
        kind = entry.get("kind", "u8")
        lines = [
            f"{entry.get('name', '?')}    [{entry.get('group', '')}]",
            f"  addr  = 0x{entry['addr']:04X}    kind = {kind}",
        ]
        if kind in ("u16be", "u16le", "u24be"):
            roles = {
                "u16be": ["MSB", "LSB"],
                "u16le": ["LSB", "MSB"],
                "u24be": ["MSB", "mid", "LSB"],
            }[kind]
            lines.append(f"  byte  = {roles[offset]} (offset +{offset})")
        if "lo" in entry or "hi" in entry:
            lines.append(
                f"  range = {entry.get('lo', 0)} .. "
                f"{entry.get('hi', 0xFF if kind == 'u8' else 0xFFFF)}"
            )
        if kind == "enum" and isinstance(entry.get("choices"), dict):
            lines.append("  values:")
            for k, v in entry["choices"].items():
                lines.append(f"    {v:>4}  →  {k}")
        if entry.get("desc"):
            lines.append("")
            lines.append(entry["desc"])
        if entry.get("help"):
            lines.append("")
            lines.append(entry["help"])
        self._regs_set_info("\n".join(lines))

    def _regs_set_info(self, text: str) -> None:
        if not hasattr(self, "regs_info_text"):
            return
        self.regs_info_text.configure(state=tk.NORMAL)
        self.regs_info_text.delete("1.0", tk.END)
        self.regs_info_text.insert(tk.END, text)
        self.regs_info_text.configure(state=tk.DISABLED)

    def _regs_apply_bit_names(self, entry: Optional[dict]) -> None:
        """Label each of the 8 bit checkboxes from the entry's ``bits`` list.

        Schema (optional, per register entry):
            "bits": [{"lsb": 0, "width": 1, "name": "EN", "desc": "..."}, ...]
        Bits not covered by any spec are left unlabeled.
        """
        names = ["" for _ in range(8)]
        if entry is not None and isinstance(entry.get("bits"), list):
            for spec in entry["bits"]:
                if not isinstance(spec, dict):
                    continue
                try:
                    lsb = int(spec.get("lsb", 0))
                    width = max(1, int(spec.get("width", 1)))
                except (TypeError, ValueError):
                    continue
                label = str(spec.get("name", ""))
                for b in range(lsb, min(8, lsb + width)):
                    names[b] = label
        for i, lbl in enumerate(self._regs_bit_lbls):
            lbl.configure(text=names[i])

    def _regs_sync_bits_from_value(self) -> None:
        """Reflect ``self.reg_val`` into the 8 bit checkboxes."""
        try:
            v = self._parse_int(self.reg_val.get()) & 0xFF
        except ValueError:
            return
        self._regs_bit_updating = True
        try:
            for i, var in enumerate(self._regs_bit_vars):
                var.set(1 if (v >> i) & 1 else 0)
        finally:
            self._regs_bit_updating = False

    def _regs_toggle_bit(self, i: int) -> None:
        if getattr(self, "_regs_bit_updating", False):
            return
        try:
            v = self._parse_int(self.reg_val.get()) & 0xFF
        except ValueError:
            v = 0
        if self._regs_bit_vars[i].get():
            v |= (1 << i)
        else:
            v &= ~(1 << i) & 0xFF
        # Avoid recursion: trace will refresh checkboxes from the new value.
        self.reg_val.set(f"0x{v:02X}")

    def _dump_regs_to_file(self) -> None:
        if not self.worker.is_open:
            messagebox.showinfo("Dump", "Open the camera first.")
            return
        try:
            start = self._parse_int(self.dump_start.get())
            end = self._parse_int(self.dump_end.get())
        except ValueError:
            messagebox.showerror("Dump", "Invalid hex range.")
            return
        if end < start:
            messagebox.showerror("Dump", "End < start.")
            return
        path = filedialog.asksaveasfilename(
            title="Save register dump",
            defaultextension=".cfg",
            initialfile=f"regdump_0x{start:04X}_0x{end:04X}.cfg",
            filetypes=[("Config", "*.cfg"), ("Text", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        thread = threading.Thread(
            target=self._dump_regs_worker, args=(start, end, path), daemon=True
        )
        thread.start()

    def _dump_regs_worker(self, start: int, end: int, path: str) -> None:
        total = end - start + 1
        ok = fail = 0
        self.dump_progress.configure(maximum=total, value=0)
        try:
            with open(path, "w", encoding="utf-8") as fout:
                fout.write(f"; sensor register dump 0x{start:04X} .. 0x{end:04X}\n")
                for i, addr in enumerate(range(start, end + 1)):
                    val = self.worker.read_sensor_reg(addr)
                    if val is None:
                        fail += 1
                    else:
                        ok += 1
                        fout.write(f"REG = 0x{addr:04X}, 0x{val & 0xFF:02X}\n")
                    if (i & 0x3F) == 0:
                        self.root.after(
                            0,
                            lambda v=i, o=ok, f=fail: (
                                self.dump_progress.configure(value=v),
                                self.dump_status.set(f"ok={o} fail={f}"),
                            ),
                        )
            self.root.after(
                0,
                lambda: (
                    self.dump_progress.configure(value=total),
                    self.dump_status.set(f"done: ok={ok} fail={fail} → {path}"),
                ),
            )
            self._log(f"register dump saved: {path} (ok={ok}, fail={fail})")
        except Exception as exc:
            self._log(f"dump error: {exc}")
            self.root.after(0, lambda: self.dump_status.set(f"error: {exc}"))

    # ----- snapshots -----------------------------------------------------
    def _save_snapshot(self) -> None:
        try:
            img = self.worker.frame_queue.queue[-1]
        except (IndexError, AttributeError):
            messagebox.showinfo("Snapshot", "No frame available; start streaming first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save snapshot",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("All", "*.*")],
        )
        if path:
            cv2.imwrite(path, img)
            self._log(f"snapshot saved: {path}")

    def _compute_histogram(self) -> None:
        with self.worker.lock:
            frames = list(self.worker.raw_history)
        if not frames:
            if not self.worker.is_streaming:
                self.hist_info.set("(start streaming to see histogram)")
            else:
                self.hist_info.set("(waiting for first frame…)")
            self.hist_canvas.delete("all")
            return
        # Average across the captured frames (float64 accumulator).
        acc = np.zeros_like(frames[0], dtype=np.float64)
        for f in frames:
            acc += f
        avg = acc / len(frames)
        if avg.ndim == 3 and avg.shape[2] >= 3:
            gray = cv2.cvtColor(avg.astype(frames[0].dtype), cv2.COLOR_BGR2GRAY)
        else:
            gray = avg.squeeze()
        flat = gray.ravel()
        # Pick a sensible histogram range: round the data peak up to the
        # next power-of-two so 10/12/14-bit sensors don't collapse all
        # bins into the leftmost few pixels of a 0..65535 axis.
        data_peak = int(flat.max()) if flat.size else 0
        if frames[0].dtype == np.uint8:
            max_v = 255
        elif frames[0].dtype == np.uint16:
            bits = max(8, max(1, data_peak).bit_length())
            max_v = (1 << bits) - 1
        else:
            max_v = max(data_peak, 1)
        bins = 256
        hist, _ = np.histogram(flat, bins=bins, range=(0, max_v + 1))
        c = self.hist_canvas
        c.delete("all")
        w = int(c.cget("width"))
        h = int(c.cget("height"))
        # Reserve a bottom strip for X-axis labels and a left strip for the
        # Y-axis labels so neither overlaps the bars.
        m_left = 28
        m_bot = 12
        plot_w = max(w - m_left - 2, 16)
        plot_h = max(h - m_bot - 2, 16)
        # Plot area background frame
        c.create_rectangle(m_left, 0, m_left + plot_w, plot_h,
                           outline="#444", width=1)
        peak = max(int(hist.max()), 1)
        # Logarithmic Y axis: one bin with millions of pixels won't squash
        # the rest into invisibility. Scale factor maps log(1+peak) -> plot_h.
        log_peak = float(np.log10(1.0 + peak))
        if log_peak <= 0:
            log_peak = 1.0
        # Y grid + labels at 1, peak^(1/3), peak^(2/3), peak (decades-like)
        for frac in (0.25, 0.5, 0.75, 1.0):
            y = plot_h - frac * plot_h
            c.create_line(m_left, y, m_left + plot_w, y, fill="#2a2a2a")
            tick_val = int(round(10 ** (frac * log_peak)))
            c.create_text(m_left - 2, y, anchor=tk.E, fill="#888",
                          font=("Consolas", 7), text=str(tick_val))
        # X grid + labels at 0, 25%, 50%, 75%, 100%
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            x = m_left + frac * plot_w
            c.create_line(x, 0, x, plot_h, fill="#2a2a2a")
            label = f"{int(round(frac * max_v))}"
            anchor = tk.N if 0 < frac < 1 else (tk.NW if frac == 0 else tk.NE)
            c.create_text(x, plot_h + 1, anchor=anchor, fill="#888",
                          font=("Consolas", 7), text=label)
        bar_w = max(plot_w / bins, 1.0)
        for i, v in enumerate(hist):
            if v <= 0:
                continue
            bh = (np.log10(1.0 + float(v)) / log_peak) * plot_h
            bh = max(bh, 1.0)
            x0 = m_left + i * bar_w
            x1 = x0 + bar_w
            c.create_rectangle(x0, plot_h - bh, x1, plot_h,
                               outline="", fill="#4ec9b0")
        c.create_text(m_left + 4, 2, anchor=tk.NW, fill="#aaa",
                      font=("Consolas", 8),
                      text=f"log10  peak={peak}")
        mn = float(flat.min()); mx = float(flat.max())
        mean = float(flat.mean()); std = float(flat.std())
        sat_hi = int((flat >= max_v - 1).sum())
        sat_lo = int((flat == 0).sum())
        npix = flat.size
        self.hist_info.set(
            f"frames avg: {len(frames)}\n"
            f"dtype : {frames[0].dtype}\n"
            f"shape : {gray.shape}\n"
            f"min   : {mn:.1f}   max : {mx:.1f}\n"
            f"mean  : {mean:.1f}\n"
            f"std   : {std:.1f}\n"
            f"sat hi: {sat_hi} ({100*sat_hi/npix:.2f}%)\n"
            f"sat lo: {sat_lo} ({100*sat_lo/npix:.2f}%)"
        )

    def _save_sequence(self) -> None:
        if not self.worker.is_streaming:
            messagebox.showinfo("Sequence", "Start streaming first.")
            return
        directory = filedialog.askdirectory(title="Folder for the sequence")
        if not directory:
            return
        n = max(1, int(self.batch_n.get()))
        threading.Thread(
            target=self._save_sequence_worker, args=(directory, n), daemon=True
        ).start()

    def _save_sequence_worker(self, directory: str, n: int) -> None:
        saved = 0
        last_seq = -1
        for _ in range(n * 10):  # bounded retries
            if saved >= n:
                break
            try:
                img = self.worker.frame_queue.queue[-1]
                seq = self.worker.stats.seq
            except (IndexError, AttributeError):
                time.sleep(0.02)
                continue
            if seq == last_seq:
                time.sleep(0.005)
                continue
            last_seq = seq
            fname = os.path.join(directory, f"frame_{saved:06d}.png")
            cv2.imwrite(fname, img)
            saved += 1
        self._log(f"sequence: saved {saved}/{n} frames in {directory}")

    # ----- info / log ----------------------------------------------------
    def _refresh_info(self) -> None:
        info = self.worker.dump_info()
        text = "\n".join(f"{k:14s}: {v}" for k, v in info.items()) or "(closed)"
        self.info_text.configure(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)
        self.info_text.insert(tk.END, text)
        self.info_text.configure(state=tk.DISABLED)

    def _log(self, msg: str) -> None:
        # Synchronous when called from Tk thread.
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg.rstrip() + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _log_threadsafe(self, msg: str) -> None:
        self._log_pending.put(str(msg))

    def _drain_logs(self) -> None:
        try:
            while True:
                msg = self._log_pending.get_nowait()
                self._log(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_logs)

    def _clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ----- periodic ------------------------------------------------------
    def _tick_preview(self) -> None:
        try:
            img = self.worker.frame_queue.get_nowait()
        except queue.Empty:
            self.root.after(33, self._tick_preview)
            return
        # Adaptive bit-depth scaling for uint16 mono frames (any sensor
        # delivering 10/12/14/16-bit data). We do NOT min-max stretch
        # (that hides the black
        # level); instead we pick the smallest power-of-two divisor that maps
        # the running peak into 0..255. A small EMA prevents per-frame
        # flicker while still adapting when the scene becomes brighter.
        if img.dtype == np.uint16:
            peak = int(img.max()) if img.size else 0
            ema = getattr(self, "_peak_ema", 0)
            ema = max(peak, int(ema * 0.97))
            self._peak_ema = ema
            ref = max(ema, 255)
            # number of bits needed to represent ref
            bits = max(8, ref.bit_length())
            shift = bits - 8
            img = np.clip(np.right_shift(img, shift), 0, 255).astype(np.uint8)
        elif img.dtype != np.uint8:
            img = cv2.normalize(img, None, 0, 255,
                                cv2.NORM_MINMAX).astype(np.uint8)
        # convert BGR→RGB for PIL, and downscale to canvas
        if img.ndim == 2:
            disp = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        elif img.shape[2] == 4:
            disp = cv2.cvtColor(img, cv2.COLOR_BGRA2RGB)
        else:
            disp = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        h, w = disp.shape[:2]
        # Preserve aspect ratio; allow downscale to fit, allow modest upscale
        # (up to 2x) when canvas is bigger than the frame.
        scale = min(cw / w, ch / h)
        scale = min(scale, 2.0)
        if scale < 1.0:
            disp = cv2.resize(disp, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_AREA)
        elif scale > 1.0:
            disp = cv2.resize(disp, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_NEAREST)

        pil = Image.fromarray(disp)
        self._photo_ref = ImageTk.PhotoImage(pil)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo_ref)
        self.root.after(20, self._tick_preview)

    def _tick_status(self) -> None:
        with self.worker.lock:
            s = Stats(**self.worker.stats.__dict__)
        if self.worker.is_open:
            self.live_info.set(
                f"Resolution : {s.width} x {s.height}\n"
                f"Streaming  : {'yes' if self.worker.is_streaming else 'no'}\n"
                f"Capture try: {s.attempts}\n"
                f"Frames OK  : {s.frames}\n"
                f"Timeouts   : {s.none_frames}\n"
                f"Dropped    : {s.dropped}  (frame.bad)\n"
                f"Missed     : {s.missed}  (seq gaps)\n"
                f"Conv. err  : {s.bad_convert}\n"
                f"Frame seq  : {s.seq}\n"
                f"FPS (SDK)  : {s.fps:6.2f}\n"
                f"FPS (meas) : {s.fps_measured:6.2f}\n"
                f"Bandwidth  : {s.bw_mb:6.2f} MB/s"
            )
        else:
            self.live_info.set("(camera closed)")
        self.root.after(500, self._tick_status)

    def _tick_histogram(self) -> None:
        try:
            if self.hist_auto.get() and self.worker.is_streaming:
                self._compute_histogram()
        except Exception as exc:
            self._log(f"histogram error: {exc}")
        self.root.after(1000, self._tick_histogram)

    def _tick_ae(self) -> None:
        try:
            if self.ae_enable.get() and self.worker.is_streaming:
                self._ae_step()
            elif not self.ae_enable.get():
                self.ae_status.set("(disabled)")
        except Exception as exc:
            self._log(f"AE error: {exc}")
            self.ae_status.set(f"err: {exc}")
        self.root.after(1000, self._tick_ae)

    def _ae_step(self) -> None:
        # Compute mean from latest raw frames
        with self.worker.lock:
            frames = list(self.worker.raw_history)
        if not frames:
            self.ae_status.set("waiting frame…")
            return
        f = frames[-1]
        if f.dtype == np.uint16:
            full = 65535
        elif f.dtype == np.uint8:
            full = 255
        else:
            full = float(f.max()) or 1
        mean = float(f.mean())
        target_pct = float(self.ae_target_pct.get())
        tol_pct = float(self.ae_tol_pct.get())
        target = full * target_pct / 100.0
        err_pct = (mean - target) / full * 100.0
        if abs(err_pct) <= tol_pct:
            self.ae_status.set(f"locked: mean={mean:.0f} ({100*mean/full:.1f}%)")
            return
        if "Exp(us)" not in self.control_widgets:
            self.ae_status.set("no Exp(us) ctrl")
            return
        # Read current values from sliders
        cur_exp = int(round(self.control_widgets["Exp(us)"].get()))
        # Proportional update with damping
        if mean <= 0:
            ratio = 4.0
        else:
            ratio = max(0.25, min(4.0, target / mean))
        new_exp = int(cur_exp * ratio)
        # Apply Exp slider's current `to=` cap (depends on framerate)
        scale = self.control_widgets["Exp(us)"]
        cap_max = int(scale.cget("to"))
        cap_min = int(scale.cget("from"))
        new_exp = max(cap_min, min(cap_max, new_exp))

        # Sensor analog gain register at 0x4009 (per the active profile):
        # valid 0..3 mapping to 1x / 2x / 4x / 8x. Read current value
        # directly from the sensor since this register is not exposed as
        # an SDK control. Override per-sensor via the JSON profile if
        # the address differs.
        cur_g = self.worker.read_sensor_reg(0x4009)
        new_g = cur_g if cur_g is not None else 0
        used_gain = False
        if self.ae_use_gain.get() and cur_g is not None:
            if new_exp == cap_max and ratio > 1 and cur_g < 3:
                new_g = cur_g + 1
                self.worker.write_sensor_reg(0x4009, new_g)
                used_gain = True
            elif new_exp == cap_min and ratio < 1 and cur_g > 0:
                new_g = cur_g - 1
                self.worker.write_sensor_reg(0x4009, new_g)
                used_gain = True
        if new_exp != cur_exp:
            scale.set(new_exp)
            self.worker.set_control(
                self._ctrl_meta["Exp(us)"].get("func", "Exp(us)"), new_exp
            )
        # Update value label too
        if "Exp(us)" in self.control_value_lbls:
            self.control_value_lbls["Exp(us)"].configure(text=str(new_exp))
        gain_str = ["1x", "2x", "4x", "8x"][min(new_g, 3)] if cur_g is not None else "?"
        self.ae_status.set(
            f"mean={mean:.0f} ({100*mean/full:.1f}%)\n"
            f"→ exp={new_exp}us  gain={gain_str}"
            + ("  ↑" if used_gain and ratio > 1 else "  ↓" if used_gain else "")
        )

    # ----- exit ----------------------------------------------------------
    def _on_close(self) -> None:
        try:
            self.worker.close()
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        # Use a modern theme when available
        ttk.Style().theme_use("vista" if os.name == "nt" else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
