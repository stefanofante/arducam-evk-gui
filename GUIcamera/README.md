# GUIcamera

Tkinter front-end for the Arducam EVK SDK. Discovers the available camera
configurations at start-up and lets the user pick the sensor and the
operating mode before opening the device. Once the camera is streaming the
GUI shows a live preview together with capture statistics, and exposes the
sensor register map declared in the matching JSON profile.

The application is part of the [EVK](../README.md) fork; the upstream
ArduCam_EVK_Demo only ships individual command-line examples.

## Features

- USB device enumeration and selection
- Sensor / configuration picker driven by the contents of `configs/`
- Live preview with frame counter and measured FPS
- Capture worker running on a dedicated thread (UI never blocks on USB I/O)
- Single-register read / write helpers
- In-app log console for SDK and application messages
- New sensors plug in by adding files in `configs/` and `sensors/`; no
  Python change required

## Structure

```
GUIcamera/
├── gui.py                  # Tkinter application
├── config_manager.py       # .cfg parser and .json profile loader
├── launch_gui.py           # Convenience launcher (sets sys.path)
├── requirements.txt        # Runtime Python dependencies
├── README.md
├── configs/                # Camera .cfg files (one per sensor mode)
│   └── Mira220_640x480.cfg
├── sensors/                # Sensor register profiles (.json)
│   ├── _template.json      # Schema reference for new profiles
│   └── mira220.json
└── utils/
    ├── __init__.py
    └── image_converter.py  # RAW -> BGR conversion (cv2/numpy)
```

## Requirements

- Python 3.8 or newer
- Arducam EVK SDK (Python bindings + USB driver) installed and on the
  search path; see [`../doc/`](../doc) for platform install guides
- Python packages listed in [`requirements.txt`](requirements.txt)

## Run

```bash
cd GUIcamera
pip install -r requirements.txt
python launch_gui.py
```

`launch_gui.py` adds the `GUIcamera/` directory to `sys.path` and then
calls `gui.main()`. Running `python gui.py` from inside the directory
works as well.

## Workflow

1. The GUI scans `configs/` at start-up. Each `.cfg` file is parsed to
   read its `TYPE`, `SIZE`, `BIT_WIDTH` and `FORMAT` fields. Files are
   grouped by sensor type.
2. The matching JSON profile is looked up in `sensors/` using the `match`
   field (case-insensitive comparison against the `TYPE` value).
3. The user picks the sensor, then a specific configuration, then the USB
   device, and finally clicks **Open** and **Start**.
4. The capture loop runs in a background thread, pushing the latest frame
   onto a bounded queue. The Tk main loop drains the queue and updates
   the preview canvas without blocking.

## Adding a New Sensor

1. Drop the `.cfg` file produced by the Arducam tooling into `configs/`.
   The `TYPE` field is the key that ties the configuration to a profile.
2. Create a matching JSON profile in `sensors/` (use `_template.json` as a
   starting point). The `match` list must contain the same `TYPE` value
   (or any case-insensitive alias).
3. Restart the GUI. The new sensor and its configurations appear in the
   sensor selector automatically.

## Configuration File Format

`.cfg` files follow the format already used by the upstream Arducam SDK.
Only the fields consumed by the GUI metadata parser are listed here; the
SDK itself accepts additional sections (per-control function blocks,
register initialisation, board parameters, ...).

```ini
[camera parameter]
CFG_MODE  = 0          ; 0 = user-defined / 1 = use this file as a script
TYPE      = Mira220    ; sensor identifier; must match a profile alias
SIZE      = 640, 480   ; active width, active height
BIT_WIDTH = 12         ; output bit depth
FORMAT    = 4, 0       ; image format, color mode (Bayer order for RAW)
I2C_MODE  = 2          ; address/data widths for sensor CCI
I2C_ADDR  = 0xA8       ; sensor I2C address (8-bit, including R/W bit)
```

## JSON Profile Format

```json
{
  "name": "Mira220",
  "description": "Optional one-liner shown in the UI",
  "match": ["MIRA220", "Mira220"],
  "registers": [
    {
      "group": "Exposure / framing",
      "name":  "Exposure (rows)",
      "addr":  "0x100C",
      "kind":  "u16be",
      "lo": 1, "hi": 65535,
      "desc": "Integration time in row periods",
      "help": "Longer description shown in the help dialog."
    }
  ],
  "guide": "Optional multi-paragraph quick reference.",
  "tabs":  { "registers": "Hint shown on the Registers tab." }
}
```

Recognised register `kind` values include `u8`, `u16be`, `u16le`, `u24be`,
`bool`, `enum`, `ro` and `multi` (bit-field spread across several
registers). See [`sensors/_template.json`](sensors/_template.json) for the
full set of supported fields.

## Debugging

Print the list of discovered configurations and profiles without opening
the GUI:

```bash
python -m config_manager
```

Application and SDK log messages are also visible in the **Info / Log**
tab while the GUI is running.

## Author

Stefano Fante — STLINE srl. Released under the [MIT License](../LICENSE).
