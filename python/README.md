# Directory Structure

```
.
├── function       # python demo source code (independent use cases for each function)
├── project        # python demo project (integrated use cases for common functions)
├── sensors/       # JSON sensor profiles loaded by gui_camera.py
└── gui_camera.py  # all-in-one Tkinter GUI (recommended entry point)
```

# Introduction

This is a demo for reading and displaying images with sync/async mode.

## All-in-one GUI (recommended)

`gui_camera.py` merges every example in this folder (open / init / start /
stop, controls, transfer options, mode switching, sensor register dump,
single register read & write, snapshots, sequence saving, SDK logging…)
into a single Tkinter window. The GUI is **sensor-agnostic**: any cfg
that ships with the workspace is auto-detected and matched against the
JSON profiles under `python/sensors/`.

### Tabs

* **Preview** – live canvas with adaptive 16→8 bit shifting (no min-max
  stretch), live stats, snapshot / sequence saving, log-scale histogram
  with X (pixel value) and Y (count) axes, and a software auto-exposure
  loop with optional auto-gain.
* **Controls** – auto-generated sliders for every cfg-declared control
  (e.g. `Framerate`, `Exp(us)`); the exposure cap follows the framerate.
* **Sensor regs** – friendly per-register editor driven by the active
  JSON profile. Supports register kinds `u8`, `bool`, `enum`, `ro`,
  `u16be` / `u16le` / `u24be`, `multi` (bit-split fields with RMW
  writes) and `computed` (formula over multiple reads, e.g. die
  temperature). Toolbar lets you switch profile, filter, group, and
  toggle between combined multi-byte view and per-byte view. Each row
  has a `i` button that opens a rich datasheet-style help dialog.
* **Registers (raw)** – raw address/value picker with 8 bit checkboxes
  and live description from the active profile.
* **Advanced** – USB transfer size, sensor mode switch, SDK log level
  and log file.
* **Info** – `dump_info()` output and an in-app SDK log console.

### Sensor profiles

Profiles live in `python/sensors/*.json`. Each file declares `name`,
`description`, optional `match` patterns (matched against cfg `TYPE` or
filename), `tabs` hints, a long-form `guide`, and a `registers` array.
Add a new sensor by dropping a JSON file in that folder; see
`sensors/_template.json` and `sensors/README.md` for the schema.

### Run

```shell
cd python
pip install -r requirements.txt
pip install Pillow   # GUI-only
python gui_camera.py
```

## Installation dependence

<!-- git clone https://github.com/ArduCAM/ArduCAM_USB_Camera_Shield_Cpp_Demo.git -->
<!-- cd ArduCAM_USB_Camera_Shield_Cpp_Demo -->

```shell
cd python
pip install -r requirements.txt
```

## Functions

### List Devices

```shell
# run
python function/list_devices.py
```

### Open

Open Basic

```shell
# show help
python function/open_basic.py --help
# run
python function/open_basic.py -c <config>
```

Open Specific Device

```shell
# show help
python function/open_device.py --help
# run
python function/open_device.py -c <config> -d <device_index>
```

Open Advanced

```shell
# show help
python function/open_advanced.py --help
# run
python function/open_advanced.py -c <config> --dma
```

Set Transfer Option

```shell
# show help
python function/set_transfer_option.py --help
# run
python function/set_transfer_option.py -c <config> -t <transfer_count> -s <transfer_size> -n <number_of_frames>
```

### Log

Log Basic

```shell
# run
python function/log_basic.py
```

Set the environment variable `ARDUCAM_LOG_LEVEL` to change the log level before running.
The `ARDUCAM_LOG_LEVEL` value can be `off`, `error`, `warn`, `info`, `debug`, `trace`.

```shell
# run with environment variable
ARDUCAM_LOG_LEVEL=off python function/log_basic.py
```

```shell
# run with environment variable
ARDUCAM_LOG_LEVEL=trace python function/log_basic.py
```

Log Callback

```shell
# run
python function/log_callback.py
```

Log File

```shell
# run
python function/log_file.py
```

### Capture

Capture Sync

```shell
# show help
python function/capture.py --help
# run
python function/capture.py -c <config> -n <number_of_frames>
```

Capture Async

```shell
# show help
python function/capture_async.py --help
# run
python function/capture_async.py -c <config> -d <delay_in_seconds>
```

### Utils

Show fps

```shell
# show help
python function/show_fps.py --help
# run
python function/show_fps.py -c <config>
```

List All Modes in Binary Config

> Make sure you have a camera connected

```shell
# show help
python function/list_mode.py --help
# run, and list all modes in binary config
python function/list_mode.py -c <config> -l
# run, and switch to a specific mode
python function/list_mode.py -c <config> -i <mode_id>
```

Controls

> Liat all controls

```shell
# show help
python function/controls.py --help
# run
python function/controls.py -c <config>
```

## Project

### Run sync demo

Show help

```shell
python project/sync.py --help
```

Run

```shell
python project/sync.py -c <path/config-file-name>
```

### Run async demo

Show help

```shell
python project/async.py --help
```

Run

```shell
python project/async.py -c <path/config-file-name>
```

### Run time source demo

Show help

```shell
python project/time_source.py --help
```

Run

```shell
python project/time_source.py -c <path/config-file-name>
```
