"""P4.4 guest fstab policy; host userdata is never an fs_mgr mount."""

import argparse
from pathlib import Path
import shutil
import sys
import hashlib
import re
import tomllib

FSTAB = "fstab.qcom"
CHARGER = "charger_fw_fstab.qti"
GUEST = "/dev/block/skyward/"
HOST = "/dev/block/bootdevice/by-name/"
GUEST_OPTIONS = "rw,noatime,nosuid,nodev"
DATA_FLAGS = (
    "wait,check,latemount,formattable,"
    "fileencryption=aes-256-xts:aes-256-cts:v2,"
    "metadata_encryption=aes-256-xts,"
    "keydirectory=/metadata/vold/metadata_encryption"
)

def load_profile(path):
    blob = path.read_bytes()
    profile = tomllib.loads(blob.decode())
    if profile['schema_version'] != 1 or not re.fullmatch(r'[a-z][a-z0-9-]*', profile['id']):
        raise ValueError('unsupported device profile')
    if profile['product']['zygote'] not in ('zygote64', 'zygote64_32'):
        raise ValueError('unsupported zygote ABI')
    if profile['format_protocol'] not in ('token-v2', 'legacy-v1'):
        raise ValueError('unknown format protocol')
    return profile, hashlib.sha256(blob).hexdigest()


def policy(profile, charger):
    mounts = {} if charger else {
        (GUEST + 'metadata', '/metadata'): ('ext4', GUEST_OPTIONS, 'wait,check,formattable'),
        (GUEST + 'userdata', '/data'): ('ext4', GUEST_OPTIONS, DATA_FLAGS),
        (GUEST + 'persist', '/mnt/vendor/persist'): ('ext4', GUEST_OPTIONS, 'wait'),
    }
    rows = profile['mounts']['charger' if charger else 'firmware']
    targets = {target for _, target in mounts}
    if len(rows) > 16:
        raise ValueError('too many firmware mounts')
    for source, target, fs, options, flags in rows:
        opts = options.split(',')
        names = [opt.split('=', 1)[0] for opt in opts]
        if (not re.fullmatch(re.escape(HOST) + r'[A-Za-z0-9_.@,-]+', source)
                or not re.fullmatch(r'/vendor/[A-Za-z0-9_.@,-]+', target)
                or source.rsplit('/', 1)[-1] in ('.', '..') or target.rsplit('/', 1)[-1] in ('.', '..')
                or target in targets or fs not in ('vfat', 'ext4') or flags != 'wait,slotselect'
                or not re.fullmatch(r'[!-~]+', options) or '#' in options or '' in names
                or len(names) != len(set(names)) or 'ro' not in opts
                or set(names) & {'rw', 'remount', 'bind', 'rbind', 'move', 'loop'}
                or (fs == 'ext4' and 'noload' not in opts)):
            raise ValueError(f'unsafe firmware mount {source} -> {target}')
        targets.add(target)
        mounts[source, target] = fs, options, flags
    return mounts


def check_options(actual, expected):
    tokens = actual.split(",")
    names = [token.split("=", 1)[0] for token in tokens]
    return len(names) == len(set(names)) and set(tokens) == set(expected.split(","))


def lint(text, label, profile, charger):
    remaining = policy(profile, charger)
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
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--fstab', type=Path)
    parser.add_argument('--charger', type=Path)
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument('--checked-dir', type=Path, help='copy both inputs after lint passes')
    outputs.add_argument('--render', type=Path, help='generate checked fstab and product inputs in a new directory')
    args = parser.parse_args()
    if args.render and (args.fstab or args.charger) or not args.render and not (args.fstab and args.charger):
        parser.error('use either --render or both --fstab and --charger')
    errors = []
    try:
        profile, sha = load_profile(args.profile)
        if args.render:
            files = {}
            for dest, charger in ((FSTAB, False), (CHARGER, True)):
                rows = policy(profile, charger)
                files[dest] = ''.join(' '.join(source + fields) + '\n' for source, fields in rows.items())
            args.render.mkdir()
            for dest, content in files.items():
                (args.render / dest).write_text(content, encoding='utf-8')
            (args.render / 'profile.mk').write_text(
                f"SKYWARD_ZYGOTE := {profile['product']['zygote']}\nSKYWARD_PROFILE_SHA256 := {sha}\n", encoding='utf-8')
            return 0
        for path, charger in ((args.fstab, False), (args.charger, True)):
            errors.extend(lint(path.read_text(encoding='utf-8'), path, profile, charger))
        if errors:
            raise ValueError("\n".join(errors))
        if args.checked_dir:
            args.checked_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.fstab, args.checked_dir / FSTAB)
            shutil.copyfile(args.charger, args.checked_dir / CHARGER)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"{profile['id']}: {args.fstab} and {args.charger} pass H6/H9")
    return 0


if __name__ == "__main__":
    sys.exit(main())
