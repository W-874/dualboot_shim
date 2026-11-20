#!/bin/bash
set -eu
export PATH=/bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /dev
mknod /dev/kmsg c 1 11
exec >/dev/kmsg 2>&1
poweroff_vm() { echo o >/proc/sysrq-trigger; while :; do :; done; }
trap 'echo "CRYPTO FAIL line $LINENO"; poweroff_vm' ERR
mkdir /dev/mapper
mknod /dev/mapper/control c 10 236
for mod in virtio_pci_legacy_dev virtio_pci_modern_dev virtio_pci virtio_blk; do insmod "/lib/modules/$mod.ko"; done
read -r num </sys/class/block/vda/dev
read -r sectors </sys/class/block/vda/size
[[ $sectors = 131072 ]]
mknod /dev/testdisk b "${num%:*}" "${num#*:}"
read -r cmd </proc/cmdline
mkdir -p /outer /dev/block/skyward
if [[ " $cmd " = *' skytest=first '* ]]; then mke2fs -q -t ext4 -b 4096 /dev/testdisk; fi
mount -t ext4 /dev/testdisk /outer
if [[ " $cmd " = *' skytest=first '* ]]; then truncate -s 32M /outer/userdata.raw; fi
mknod /dev/block/loop0 b 7 0
losetup /dev/block/loop0 /outer/userdata.raw
ln -s /dev/block/loop0 /dev/block/skyward/userdata
read -r sectors </sys/block/loop0/size
[[ $sectors = 65536 ]]
map() {
    dmsetup create skytest --noudevsync --table "0 $sectors default-key aes-xts-plain64 $1 0 /dev/block/skyward/userdata 0 2 sector_size:4096 iv_large_sectors" || return 1
    read -r num </sys/block/dm-0/dev
    mknod /dev/mapped b "${num%:*}" "${num#*:}"
}
key=$(printf '%064d%064d' 1 2)
bad=$(printf '%064d%064d' 3 4)
if [[ " $cmd " = *' skytest=first '* ]]; then
    map "$key"
    mke2fs -q -t ext4 -b 4096 /dev/mapped
    mount -t ext4 /dev/mapped /mnt
    echo 'guest persistence sentinel v1' >/mnt/sentinel
    umount /mnt
    echo 'CRYPTO PASS initialized encrypted mapping'
else
    if mount -t ext4 -o ro,noload /dev/block/skyward/userdata /mnt; then exit 1; fi
    map "$bad"
    if mount -t ext4 -o ro,noload /dev/mapped /mnt; then exit 1; fi
    dmsetup remove skytest --noudevsync
    rm /dev/mapped
    map "$key"
    mount -t ext4 -o ro,noload /dev/mapped /mnt
    read -r value </mnt/sentinel
    [[ $value = 'guest persistence sentinel v1' ]]
    umount /mnt
    echo 'CRYPTO PASS raw and wrong key rejected; correct key recovered data after reboot'
fi
dmsetup remove skytest --noudevsync
sync
losetup -d /dev/block/loop0
umount /outer
poweroff_vm
