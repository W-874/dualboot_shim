#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
lab=$(realpath "${1:?entry lab}"); kernel=$(realpath "${2:?x86 Android kernel}")
o=$(mktemp -d "$lab/crypto-vm.XXXXXXXX"); cd "$o"; echo "$PWD"
cp -a "$lab/initrd" root
for tool in mke2fs umount rm losetup ln truncate sync; do
    bin=$(command -v "$tool"); cp -L "$bin" "root/bin/$tool"
    ldd "$bin" | awk '/=> \//{print $3}' | while read -r lib; do install -D -m755 "$(realpath "$lib")" "root$lib"; done
done
install -Dm644 /etc/mke2fs.conf root/etc/mke2fs.conf
install -m755 "$here/vm-crypto-init.sh" root/init
lz4=${LZ4:-$here/../../uwuAOSP-src/out-dada/host/linux-x86/bin/lz4}
{ "$lz4" -dc "$(dirname "$kernel")/ramdisk.img"; (cd root && find . -print0 | cpio --null -o --format=newc --owner=0:0 --quiet); } | gzip -1 > initrd.gz
truncate -s 64M disk.img
for boot in first second; do
    timeout 40s qemu-system-x86_64 -machine q35,accel=tcg -cpu max -m 1024 -no-reboot -display none -monitor none -serial stdio -nic none -kernel "$kernel" -initrd initrd.gz -append "earlycon=uart8250,io,0x3f8 keep_bootcon ignore_loglevel printk.devkmsg=on console=ttyS0 rdinit=/init panic=-1 selinux=0 nokaslr skytest=$boot" -drive "file=$PWD/disk.img,format=raw,if=virtio" > "$boot.log" 2>&1
    if grep -E 'CRYPTO FAIL|Kernel panic' "$boot.log"; then exit 1; fi
    case "$boot" in first) grep -F 'CRYPTO PASS initialized encrypted mapping' first.log;; second) grep -F 'CRYPTO PASS raw and wrong key rejected; correct key recovered data after reboot' second.log;; esac
done
