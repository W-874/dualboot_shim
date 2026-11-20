#!/usr/bin/env bash
# Transform only a copied guest vendor stage; fail on unexpected active block paths.
set -euo pipefail
v=$(realpath "$1")
case "$v" in "$(cd "$(dirname "$0")" && pwd)"/candidate.*/stage/vendor) ;; *) exit 2;; esac
hw=$v/etc/init/hw
for name in init.qcom.rc init.qti.kernel.rc init.target.rc; do
    sed -i -E '\@^[[:space:]]*(chmod 0660|chown root system) /dev/block/bootdevice/by-name/(rawdump|ramdump)[[:space:]]*$@d;\@^[[:space:]]*mount vfat /dev/block/bootdevice/by-name/ramdump /mnt/ramdump ro @d;\@^[[:space:]]*wait /dev/block/platform/soc/\$\{ro.boot.bootdevice\}[[:space:]]*$@d;\@^[[:space:]]*symlink /dev/block/platform/soc/\$\{ro.boot.bootdevice\} /dev/block/bootdevice[[:space:]]*$@d' "$hw/$name"
done
if rg -n '^[[:space:]]*[^#[:space:]][^#]*/dev/block/' "$v/etc/init" -g '*.rc'; then exit 1; else test "$?" = 1; fi
