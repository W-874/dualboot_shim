# SPDX-License-Identifier: Apache-2.0
# Include a generated vendor product fragment without altering P4.1 extraction
# inputs or generated outputs. Drop install roots, not ELF dependencies of
# retained daemons (e.g. the vendor NFC HAL's interface libraries).
include $(skyward_vendor_mk)

PRODUCT_PACKAGES := $(filter-out \
    android.hardware.boot-service.% vendor.qti.hardware.boot% \
    android.hardware.weaver% vendor.qti.hardware.weaver% \
    android.hardware.secure_element% vendor.qti.secure_element% secure_element-service.xml \
    vendor.qti.esepowermanager@%-service, \
    $(PRODUCT_PACKAGES))

# Generated PRODUCT_COPY_FILES use an absolute-within-tree source followed by
# the destination path. Match specific file basenames independently of SDK,
# source-tree and partition placeholders.
_skyward_excluded_copy_basenames := android.hardware.weaver% vendor.qti.hardware.weaver% \
    android.hardware.secure_element% vendor.qti.secure_element% secure_element-service.xml \
    vendor.qti.esepowermanager@%-service.rc sn100u_ese% sn100u_pnx_ese% \
    android.hardware.nfc.ese.xml android.hardware.nfc.uicc.xml android.hardware.se.omapi.% \
    fstab.qcom charger_fw_fstab.qti charger_fstab.qti charger_fstab.xiaomi init.batteryantiaging.rc
PRODUCT_COPY_FILES := $(strip $(foreach c,$(PRODUCT_COPY_FILES), \
    $(if $(filter $(_skyward_excluded_copy_basenames),$(notdir $(word 2,$(subst :, ,$(c))))),,$(c))))
_skyward_excluded_copy_basenames :=
skyward_vendor_mk :=
