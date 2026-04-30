# Prerequisites:
- Raspberry Pi 4 (RPI4). It is tested on RPI4, but should also support Raspberry Pi Compute Module 4 or Raspberry Pi 3.
- Raspberry Pi OS Bullseye 64bit (recommended) or 32bit. See compilation notes below to set the OS version.
- Mira220 sensor board V3.0 or Mira050 sensor board V1.0.
## If cross-compiling with a host x86 machine
- Host machine running Ubuntu 18.04 or later.
- Host has the tool chain needed to compile a standard Raspberry Pi Linux Kernel. Issue this command `sudo apt install git bc bison flex libssl-dev make libc6-dev libncurses5-dev crossbuild-essential-armhf crossbuild-essential-arm64`. Refer to [Raspberry Pi doc](https://www.raspberrypi.com/documentation/computers/linux_kernel.html#cross-compiling-the-kernel) for further details. Preferably, make sure the host has the required tool by compiling a standard Raspberry Pi kernel as stated in the Raspberry Pi doc.
## If compiling natively on the raspberry pi
- The Raspberry Pi has 64-bit Desktop version of Raspberry Pi OS image [link](https://downloads.raspberrypi.org/raspios_arm64/images/raspios_arm64-2022-09-07/), or (not recommended) 32-bit version [link](https://downloads.raspberrypi.org/raspios_full_armhf/images/raspios_full_armhf-2022-09-07/).
- The Raspberry Pi has the required tools. Log into the RPI and issue this command `sudo apt install git bc bison flex libssl-dev make`. Refer to [Raspberry Pi doc](https://www.raspberrypi.com/documentation/computers/linux_kernel.html#building-the-kernel-locally) for further details.

# Compilation and installation:
## If cross-compiling with a host x86 machine
- Clone or copy this repo to an x86 machine. Make sure the host machine has prerequisites mentioned in the previous section.
- Before compilation, configure whether 64bit OS or 32bit OS is built. Open the file `common/build.sh` and edit the variable `OSBIT` to be `64` or `32`. This variable controls a few build options. For example, it controls the `KERNEL` option that points to the actual kernel file used in the RPI. By default, 32-bit RPI 4 uses `kernel7l.img`, 64-bit RPI 4 uses `kernel8.img`.
- Before compilation, configure the RPI hardware type. Open the file `common/build.sh` and edit the variable `RPIHW` to be `RPI4B` or `RPI3B`. This variable controls what kind of build config is used.
- Open the file `common/build.sh` and point `RPI_KERNEL_INSTALL_DIR=` to the actual location that the Raspberry Pi SD card is mounted on the Ubuntu host. Alternative to mounting the SD card by physically plugging it on to the Ubuntu host, it is possible to mount it via ssh by the command `sudo sshfs -o sftp_server="/usr/bin/sudo /usr/lib/openssh/sftp-server" pi@IP_OF_PI:/ /media/pi` where `IP_OF_PI` is the IP address of pi and `/media/pi` is an example mount point on the host.
- Run `build_all.sh`. The script performs the following: (1) Download a specific version of Linux kernel source; (2) Apply Mira220 and Mira050 related source code and patches; (3) Prepare compilation script; (4) Performs actual compilation and installation. To re-run the `build_all.sh` for a specific step, comment out the parts that are not relevant.
## If compiling natively on the raspberry pi
- Clone or copy this git repo to the target RPI. Make sure the RPI has the prerequisites mentioned in the previous section.
- Run `build_native.sh`. It builds the drivers and device trees into the Debian package, and then installs the Debian package to the local RPI.

### Installing via a compiled Debian package
The Debian package, with a name like `mira-driver_0.1.5-1_arm64.deb`, is built for a specific architecture (check via `dpkg --print-architecture`) and a specific kernel version (check via `uname -r`) of the RPI. If other RPIs have the same architecture and the same kernel version, users can simply copy the Debian package over to other RPIs and install the driver via three steps described below.

Step 1: Issue the command below to move current version of Mira sensor device trees (if exist) to new file names. This step is needed because the next step (dpkg) can only create new files but cannot overwrite existing files on `boot` folder (FAT partition).
```
for file in `ls /boot/overlays/mira*.dtbo`; do echo "Moving ${file} to ${file}bak"; sudo mv ${file} ${file}bak; done
```
Step 2: Install the device trees and driver files via dpkg command.
```
sudo dpkg -i mira-driver_0.1.5-1_arm64.deb
```
Step 3: Perform post-installation update.
```
sudo depmod
```

## Configuration
- Post-installation, log on to the Raspberry Pi, add a new line to `/boot/config.txt`. Depending on whether Mira220 mono or Mira220 color or Mira050 mono is connected, this new line will be either `dtoverlay=mira220` for Mira220 mono, or `dtoverlay=mira220color` for Mira220 color, or `dtoverlay=mira050` for Mira050 mono (pick one and only one!). This line tells the RPI to load the corresponding driver at boot time.
- Reboot to let the configuration take effect.

# Tests:
- Test whether installation script successfully copies files. After reboot, issue the command `uname -a` and the printed out timestamp of the kernel should match the time that you compile it.
- Test whether Mira220 or Mira050 driver is loaded. As mentioned above, the line `dtoverlay=...` should be added to `/boot/config.txt` on RPI. After reboot, the `dmesg` command should print out logs with keywords `MIRA220` and `MIRA220PMIC` for Mira220 driver; `MIRA050` and `MIRA050PMIC` for Mira050 driver;. For a 64 bit kernel - it could be that you need to add `kernel=kernel8.img` to `config.txt`
- Test whether the power management IC driver module (MIRA220PMIC/MIRA050PMIC) is working. The green LED on the sensor board should be turned on.
- To further test the actual driver module (MIRA220/MIRA050), please refer to a separate repo `ams_rpi_software` and follow instructions from there.

# Post-installation:
- Install other custom driver modules or software if needed. For example, the Quadric Dev Kit driver (`thor`) is located in a separate repo [link](https://gittf.ams-osram.info/cis_solutions/raspberry_evk/quadric_driver).
- Instructions on creating a custom OS image from a plain OS image are described in [doc/create_os_image.md](doc/create_os_image.md).

# Release workflow
Two repositories, `ams_rpi_kernel` and `ams_rpi_software`, are released. Assumption is that, both repositories have a git tag for the release version, such as `v0.1.2`. Below are commands that create a specific release from the tag.
```
# Export the release tag version, for example, v0.1.2
export RELEASE_TAG=v0.1.2
# Create release pacakge ams_rpi_kernel_${RELEASE_TAG}.tar.gz
cd ams_rpi_kernel
git archive --prefix=ams_rpi_kernel/ -o ams_rpi_kernel_${RELEASE_TAG}.tar.gz ${RELEASE_TAG}
# Create release for ams_rpi_software_${RELEASE_TAG}.tar.gz
cd ams_rpi_software
git archive --prefix=ams_rpi_software/ -o ams_rpi_software_${RELEASE_TAG}.tar.gz ${RELEASE_TAG}
```
