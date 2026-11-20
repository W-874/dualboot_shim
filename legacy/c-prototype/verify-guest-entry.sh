#!/usr/bin/env bash
# Boot the real nested images on a disposable VM overlay. No host loop devices.
set -euo pipefail
cd "$(dirname "$0")"
here=$PWD
base=$(realpath "${1:-$here/candidate.XeaY7Gw3}")
ndk=${ANDROID_NDK_HOME:-$HOME/Android/Sdk/ndk/29.0.14206865}/toolchains/llvm/prebuilt/linux-x86_64/bin
out=$(mktemp -d "$here/entry-lab.XXXXXXXX")
echo "$out"
cd "$out"
(cd "$base" && sha256sum -c rootfs.sha256)
for arch in aarch64 x86_64; do
    "$ndk/$arch-linux-android35-clang" -static -O2 -Wall -Wextra -Werror "$here/guest_entry.c" "$here/guest_volumes.c" "$here/format_auth.c" -o "entry.$arch"
done
cp --reflink=auto --sparse=always "$base/rootfs.offline.img" rootfs.arm64.lab.img
# Check the private copy, not a filename supplied by the candidate's manifest.
printf '%s  %s\n' 47be3538aad7c225cef56d23f7cf6f2dae00e1f038b52a80bbc0ecdc0798dcb1 rootfs.arm64.lab.img | sha256sum -c -
{
    echo 'mkdir /android'
    echo 'write entry.aarch64 /guest-entry'
    # Only seed a fresh lab copy of the pinned virgin storage image.
    echo 'ea_set /volumes/userdata.raw trusted.skyward.mkfs new-v1'
    for path in / /android /guest-entry /images /images/{system,vendor,product,system_ext,odm,system_dlkm,vendor_dlkm}.img; do
        echo "set_inode_field $path uid 0"
        echo "set_inode_field $path gid 0"
    done
} > debugfs.cmd
debugfs -w -f debugfs.cmd rootfs.arm64.lab.img
debugfs -R 'dump /guest-entry readback' rootfs.arm64.lab.img
cmp readback entry.aarch64
e2fsck -fn rootfs.arm64.lab.img
cp --reflink=auto --sparse=always rootfs.arm64.lab.img vm.img
debugfs -w -R 'rm /guest-entry' vm.img
debugfs -w -R 'write entry.x86_64 /guest-entry' vm.img
mkdir -p initrd/{dev,proc,sys,mnt,mods,bin}
for tool in bash mount mkdir mknod chroot dmsetup cat insmod; do
    bin=$(command -v "$tool"); cp -L "$bin" "initrd/bin/$tool"
    ldd "$bin" | awk '/=> \//{print $3} /^\s*\//{print $1}' | while read -r lib; do
        install -D -m755 "$(realpath "$lib")" "initrd$lib"
    done
done
ldso=/lib64/ld-linux-x86-64.so.2
install -D -m755 "$(realpath "$ldso")" "initrd$ldso"
k=$(uname -r)
for mod in loop erofs; do
    xz -dc "$(modinfo -k "$k" -n "$mod")" > "initrd/mods/$mod.ko"
done
install -m755 "$here/vm-init.sh" initrd/init
(cd initrd && find . -print0 | cpio --null -o --format=newc --owner=0:0 --quiet | gzip -1) > initrd.gz
timeout 20s qemu-system-x86_64 -accel kvm -cpu host -smp 2 -m 1024 -no-reboot -display none -monitor none -serial stdio -nic none -kernel "/usr/lib/modules/$k/vmlinuz" -initrd initrd.gz -append 'console=ttyS0 rdinit=/init panic=-1 selinux=0' -drive "file=$PWD/vm.img,format=raw,if=virtio,snapshot=on" > vm.log 2>&1 || test "$?" = 124
grep -F 'skyward-guest: PASS seven read-only mounts and both writable loop identities after exec' vm.log
grep -F 'skyward-guest: format authorization consumed' vm.log
sha256sum rootfs.arm64.lab.img entry.aarch64 entry.x86_64 > verified.sha256
