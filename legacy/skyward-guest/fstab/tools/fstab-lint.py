"""P4.4 guest fstab policy; host userdata is never an fs_mgr mount."""

import argparse
from pathlib import Path
import shutil
import sys

FSTAB = "fstab.qcom"
CHARGER = "charger_fw_fstab.qti"
DEVICES = ("fuxi", "dada")
GUEST = "/dev/block/skyward/"
HOST = "/dev/block/bootdevice/by-name/"
GUEST_OPTIONS = "rw,noatime,nosuid,nodev"
DATA_FLAGS = (
    "wait,check,latemount,formattable,"
    "fileencryption=aes-256-xts:aes-256-cts:v2,"
    "metadata_encryption=aes-256-xts,"
    "keydirectory=/metadata/vold/metadata_encryption"
)
MODEM_OPTIONS = (
    "ro,shortname=lower,uid=1000,gid=1000,dmask=227,fmask=337,"
    "context=u:object_r:firmware_file:s0"
)
BT_OPTIONS = (
    "ro,shortname=lower,uid=1002,gid=3002,dmask=227,fmask=337,"
    "context=u:object_r:bt_firmware_file:s0"
)


def policy(device, charger):
    # Exact source+destination pairs, not basenames: do not accept dm, path
    # traversal, raw host aliases or dynamic partition names as guest volumes.
    mounts = {
        (HOST + "modem", "/vendor/firmware_mnt"): ("vfat", MODEM_OPTIONS, "wait,slotselect"),
    }
    if not charger:
        mounts.update({
            (GUEST + "metadata", "/metadata"): ("ext4", GUEST_OPTIONS, "wait,check,formattable"),
            (GUEST + "userdata", "/data"): ("ext4", GUEST_OPTIONS, DATA_FLAGS),
            (GUEST + "persist", "/mnt/vendor/persist"): ("ext4", GUEST_OPTIONS, "wait"),
            (HOST + "dsp", "/vendor/dsp"): ("ext4", "ro,nosuid,nodev,noload", "wait,slotselect"),
            (HOST + "bluetooth", "/vendor/bt_firmware"): ("vfat", BT_OPTIONS, "wait,slotselect"),
        })
        if device == "dada":
            mounts[(HOST + "modemfirmware", "/vendor/modem_firmware")] = (
                "vfat", MODEM_OPTIONS, "wait,slotselect"
            )
    return mounts


def check_options(actual, expected):
    tokens = actual.split(",")
    names = [token.split("=", 1)[0] for token in tokens]
    return len(names) == len(set(names)) and set(tokens) == set(expected.split(","))


def lint(text, label, device, charger):
    remaining = policy(device, charger)
    errors = []
    for line_no, line in enumerate(text.split("\n"), 1):
        if any(ord(c) < 32 and c not in "\t\r" for c in line):
            errors.append(f"{label}:{line_no}: invalid control character")
            continue
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        for i, token in enumerate(fields):
            if token.startswith("#"):
                fields = fields[:i]
                break
        if len(fields) != 5:
            errors.append(f"{label}:{line_no}: expected five Android fstab fields")
            continue
        source, target, fstype, mount_flags, mgr_flags = fields
        where = f"{label}:{line_no}"
        if "formattable" in mgr_flags.split(",") and (charger or (source, target) not in (
            (GUEST + "metadata", "/metadata"), (GUEST + "userdata", "/data")
        )):
            errors.append(f"{where}: H6: formattable outside guest metadata/userdata")
        required = remaining.pop((source, target), None)
        if required is None:
            errors.append(f"{where}: H6/H9: unexpected or duplicate mount {source} -> {target}")
            continue
        if fstype != required[0]:
            errors.append(f"{where}: expected {required[0]} filesystem, got {fstype}")
        if not check_options(mount_flags, required[1]):
            errors.append(f"{where}: H6/H9: expected mount flags {required[1]}, got {mount_flags}")
        if not check_options(mgr_flags, required[2]):
            errors.append(f"{where}: H6/H9: expected fs_mgr flags {required[2]}, got {mgr_flags}")
    errors.extend(f"{label}: missing {src} -> {target}" for src, target in remaining)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True, choices=DEVICES)
    parser.add_argument("--fstab", required=True, type=Path)
    parser.add_argument("--charger", required=True, type=Path)
    parser.add_argument("--checked-dir", type=Path, help="copy both inputs after lint passes")
    args = parser.parse_args()
    errors = []
    try:
        for path, charger in ((args.fstab, False), (args.charger, True)):
            errors.extend(lint(path.read_text(encoding="utf-8"), path, args.device, charger))
        if errors:
            raise ValueError("\n".join(errors))
        if args.checked_dir:
            args.checked_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.fstab, args.checked_dir / FSTAB)
            shutil.copyfile(args.charger, args.checked_dir / CHARGER)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"{args.device}: {args.fstab} and {args.charger} pass H6/H9")
    return 0


if __name__ == "__main__":
    sys.exit(main())
