# Version v0.1.12

New features:
- Add basic Mira016 driver.
- Add network-over-usb capability.
- Enable variable fps for mira050.
- Special V4L2 commands to access I2C slaves with user-defined I2C address.
- Experimental: Python interface for mira register access.
- Experimental: remote access via Python or web interface.

# Version v0.1.11

Bug fixes:
- Fix driver code to compile with latest RPI Linux kernel and tags used by pi-gen.

Known issues:
- From now on, Mira driver is no longer compatible to Linux kernel < 6.1.21.

# Version v0.1.10

New features:
- Add LED control to Mira050 driver.

Bug fixes:
- Fix Mira050 driver duplicated modes and wrong fps.

# Version v0.1.9

Bug fixes:
- Fix bug when integrating latest libcamera tag v0.0.2.

# Version v0.1.7 and v0.1.8

New features:
- Enable 8/10/12 bit modes for Mira050.
- Mira050 12 bit mode uses coarse gains 1x/2x/4x.
- Mira050 8 and 10 bit modes use fine gain.

Bug fixes:
- Fix --list-cameras bug on Mira050.

# Version v0.1.6

New features:
- Bump to `raspios_arm64-2022-09-07`, kernel 5.15.61-v8+, tag 1.20220830.
- Add script `build_native.sh` to build the driver natively on an RPI.

Bug fixes:
- Fix module probe order problem by including pmic module inside mira220/050 module.
- Fix over exposure problem of Mira220, and strange exposure of Mira050.


# Version v0.1.5

New features:
- Support mira220color and mira050color.
- Mira220 driver supports two modes: (1) 640x480 120fps; (2) 1600x1400 30fps. BPP:8/10/12.

Bug fixes:
- Fix Mira220 bring up problem by increasing reset time.

# Version v0.1.4

New features:
- Add Mira050 support. Reduce Mira220 power consumption by keeping reset signal high most of the time.

# Version v0.1.3

Bug fixes:
- Increase reset timing to avoid instable reset.

# Version v0.1.2

New features:
- Use simplified flags `OSBIT` and `RPIHW` in `common/build.sh` to configure 32&64bit OS, and HW version of RPI.

Bug fixes:
- The RPI Linux kernel tag rpi-5.15.y is unstable. Use commit id instead. Tested on 32&64bit OS.

# Version v0.1.1

New features:
- Add 64-bit OS support (tested by overwriting an 32bit OS).

# Version v0.1.0

New features:
- Basic mode: 1600x1400 with 8 bits per pixel.

Known issues:
- Each time a new image or video is captured, the mira220pmic module requires reloading.

