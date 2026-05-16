# arducam-EVK-GUI — Arducam EVK with GUI Camera

This project is a fork of [ArduCAM/ArduCam_EVK_Demo](https://github.com/ArduCAM/ArduCam_EVK_Demo)
that adds a unified graphical interface for the Arducam EVK SDK.

## Purpose

The upstream EVK demo ships a collection of standalone command-line examples
(capture, controls, log, list devices, register dump, ...). This fork:

1. Groups all of those features into a single Tkinter GUI under
   [`GUIcamera/`](GUIcamera/README.md).
2. Extends the original feature set with interactive sensor register
   inspection and editing, driven by a sensor-specific JSON profile.
3. Decouples the GUI from any specific sensor: camera parameters live in
   `.cfg` files and the register map lives in `.json` profiles, so adding
   a new sensor does not require code changes.

The tool was developed primarily to test the **ams OSRAM Mira220** image
sensor and to have a fast, easy-to-use development aid for bring-up,
parameter tuning and register-level debug.

## Repository Layout

```
.
├── GUIcamera/          # New: unified Tkinter GUI (this fork)
│   ├── gui.py
│   ├── config_manager.py
│   ├── configs/        # Camera .cfg files (one per sensor mode)
│   └── sensors/        # Sensor register profiles (.json)
├── c/                  # Upstream C demos
├── c++/                # Upstream C++ demos
├── python/             # Upstream Python demos
├── evk_sdk/            # Arducam EVK SDK (bundled headers + prebuilt libs)
├── doc/                # Environment install guides (Windows, Linux)
└── sensorDatasheets/   # Reference datasheets used to build the profiles
```

## Bundled Arducam EVK SDK

The [`evk_sdk/`](evk_sdk) folder contains a snapshot of the official
**Arducam EVK SDK** (headers under `include/` and prebuilt libraries
plus CMake package files under `lib/`). It is **not** a product of this
fork; it is redistributed as-is from upstream so that the bundled C and
C++ projects under [`c/`](c) and [`c++/`](c++) can be compiled out of
the box, without having to install the SDK system-wide.

- Source: [ArduCAM/ArduCam_EVK_SDK](https://github.com/ArduCAM/ArduCam_EVK_SDK)
- Bundled version: **1.0.7** (see
  [`evk_sdk/include/arducam/version.h`](evk_sdk/include/arducam/version.h))
- License: governed by the original Arducam EVK SDK license terms

The CMake projects in `c/` and `c++/` locate the SDK through the
`arducam_evk_sdk` / `arducam_evk_cpp_sdk` CMake packages shipped under
`evk_sdk/lib/cmake/`. To refresh the bundled SDK, replace the contents
of `evk_sdk/` with a newer release from the upstream repository above.

## Quick Start

The fastest way to evaluate a sensor is the GUI:

```bash
cd GUIcamera
pip install -r requirements.txt
python launch_gui.py
```

See [`GUIcamera/README.md`](GUIcamera/README.md) for details on adding new
sensors and the configuration/profile format.

For the original command-line demos refer to:

- [`c/README.md`](c/README.md)
- [`c++/README.md`](c++/README.md)
- [`python/README.md`](python/README.md)

## SDK Installation

The Arducam EVK SDK (USB driver and runtime libraries) must be installed
before either the GUI or the upstream demos can talk to the hardware.
The headers and prebuilt libraries required to **compile** the C/C++
demos are already bundled under [`evk_sdk/`](evk_sdk) (see the section
above), so only the USB driver and the Python bindings have to be
installed system-wide:

- Windows: [`doc/windows_environmental_install.md`](doc/windows_environmental_install.md)
- Linux: [`doc/linux_environmental_install.md`](doc/linux_environmental_install.md)

## Author

**Stefano Fante** — CTO, STLINE srl — Treviso, Italy
[www.stline.it](https://www.stline.it) ·
[LinkedIn](https://www.linkedin.com/in/stefano-fante-2398772) ·
[GitHub @stefanofante](https://github.com/stefanofante)

Electronic engineer with 30+ years of end-to-end ownership across embedded
hardware, firmware and software for certified medical devices.

**Expertise**

- *Real-time & embedded:* ARM Cortex-M (STM32 F1/F3/F4/H7, NXP i.MX RT),
  ESP32 / ESP32-S3, FreeRTOS (static allocation), Zephyr, embedded Linux,
  bare-metal, Windows/Linux drivers.
- *Signal chain & acquisition:* sub-microvolt low-noise analog front-ends,
  multi-board synchronized acquisition, biomedical signal processing
  (ABR/ASSR, audiometry, OAE), IMU sensor fusion (ESKF/EKF), laser
  interferometry.
- *Hardware & high-speed:* precision mixed-signal design, up to 10-layer
  PCBs (Altium, KiCad), USB 2.0/3.0, MIPI, LVDS, CMOS / machine-vision
  pipelines, opto-electronic integration (pupil tracking, confocal,
  Scheimpflug, fundus).
- *Medical & regulatory:* IEC 60601, IEC 62304, ISO 13485 / MDSAP, EU MDR,
  FDA 510(k).

## License

Released under the [MIT License](LICENSE).

The upstream `ArduCam_EVK_Demo` code (under [`c/`](c), [`c++/`](c++) and
[`python/`](python)) and the bundled `ArduCam_EVK_SDK` (under
[`evk_sdk/`](evk_sdk)) retain their original Arducam license terms;
refer to the per-folder notices for details.
