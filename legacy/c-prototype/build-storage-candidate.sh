#!/usr/bin/env bash
# Offline dada engineering image; deliberately no system.toml or executable init.
set -euo pipefail
umask 077
here=$(cd "$(dirname "$0")" && pwd)
src=$(realpath "$here/../../uwuAOSP-src")
cd "$src"
p=out-dada/target/product/dada
h=$src/out-dada/host/linux-x86/bin
ref=$here/../stock-4.0.0.22
o=$(mktemp -d "$here/candidate.XXXXXXXX")
printf 'Work directory: %s\n' "$o"
v=$o/stage/vendor
r=$o/root
mkdir -p "$v" "$r"/{dev,proc,sys,images,volumes}
(cd "$p" && sha256sum -c "$here/../repair-18-images.sha256")
cp -a --reflink=auto "$p/vendor/." "$v/"
# Retain copyright headers; replace both files exclusively in the private staging tree.
sed -i '/^[[:space:]]*[^#[:space:]]/d' "$v/etc/charger_fw_fstab.qti" "$v/etc/fstab.qcom"
printf '%s\n' '/dev/block/skyward/metadata /metadata ext4 noatime,nosuid,nodev wait,check' '/dev/block/skyward/userdata /data ext4 noatime,nosuid,nodev latemount,wait,check,fileencryption=aes-256-xts:aes-256-cts:v2,keydirectory=/metadata/vold/metadata_encryption,metadata_encryption=aes-256-xts' >> "$v/etc/fstab.qcom"
i=$p/obj/PACKAGING/vendor_intermediates
bash "$here/vendor-guard.sh" "$v"
PATH="$h:$PATH" "$h/build_image" --input-directory-filter-file "$i/file_list.txt" "$v" "$i/vendor_image_info.txt" "$r/images/vendor.img" "$p/system"
for name in system product system_ext odm; do cp --reflink=auto "$p/$name.img" "$r/images/"; done
for name in system_dlkm vendor_dlkm; do cp --reflink=auto "$ref/$name.img" "$r/images/"; done
for file in fstab.qcom charger_fw_fstab.qti; do
    debugfs -R "dump /etc/$file $o/readback-$file" "$r/images/vendor.img"
    cmp "$o/readback-$file" "$v/etc/$file"
    debugfs -R "stat /etc/$file" "$r/images/vendor.img" > "$o/stat-$file"
    grep -q 'security.selinux' "$o/stat-$file"
done
for name in init.qcom.rc init.qti.kernel.rc init.target.rc; do
    debugfs -R "dump /etc/init/hw/$name $o/readback-$name" "$r/images/vendor.img"
    cmp "$o/readback-$name" "$v/etc/init/hw/$name"
done
# Never pre-format raw userdata as plaintext beneath the intended dm-default-key layer.
# Provisioning an encrypted mapping is separate from making an empty container.
truncate -s 4G "$r/volumes/userdata.raw"
truncate -s 64M "$r/volumes/metadata.ext4"
/usr/sbin/mke2fs -q -t ext4 -b 4096 -L sky-metadata -m 0 "$r/volumes/metadata.ext4"
size=$(du -sb --apparent-size "$r" | cut -f1)
outer=$o/rootfs.offline.img
truncate -s "$((size + 1024 * 1024 * 1024))" "$outer"
/usr/sbin/mke2fs -q -t ext4 -b 4096 -L sky-dada-lab -m 0 -d "$r" "$outer"
# mke2fs -d preserves workstation ownership; set it inside the image only.
for path in /volumes /volumes/metadata.ext4 /volumes/userdata.raw; do
    for field in uid gid; do
        debugfs -w -R "set_inode_field $path $field 0" "$outer"
    done
    debugfs -R "stat $path" "$outer" | grep -E 'User: +0 +Group: +0'
done
e2fsck -fn "$outer"
# Verify every nested file from the actual outer filesystem, not just staging.
for f in "$r"/{images,volumes}/*; do
    debugfs -R "dump ${f#"$r"} $o/check.tmp" "$outer"
    cmp "$f" "$o/check.tmp"
    rm -- "$o/check.tmp"
done
(cd "$r" && sha256sum images/* volumes/*) > "$o/nested.sha256"
sha256sum "$outer" > "$o/rootfs.sha256"
printf 'Verified offline storage candidate: %s\n' "$outer"
