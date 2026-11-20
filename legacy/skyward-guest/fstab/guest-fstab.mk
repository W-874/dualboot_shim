# Include only in a Skyward guest product, after PRODUCT_DEVICE is set.
ifeq ($(filter fuxi dada,$(PRODUCT_DEVICE)),)
$(error Skyward guest fstab: unsupported PRODUCT_DEVICE '$(PRODUCT_DEVICE)')
endif
# ProductGraph's guest-only skyward/guest_device config also gates the Soong install rules.
PRODUCT_PACKAGES += skyward_guest_fstab_$(PRODUCT_DEVICE)
