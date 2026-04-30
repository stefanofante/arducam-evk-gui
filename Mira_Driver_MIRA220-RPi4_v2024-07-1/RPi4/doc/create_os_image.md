
# Download plain OS image
Download 64-bit Desktop version of Raspberry Pi OS image, file extension is "img.xz":
https://downloads.raspberrypi.org/raspios_arm64/images/raspios_arm64-2022-09-07/

# Flash OS image to RPI
Flash the image to an SD card of RPI.
Flash the image to an SD card of RPI.
To flash on eMMC of CM4, two extra things are needed:
1. Connect a jumper wire to J2 head pins "Disable eMMC boot"
2. Install rpiboot on host computer to detect the eMMC
Details at https://www.raspberrypi.com/documentation/computers/compute-module.html#setting-up-the-cmio-board

# Post-flash log in

## First time log in
For the first time log in, a monitor, keyboard, and mouse are needed.
Set user name `pi` and password `pi`.
Make sure the RPI is connected to the Internet, either wired or wireless, because subsequent steps require it.
Run "Raspberry Pi Configuration" to set a few things, and reboot afterwards.
- On the "Interfaces" menu, enable SSH, VNC, and I2C
- On the "Display" menu, change "headless" (VNC) resolution to 1280x720 or larger.
- On the "Performance" menu, change GPU memory size to 512 (MB)
After reboot, subsequent steps can be performed via remote log in (SSH, VNC).

## Extra step for CM4
Post-flash, CM4 requires an extra step to enable CAM1 connector:
```
sudo wget https://datasheets.raspberrypi.com/cmio/dt-blob-cam1.bin -O /boot/dt-blob.bin
```
Details at https://www.raspberrypi.com/documentation/computers/compute-module.html#quickstart-guide

# Install mira driver and Quadric driver

## Install ams_rpi_kernel
Clone or copy git repo `ams_rpi_kernel` to the RPI.
The git repo is at https://gittf.ams-osram.info/cis_solutions/raspberry_evk/ams_rpi_kernel .
Install prerequisites.
```
sudo apt install git bc bison flex libssl-dev make
```
Reference: Details: https://www.raspberrypi.com/documentation/computers/linux_kernel.html#building-the-kernel-locally .
Inside the repo, execute the following command.
```
./build_native.sh
```
Post installation, add a new line in `/boot/config.txt` to load the overlay of mira sensor. Depending on the sensor type, the overlay name is `mira220`, `mira220color`, `mira050`, `mira050color`.
```
dtoverlay=mira220
```

## Install ams_rpi_software
Clone git repo `ams_rpi_software` at https://gittf.ams-osram.info/cis_solutions/raspberry_evk/ams_rpi_software .
Install pre-requisites:
```
./install_requirements.sh
```
Run installation script:
```
./build_all.sh
```

## (If needed) Install quadric_driver
Clone git repo `quadric_driver` at https://gittf.ams-osram.info/cis_solutions/raspberry_evk/quadric_driver .
Follow the README files to install the drivers and demo pipeline.
For Quadric demo, copy the demo pipeline and doc to desktop
```
cp face_pipeline_picamera2.py ./face_detauth_pipeline/
cp ./face_detauth_pipeline ~/Desktop/
cp README_demo.md ~/Desktop/
```

# Create an OS image
This is a reversed step as flashing. For CM4, the J2 jumper step and rpiboot step are needed.
On Windows, a handy program for creating an image is `Win32DiskImager`. There are instructions online for its usage. One thing worth mentioning is that, the SD card or eMMC are mounted as two partitions on Windows, such as `D:` and `E:`. Let `Win32DiskImager` clone the first partition, such as `D:`, and it will automatically clone the second partition `E:` as well.

## Shrinking the image
Shrink the image on a Linux machine using PiShrink: https://github.com/Drewsif/PiShrink
And compress the image into "xz" format using the `-Z` argument. Below is an example.
```
sudo pishrink.sh -Z pi.img
```


