LINUX_PATH=../../linux
PATCH_PATH=.
cp $PATCH_PATH/mira220_mono_color-overlay.dtsi $LINUX_PATH/arch/arm/boot/dts/overlays/
cp $PATCH_PATH/mira220-overlay.dts $LINUX_PATH/arch/arm/boot/dts/overlays/
cp $PATCH_PATH/mira220color-overlay.dts $LINUX_PATH/arch/arm/boot/dts/overlays/
cp $PATCH_PATH/mira220.inl $LINUX_PATH/drivers/media/i2c/
cp $PATCH_PATH/mira220.c $LINUX_PATH/drivers/media/i2c/
cp $PATCH_PATH/mira220color.c $LINUX_PATH/drivers/media/i2c/
