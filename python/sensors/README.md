# Sensor register profiles

Drop one JSON file per sensor in this folder. The GUI auto-discovers them at
startup and populates the **Sensor** combobox in the *Sensor regs* tab.

## File format

```json
{
  "name": "MySensor",
  "description": "optional one-liner shown above the table",
  "match": ["MYSENSOR", "MY-SENSOR-1234"],
  "guide": "Multi-line free text shown by the 'Guide' button in the Sensor regs tab. Use it for sensor overview, key register pairs, tips, datasheet references, etc.",
  "tabs": {
    "preview":  "Hint shown at the top of the Preview tab.",
    "controls": "Hint shown at the top of the Controls tab.",
    "advanced": "Hint shown at the top of the Advanced tab.",
    "info":     "Hint shown at the top of the Info / Log tab."
  },
  "registers": [
    {
      "group": "Acquisition",
      "name": "Exposure",
      "addr": 12,
      "kind": "u16be",
      "lo": 1,
      "hi": 65535,
      "desc": "tooltip-style short description",
      "help": "longer multi-paragraph description shown by 'i'"
    },
    {
      "group": "Control",
      "name": "Mode flags",
      "addr": "0x1100",
      "kind": "u8",
      "desc": "Bitfield register",
      "bits": [
        {"lsb": 0, "width": 1, "name": "EN",   "desc": "enable"},
        {"lsb": 1, "width": 2, "name": "MODE", "desc": "00=A, 01=B, 10=C"},
        {"lsb": 7, "width": 1, "name": "BUSY", "desc": "read-only status"}
      ]
    },
    {
      "group": "Gain",
      "name": "Analog gain",
      "addr": 16393,
      "kind": "enum",
      "choices": {"1x": 0, "2x": 1, "4x": 2, "8x": 3},
      "desc": "..."
    }
  ]
}
```

### Supported `kind` values

| kind     | layout                                            |
|----------|---------------------------------------------------|
| `u8`     | single 8-bit register, decimal entry              |
| `u16be`  | two regs, MSB at `addr+0`, LSB at `addr+1`        |
| `u16le`  | two regs, LSB at `addr+0`, MSB at `addr+1`        |
| `u24be`  | three regs, MSB first                             |
| `bool`   | single bit (0/1)                                  |
| `enum`   | single byte with `choices: {label: value}`        |
| `ro`     | read-only (status / version registers)            |

### Notes

- `addr` accepts decimal or strings like `"0x100C"` (JSON5 not required).
- Profiles are loaded lazily; switch sensor from the GUI without restart.
- Optional per-register `bits` array adds labels under the 8 bit
  checkboxes in the **Registers (raw)** tab. Each item: `lsb` (0..7),
  `width` (>=1), `name` (short label shown under the checkbox), `desc`
  (longer description, future use).
- Optional `tabs.registers` key feeds a hint banner shown at the top of
  the **Registers (raw)** tab.
- Optional `match` array lists case-insensitive substrings; when the
  user opens a `.cfg` whose `TYPE = ...` field (or filename) contains
  any of them, the GUI auto-switches to this profile. If `match` is
  omitted the GUI also tries the profile `name` itself against
  `TYPE` / filename.
- The shipped `mira220.json` is regenerated from `mira220_regs.py` via
  `python _export_mira220.py` (one-shot helper) — edit the JSON
  directly going forward.
