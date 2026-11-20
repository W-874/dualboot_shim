# SPDX-License-Identifier: Apache-2.0
# The guest is entered using the running host kernel and the host ramdisk.
TARGET_NO_KERNEL := true
TARGET_NO_RECOVERY := true
# Valid only when the product builds a recovery image.
BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE :=
# Soong also infers init_boot from a nonempty partition size.
BOARD_INIT_BOOT_IMAGE_PARTITION_SIZE :=
BOARD_PREBUILT_DTBOIMAGE :=
BOARD_PREBUILT_DTBIMAGE_DIR :=
TARGET_RECOVERY_FSTAB :=
AB_OTA_UPDATER := false
AB_OTA_PARTITIONS :=

# Stock DLKM is supplied separately; do not make guest vbmeta depend on it.
BOARD_AVB_VBMETA_SYSTEM := $(filter-out system_dlkm,$(BOARD_AVB_VBMETA_SYSTEM))
BOARD_AVB_VBMETA_VENDOR := $(filter-out vendor_dlkm odm_dlkm,$(BOARD_AVB_VBMETA_VENDOR))
