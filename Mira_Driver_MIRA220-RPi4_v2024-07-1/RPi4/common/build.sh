#!/usr/bin/env bash

set -e

cd "$( dirname "${BASH_SOURCE[0]}" )"

# Set OSBIT to 32 or 64 to switch builds
OSBIT=64

# SET RPIHW to RPI4B or RPI3BP
RPIHW=RPI4B

# The OSBIT and RPIHW controls KERNEL and CONFIG
# Details in doc:
# https://www.raspberrypi.com/documentation/computers/linux_kernel.html#kernel-configuration

# Configure variables according to OSBIT and RPIHW
if [ "$OSBIT" = "32" ]; then
	# For 32-bit OS, use the following settings
	export ARCH=arm
	export CROSS_COMPILE=arm-linux-gnueabihf-
	export IMG_NAME=zImage
	if [ "$RPIHW" = "RPI4B" ]; then
		export KERNEL=kernel7l
	elif [ "$RPIHW" = "RPI3B" ]; then
		export KERNEL=kernel7
	else
		echo "ERROR: RPIHW should be RPI4B or RPI3B."
		exit 1
	fi
elif [ "$OSBIT" = "64" ]; then
	# For 64-bit OS, use the following settings instead
	export ARCH=arm64
	export CROSS_COMPILE=aarch64-linux-gnu-
	export IMG_NAME=Image
	export KERNEL=kernel8
else
	echo "ERROR: OSBIT should be 32 or 64."
	exit 1
fi

if [ "$RPIHW" = "RPI4B" ]; then
	CONFIG=bcm2711_defconfig
elif [ "$RPIHW" = "RPI3B" ]; then
	if [ "$OSBIT" = "64" ]; then
		CONFIG=bcm2711_defconfig
	elif [ "$OSBIT" = "32" ]; then
		CONFIG=bcm2709_defconfig
	else
		echo "ERROR: OSBIT should be 32 or 64."
		exit 1
	fi
else
	echo "ERROR: RPIHW should be RPI4B or RPI3B."
	exit 1
fi

#
# The variables below are independent of OSBIT and RPIHW
#

NAME=$0
COMMAND=$1
INSTALL_DIR=${RPI_KERNEL_INSTALL_DIR:-/media/pi}
BUILD_DIR=${RPI_KERNEL_BUILD_DIR:-build}

# ASCII Escape sequences
RED_COLOR=31
YELLOW_COLOR=33
BLUE_COLOR=34
CYAN_COLOR=36

set_attribute() {
    echo -ne "\u001b[${1}m"
}

reset_attributes() {
    echo -ne "\u001b[0m"
}

log_warning() {
    set_attribute ${YELLOW_COLOR}
    echo "$@"
    reset_attributes
}

log_error() {
    set_attribute ${RED_COLOR}
    echo "$@"
    reset_attributes
}

usage() {
    set_attribute ${BLUE_COLOR}
    cat <<EOF
Usage: ${NAME} COMMAND
Supported commands are:
    clean:            Cleans the kernel output.
    restoreconfig:    Creates the config from the ${CONFIG} target.
    menuconfig:       Configure the target interactively using ncurses.
    build:            Builds kernel image, modules and device tree binaries.
    modules:          Builds the kernel modules only.
    dtbs:             Builds the device tree binaries only.
    kernel:           Builds the compressed kernel image only.
    install:          Builds and installs kernel and modules in the rootdir specified by
                      RPI_KERNEL_INSTALL_DIR.
    install_modules:  Builds and installs modules in the rootdir specified by RPI_KERNEL_INSTALL_DIR.
    install_dtbs:     Builds and installs dtbs in the rootdir specified by RPI_KERNEL_INSTALL_DIR.
    install_kernel:   Builds and installs kernel in the rootdir specified by RPI_KERNEL_INSTALL_DIR.
    compdb:           Generates a compilation database that can be used with clangd or IntelliSense
                      in VSCode.
EOF
    reset_attributes
    exit 1
}

install_cross_compiler() {
    echo "Would you like to install it now? (you may be asked for your password)"
    select response in "Yes" "No"; do
        case ${response} in
            Yes) sudo apt install -y gcc-arm-linux-gnueabihf gcc-aarch64-linux-gnu; break;;
            No) exit 0;;
            *) echo "Please select one of the available options";;
        esac
    done
}

check_dependencies() {
    if ! ${CROSS_COMPILE}gcc --version > /dev/null 2>&1; then
        log_warning "Could not find ${CROSS_COMPILE}gcc, which is required to build the Raspberry Pi kernel."
        install_cross_compiler
    fi
}

run_command() {
    set_attribute ${CYAN_COLOR}
    echo -n "Running command: "
    reset_attributes
    echo "\"$@\""
    if ! "$@"; then
        log_error "Error running command"
        exit 1
    fi
}
run_command_as_su_no_fail() {
    set_attribute ${CYAN_COLOR}
    echo -n "Running command as super user: "
    reset_attributes
    echo "\"$@\""
    if ! "sudo" "$@"; then
        log_error "Error running command"
    fi
}

run_command_as_su() {
    set_attribute ${CYAN_COLOR}
    echo -n "Running command as super user: "
    reset_attributes
    echo "\"$@\""
    if ! "sudo" "$@"; then
        log_error "Error running command"
        exit 1
    fi
}

run_make() {
    run_command make -j"$(nproc)" "$@" INSTALL_MOD_PATH="${INSTALL_DIR}" "O=${BUILD_DIR}"
}

run_install_modules() {
    run_command_as_su make -j"$(nproc)" modules_install INSTALL_MOD_PATH="${INSTALL_DIR}" "O=${BUILD_DIR}"
}

run_install_dtbs() {
    run_command_as_su mkdir -p "${INSTALL_DIR}/boot/overlays"
    if [ "$ARCH" = "arm" ]; then
        run_command_as_su cp "${BUILD_DIR}"/arch/${ARCH}/boot/dts/*.dtb "${INSTALL_DIR}/boot/"
    else
        run_command_as_su cp "${BUILD_DIR}"/arch/${ARCH}/boot/dts/broadcom/*.dtb "${INSTALL_DIR}/boot/"
    fi
    run_command_as_su cp "${BUILD_DIR}"/arch/${ARCH}/boot/dts/overlays/*.dtb* "${INSTALL_DIR}/boot/overlays"
}

run_install_kernel() {
    run_command_as_su mkdir -p "${INSTALL_DIR}/boot"
    if [ -e ${INSTALL_DIR}/boot/$KERNEL.img ]; then
	    run_command_as_su_no_fail cp "${INSTALL_DIR}/boot/$KERNEL.img" "${INSTALL_DIR}/boot/${KERNEL}-backup.img"
    fi
    run_command_as_su cp "${BUILD_DIR}/arch/${ARCH}/boot/${IMG_NAME}" "${INSTALL_DIR}/boot/${KERNEL}.img"
}

run_install() {
    run_install_modules
    run_install_kernel
    run_install_dtbs
}

run_build() {
    if [ ! -f ${BUILD_DIR}/.config ];
    then
        log_warning "No configuration found, using default configuration."
        run_make ${CONFIG}
    fi
    run_make "$@"
}

gen_compile_commands() {
    KERNEL_SOURCE_DIR=${PWD}
    run_command pushd ${BUILD_DIR}
    run_command ${KERNEL_SOURCE_DIR}/scripts/clang-tools/gen_compile_commands.py
    run_command popd
    run_command ln -sf ${BUILD_DIR}/compile_commands.json compile_commands.json
}

check_dependencies

case ${COMMAND} in
    clean)
        run_make clean
        run_command rm -rf ${BUILD_DIR};;
    restoreconfig) run_make ${CONFIG};;
    menuconfig) run_make menuconfig;;
    build) run_build ${IMG_NAME} dtbs modules;;
    modules) run_build modules;;
    dtbs) run_build dtbs;;
    kernel) run_make ${IMG_NAME};;
    install) run_install;;
    install_modules) run_install_modules;;
    install_dtbs) run_install_dtbs;;
    install_kernel) run_install_kernel;;
    compdb) gen_compile_commands;;
    --) run_make "${@:2}";;
    *) usage;;
esac
