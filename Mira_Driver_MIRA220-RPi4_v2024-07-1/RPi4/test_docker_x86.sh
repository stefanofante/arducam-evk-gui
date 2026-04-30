#!/usr/bin/env bash

cd "$( dirname "${BASH_SOURCE[0]}" )"

set -e

NAME=$0
COMMAND=$1

export BUILD_DIR=${PWD}/build
export RPI_KERNEL_INSTALL_DIR=${RPI_KERNEL_INSTALL_DIR:-"${BUILD_DIR}/kernel_install"}
export RPI_KERNEL_BUILD_DIR=${RPI_KERNEL_BUILD_DIR:-"${BUILD_DIR}/kernel"}
export AMSRPIKERNEL_BUILD_DIR=${AMSRPIKERNEL_BUILD_DIR:-"${BUILD_DIR}/ams_rpi_kernel"}

./build_all.sh
