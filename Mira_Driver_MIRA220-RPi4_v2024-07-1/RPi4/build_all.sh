#!/usr/bin/env bash
set -e

cd "$( dirname "${BASH_SOURCE[0]}" )"

echo "${PWD}"

# Use a very new tag, to sync with pi-gen.
TAG="1.20230405"
# Previous kernel version is raspios_arm64-2022-09-07
# TAG="1.20220830"
# Check if the unzipped directory of the Linux source exist; Otherwise create it from cache or download it.
if [[ ! -d $PWD/linux-${TAG} ]]
then
	if [[ ! -e ./${TAG}.tar.gz ]]
	then
		echo "Downloading Raspberry Pi Linux repo tag ${TAG}"
		wget https://github.com/raspberrypi/linux/archive/refs/tags/${TAG}.tar.gz
	fi
	tar -zxf ./${TAG}.tar.gz
fi
ln -sf $PWD/linux-${TAG} linux

# apply patches and sources
echo "Applying patches to Linux source"
(cd $PWD/mira220/patch && ./apply_patch.sh)
echo "Copying source files to Linux source"
(cd $PWD/mira220/src && ./apply_src.sh)
echo "Applying patches to Linux source"
(cd $PWD/mira050/patch && ./apply_patch.sh)
echo "Copying source files to Linux source"
(cd $PWD/mira050/src && ./apply_src.sh)
echo "Applying patches to Linux source"
(cd $PWD/mira016/patch && ./apply_patch.sh)
echo "Copying source files to Linux source"
(cd $PWD/mira016/src && ./apply_src.sh)
echo "Applying patches to Linux source"
(cd $PWD/mira130/patch && ./apply_patch.sh)
echo "Copying source files to Linux source"
(cd $PWD/mira130/src && ./apply_src.sh)
echo "Applying patches to Linux source"
(cd $PWD/poncha110/patch && ./apply_patch.sh)
echo "Copying source files to Linux source"
(cd $PWD/poncha110/src && ./apply_src.sh)

# prepare compilation script
echo "Preparing a build.sh script inside Linux source dir"
ln -sf $PWD/common/build.sh ./linux/build.sh

# config, build, and install kernel
echo "Inside Linux source dir, configuring the build"
(cd $PWD/linux && ./build.sh restoreconfig)
echo "Inside Linux source dir, compiling kernel and driver modules"
(cd $PWD/linux && ./build.sh build)
echo "Copying artifacts to RPI_KERNEL_INSTALL_DIR defined in linux/build.sh"
(cd $PWD/linux && ./build.sh install)
