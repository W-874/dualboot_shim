#!/usr/bin/env bash
set -euo pipefail
lab=$(realpath "${1:?verified entry lab}")
kernel=$(realpath "${2:?x86 Android kernel}")
o=$(mktemp -d "$lab/dm-probe.XXXXXXXX")
cd "$o"
cp -a "$lab/initrd" root
printf '%s\n' '#!/bin/bash' 'export PATH=/bin' 'mount -t proc proc /proc || exit 1' 'mount -t tmpfs tmpfs /dev || exit 1' 'mknod /dev/kmsg c 1 11' 'exec >/dev/kmsg 2>&1' 'mkdir -p /dev/mapper' 'mknod /dev/mapper/control c 10 236' 'dmsetup targets && echo TARGET_LIST_COMPLETE' 'echo o >/proc/sysrq-trigger' > root/init
(cd root && find . -print0 | cpio --null -o --format=newc --owner=0:0 --quiet | gzip -1) > initrd.gz
timeout 30s qemu-system-x86_64 -machine q35,accel=tcg -cpu max -m 1024 -no-reboot -display none -monitor none -serial stdio -nic none -kernel "$kernel" -initrd initrd.gz -append 'earlycon=uart8250,io,0x3f8 keep_bootcon ignore_loglevel console=ttyS0 rdinit=/init panic=-1 selinux=0 nokaslr' > vm.log 2>&1 || test "$?" = 124
printf '%s\n' "$PWD/vm.log"
grep -F TARGET_LIST_COMPLETE vm.log
grep -E 'default-key[[:space:]]+v[0-9]' vm.log
