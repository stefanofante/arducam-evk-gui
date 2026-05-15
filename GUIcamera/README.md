# GUIcamera

Tkinter GUI for Arducam EVK sensors.

## Structure

```
GUIcamera/
├── gui.py                  # Main application
├── config_manager.py       # Config/profile discovery
├── launch_gui.py           # Entry point
├── requirements.txt
├── configs/                # Camera .cfg files
├── sensors/                # Sensor .json profiles
└── utils/
    └── image_converter.py  # RAW -> BGR conversion
```

## Run

```bash
pip install -r requirements.txt
python launch_gui.py
```

## Add a sensor

1. Drop a `.cfg` file in `configs/` (set `TYPE = YourSensor`).
2. Drop a matching `.json` profile in `sensors/` (with `"match": ["YourSensor"]`).
3. Restart the GUI.

## Config file format

```ini
[camera parameter]
CFG_MODE  = 0
TYPE      = Mira220
SIZE      = 640, 480
BIT_WIDTH = 12
FORMAT    = 4, 0
I2C_MODE  = 2
I2C_ADDR  = 0xA8
```

## JSON profile format

```json
{
  "name": "Mira220",
  "match": ["MIRA220", "Mira220"],
  "registers": [
    { "group": "Exposure", "name": "Exp time",
      "addr": "0x100C", "kind": "u16be", "lo": 1, "hi": 65535 }
  ]
}
```

See `sensors/_template.json` for a full profile example.

## Debug

List discovered configs and profiles:

```bash
python -m config_manager
```
