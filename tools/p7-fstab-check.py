"""P7.4: run H6/H9 lint on fstabs read from vendor.img or a delivery zip."""

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

FSTAB = "fstab.qcom"
CHARGER = "charger_fw_fstab.qti"
EXPECTED = {FSTAB, CHARGER}


def run(*args, cwd, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, env={**os.environ, "LC_ALL": "C"}, **kwargs)


def debugfs(image, request, work):
    result = run("debugfs", "-R", request, image, cwd=work, capture_output=True)
    # Some debugfs request failures don't set the exit status.
    errors = [line for line in result.stderr.decode(errors="replace").splitlines()
              if not line.startswith("debugfs ")]
    if errors:
        raise ValueError(f"{image}: {request}: {'; '.join(errors)}")
    return result.stdout


def ext4_entries(image, path, work):
    data = debugfs(image, f"ls -p {path}", work).decode(errors="replace")
    entries = {}
    for line in data.splitlines():
        if not line:
            continue
        parts = line.split("/")
        if len(parts) != 8 or not parts[1].isdigit():
            raise ValueError(f"{image}: cannot inventory {path}: {line!r}")
        entries[parts[5]] = parts[2]
    if "." not in entries or ".." not in entries:
        raise ValueError(f"{image}: {path} is not a directory")
    return entries


def ext4_dump(image, source, destination, work):
    info = debugfs(image, f"stat {source}", work)
    if not re.search(rb"\bType:\s+regular\b", info):
        raise ValueError(f"{image}:{source}: expected regular file")
    debugfs(image, f"dump {source} {destination.name}", work)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ValueError(f"{image}:{source}: empty or missing")


def check_names(entries, allowed, directory):
    extra = {name for name in entries if "fstab" in name.lower()} - allowed
    if extra:
        raise ValueError(f"{directory}: unexpected alternate fstab(s): {', '.join(sorted(extra))}")


def read_vendor(image, work):
    with image.open("rb") as contents:
        magic = contents.read(4096)
    if magic[:4] == b"\x3a\xff\x26\xed":
        raw = work / "vendor.raw"
        run("simg2img", image, raw, cwd=work)
        image = raw
        with image.open("rb") as contents:
            magic = contents.read(4096)
    if magic[1080:1082] == b"\x53\xef":
        etc = ext4_entries(image, "/etc", work)
        check_names(etc, EXPECTED, "/vendor/etc")
        if "charger" in etc:
            check_names(ext4_entries(image, "/etc/charger", work), set(), "/vendor/etc/charger")
        for name in sorted(EXPECTED):
            ext4_dump(image, f"/etc/{name}", work / name, work)
        return work
    if magic[1024:1028] == b"\xe2\xe1\xf5\xe0":
        etc = work / "vendor" / "etc"
        run("fsck.erofs", "--no-preserve", f"--extract={etc.parent}", image, cwd=work)
        if etc.is_symlink():
            raise ValueError("/vendor/etc: expected real directory")
        check_names((file.name for file in etc.iterdir()), EXPECTED, "/vendor/etc")
        aux = etc / "charger"
        if aux.is_symlink():
            raise ValueError("/vendor/etc/charger: unexpected symlink")
        if aux.exists():
            check_names((file.name for file in aux.iterdir()), set(), "/vendor/etc/charger")
        for name in EXPECTED:
            if (etc / name).is_symlink() or not (etc / name).is_file():
                raise ValueError(f"/vendor/etc/{name}: expected regular file")
        return etc
    raise ValueError(f"{image}: unknown vendor image filesystem (expected ext4 or EROFS)")


def unpack_vendor(delivery, work):
    root = work / "rootfs.img"
    vendor = work / "vendor.img"
    with zipfile.ZipFile(delivery) as package:
        entries = [info for info in package.infolist() if info.filename == "rootfs.img"]
        if len(entries) != 1 or entries[0].is_dir():
            raise ValueError(f"{delivery}: expected exactly one rootfs.img member")
        with package.open(entries[0]) as source, root.open("wb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)
    ext4_dump(root, "/images/vendor.img", vendor, work)
    root.unlink()
    return vendor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--vendor-image", type=Path)
    source.add_argument("--delivery-zip", type=Path)
    args = parser.parse_args()
    artifact = args.vendor_image or args.delivery_zip
    try:
        with tempfile.TemporaryDirectory(prefix="skyward-p7-fstab-") as temp:
            work = Path(temp).resolve()
            image = (args.vendor_image.resolve() if args.vendor_image
                     else unpack_vendor(args.delivery_zip, work))
            etc = read_vendor(image, work)
            lint = Path(__file__).resolve().with_name("fstab-lint.py")
            run(sys.executable, lint, '--profile', args.profile.resolve(), '--fstab', etc / FSTAB,
                "--charger", etc / CHARGER, cwd=work)
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"{artifact}: {error}", file=sys.stderr)
        return 1
    print(f'{artifact}: vendor fstab readback passed ({args.profile})')
    return 0


if __name__ == "__main__":
    sys.exit(main())
