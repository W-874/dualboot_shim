# SPDX-License-Identifier: Apache-2.0
# Included directly by the two Skyward guest products after PRODUCT_DEVICE.
$(call soong_config_set,skyward,guest_device,$(PRODUCT_DEVICE))
$(call soong_config_set,skyward,guest_gatekeeper,true)

PRODUCT_SYSTEM_PROPERTIES += ro.skyward.guest=true ro.frp.pst=
PRODUCT_ODM_PROPERTIES += ro.vendor.se.type=HCE
PRODUCT_NO_DYNAMIC_SYSTEM_UPDATE := true

# The fuxi guest system ships app_process64 but not app_process32.
ifeq ($(PRODUCT_DEVICE),fuxi)
PRODUCT_VENDOR_PROPERTIES += ro.zygote=zygote64
endif

# This is not an OTA. P4.6 checks VINTF against the running host's exact
# kernel release and IKCONFIG, never against a guest-generated kernel.
PRODUCT_OTA_ENFORCE_VINTF_KERNEL_REQUIREMENTS := false

# The host provides the boot chain and both stock DLKM images.
PRODUCT_BUILD_BOOT_IMAGE := false
PRODUCT_BUILD_INIT_BOOT_IMAGE := false
PRODUCT_BUILD_VENDOR_BOOT_IMAGE := false
PRODUCT_BUILD_VENDOR_KERNEL_BOOT_IMAGE := false
PRODUCT_BUILD_RAMDISK_IMAGE := false
PRODUCT_BUILD_RECOVERY_IMAGE := false
PRODUCT_BUILD_SYSTEM_DLKM_IMAGE := false
PRODUCT_BUILD_VENDOR_DLKM_IMAGE := false
PRODUCT_BUILD_ODM_DLKM_IMAGE := false

include device/xiaomi/skyward-guest/fstab/guest-fstab.mk
