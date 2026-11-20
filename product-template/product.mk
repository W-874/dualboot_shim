# SPDX-License-Identifier: Apache-2.0
# Guest products only. Include after selecting a rendered profile directory.
ifeq ($(strip $(SKYWARD_GENERATED_PROFILE)),)
$(error SKYWARD_GENERATED_PROFILE must point to an approved rendered profile)
endif
include $(SKYWARD_GENERATED_PROFILE)/profile.mk
$(call soong_config_set,skyward,guest_gatekeeper,true)

PRODUCT_SYSTEM_PROPERTIES += ro.skyward.guest=true ro.frp.pst=
PRODUCT_ODM_PROPERTIES += ro.vendor.se.type=HCE
PRODUCT_NO_DYNAMIC_SYSTEM_UPDATE := true

PRODUCT_VENDOR_PROPERTIES += ro.zygote=$(SKYWARD_ZYGOTE)

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

PRODUCT_COPY_FILES += \
    $(SKYWARD_GENERATED_PROFILE)/fstab.qcom:$(TARGET_COPY_OUT_VENDOR)/etc/fstab.qcom \
    $(SKYWARD_GENERATED_PROFILE)/charger_fw_fstab.qti:$(TARGET_COPY_OUT_VENDOR)/etc/charger_fw_fstab.qti
