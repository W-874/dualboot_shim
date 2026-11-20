#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
lab=$(realpath "${1:?verified entry lab required}")
cd "$lab"
sha256sum -c verified.sha256
d=$(mktemp -d "$lab/auth-vm.XXXXXXXX")
cd "$d"
ndk=${ANDROID_NDK_HOME:-$HOME/Android/Sdk/ndk/29.0.14206865}/toolchains/llvm/prebuilt/linux-x86_64/bin
"$ndk/x86_64-linux-android35-clang" -static -O2 -Wall -Wextra -Werror "$here/auth_test.c" "$here/guest_volumes.c" "$here/format_auth.c" -o auth-test
cp --reflink=auto --sparse=always "$lab/rootfs.arm64.lab.img" disk.img
debugfs -w -R 'rm /guest-entry' disk.img
debugfs -w -R 'write auth-test /guest-entry' disk.img
for boot in 1 2; do
    timeout 60s qemu-system-x86_64 -accel kvm -cpu host -m 1024 -no-reboot -display none -monitor none -serial stdio -nic none -kernel "/usr/lib/modules/$(uname -r)/vmlinuz" -initrd "$lab/initrd.gz" -append 'console=ttyS0 rdinit=/init panic=-1 selinux=0' -drive "file=$PWD/disk.img,format=raw,if=virtio" > "boot$boot.log" 2>&1
done
for line in 'mismatched loop rejected' 'nonzero final byte rejected and retained' 'single grant, replay denied'; do grep -F "AUTH PASS $line" boot1.log; done
grep -F 'AUTH PASS reboot after claim before mkfs: denied' boot2.log
if grep -F 'AUTH FAIL' boot*.log; then exit 1; fi
