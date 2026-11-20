#!/bin/bash
set -ex
export PATH=/bin
mount -t devtmpfs devtmpfs /dev
mount -t proc proc /proc
mount -t sysfs sysfs /sys
insmod /mods/loop.ko
insmod /mods/erofs.ko
dmsetup create skyward-root --table "0 $(cat /sys/block/vda/size) linear /dev/vda 0" --noudevsync
mount -t ext4 /dev/dm-0 /mnt
mount -t tmpfs tmpfs /mnt/dev
mkdir -p /mnt/dev/block
# Match the block special file made by Skyward, not a symlink.
read -r dev < /sys/block/dm-0/dev
/bin/mknod /mnt/dev/skyward-root b "${dev%:*}" "${dev#*:}"
mount --move /sys /mnt/sys
mount --move /proc /mnt/proc
exec chroot /mnt /guest-entry
