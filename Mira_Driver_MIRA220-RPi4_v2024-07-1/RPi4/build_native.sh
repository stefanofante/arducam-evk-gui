#!/usr/bin/env bash
set -e

cd "$( dirname "${BASH_SOURCE[0]}" )"

TOPDIR=${PWD}

echo "Building from DIR: ${TOPDIR}"

KERNELRELEASE=${KERNELRELEASE:=$(uname -r)}

echo "Building for kernel version: ${KERNELRELEASE}"

if [ ! -d "/usr/src/linux-headers-${KERNELRELEASE}" ]
then
	echo "Kernel header /usr/src/linux-headers-${KERNELRELEASE} not found."
	echo "Try of obtain it via apt."
	sudo apt-get install raspberrypi-kernel-headers
	if [ ! -d "/usr/src/linux-headers-${KERNELRELEASE}" ]
	then
		echo "Failed to install kernel header. Exiting."
		exit 1
	fi
fi

echo "#############################"
echo "# Creating deb pkg folder"
echo "#############################"

# module name should not have underscore
PKGNAME="mira-driver"
# release version
PKGVER="0.1.5"
# arm64 for 64bit or armhf for 32bit
PKGARCH=$(dpkg --print-architecture)

PKGDIR=$TOPDIR/$PKGNAME"_"$PKGVER"-1_"$PKGARCH

echo "Creating deb package at $PKGDIR"

mkdir -p $PKGDIR/DEBIAN

echo "Package: $PKGNAME
Version: $PKGVER
Architecture: $PKGARCH
Maintainer: Zhenyu Ye <zhenyu.ye@ams-osram.com>
Description: Mira device tree and driver for RPI.
 It contains mira220, mira220color, mira050, mira050color, mira016, mira130, poncha110." > $PKGDIR/DEBIAN/control

MODULEDIR=$PKGDIR/usr/lib/modules/$KERNELRELEASE/kernel/drivers/media/i2c
mkdir -p $MODULEDIR
mkdir -p $PKGDIR/boot/overlays

echo "#############################"
echo "# Add driver and dtbo to deb"
echo "#############################"

# Build dtbo and driver
(cd poncha110/src && make)
# Install driver to deb package folder
(cd poncha110/src && make INSTALL_MOD_PATH=$PKGDIR install)
# Install dtbo to deb package folder
(cd poncha110/src && cp poncha110.dtbo poncha110color.dtbo $PKGDIR/boot/overlays/)
# Cleanup artifacts from source folder
(cd poncha110/src && make clean)


# Build dtbo and driver
(cd mira220/src && make)
# Install driver to deb package folder
(cd mira220/src && make INSTALL_MOD_PATH=$PKGDIR install)
# Install dtbo to deb package folder
(cd mira220/src && cp mira220.dtbo mira220color.dtbo $PKGDIR/boot/overlays/)
# Cleanup artifacts from source folder
(cd mira220/src && make clean)

# Build dtbo and driver
(cd mira050/src && make)
# Install driver to deb package folder
(cd mira050/src && make INSTALL_MOD_PATH=$PKGDIR install)
# Install dtbo to deb package folder
(cd mira050/src && cp mira050.dtbo mira050color.dtbo $PKGDIR/boot/overlays/)
# Cleanup artifacts from source folder
(cd mira050/src && make clean)

# Build dtbo and driver
(cd mira016/src && make)
# Install driver to deb package folder
(cd mira016/src && make INSTALL_MOD_PATH=$PKGDIR install)
# Install dtbo to deb package folder
(cd mira016/src && cp mira016.dtbo $PKGDIR/boot/overlays/)
# Cleanup artifacts from source folder
(cd mira016/src && make clean)

# Build dtbo and driver
(cd mira130/src && make)
# Install driver to deb package folder
(cd mira130/src && make INSTALL_MOD_PATH=$PKGDIR install)
# Install dtbo to deb package folder
(cd mira130/src && cp mira130.dtbo $PKGDIR/boot/overlays/)
# Cleanup artifacts from source folder
(cd mira130/src && make clean)



echo "#############################"
echo "# Build and install deb pkg"
echo "#############################"

echo "Building $PKGDIR.deb"
dpkg-deb --build --root-owner-group $PKGDIR

echo "Backing up mira dtbo in /boot/overlays"
for file in `ls /boot/overlays/mira*.dtbo`; do
	echo "Moving ${file} to ${file}bak"
	sudo mv ${file} ${file}bak
done
for file in `ls /boot/overlays/poncha*.dtbo`; do
	echo "Moving ${file} to ${file}bak"
	sudo mv ${file} ${file}bak
done
echo "Installing $PKGDIR.deb"
sudo dpkg -i $PKGDIR.deb

echo "Device trees installed to /boot/overlays"
echo "Drivers installed to /usr/lib/modules/$KERNELRELEASE/kernel/drivers/media/i2c"

echo "Post installation: rebuild dependency modules to make modules loaded by kernel"
sudo depmod -v $KERNELRELEASE

