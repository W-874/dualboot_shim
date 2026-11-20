#!/usr/bin/env python3
"""Compare tracked guest-entry bytes with the original heritage tarball."""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = 'daa649b99e537aa13632665343767c91683baf4a'
ARCHIVE_SHA256 = '72eaecaf8a01756b21ea371969c70e0a816e73547b249b44039423bfed0ab691'
CHANGED = {'Cargo.toml', 'src/main.rs', 'src/policy.rs', 'src/boot.rs', 'src/storage.rs'}
ADDED = {'src/profile.rs'}


def manifest(archive, prefix):
    files = {}
    for member in archive:
        if member.isdir():
            continue
        if not member.isfile() or not member.name.startswith(prefix):
            raise ValueError(f'unexpected archive entry: {member.name}')
        name = member.name.removeprefix(prefix)
        if name in files:
            raise ValueError(f'duplicate archive entry: {name}')
        files[name] = (member.mode & 0o111, hashlib.file_digest(archive.extractfile(member), 'sha256').hexdigest())
    return files


def tracked(ref):
    data = subprocess.check_output(['git', '-C', ROOT, 'archive', '--format=tar', ref, 'guest-entry/'])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return manifest(archive, 'guest-entry/')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('snapshot', type=Path)
    cli.add_argument('--ref', default='HEAD')
    args = cli.parse_args()
    with args.snapshot.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != ARCHIVE_SHA256:
            cli.error('original tarball SHA-256 mismatch')
    with tarfile.open(args.snapshot, 'r:gz') as archive:
        original = manifest(archive, 'skyward-guest-entry/')
    if tracked(ORIGINAL) != original:
        cli.error('original archive commit differs from tarball')
    current = tracked(args.ref)
    missing = original.keys() - current.keys()
    added = current.keys() - original.keys()
    changed = {key for key in current.keys() & original.keys() if current[key] != original[key]}
    print(f'{len(original)} original files; {len(original) - len(missing) - len(changed)} unchanged')
    for label, names in [('changed', changed), ('added', added), ('missing', missing)]:
        for name in sorted(names):
            print(f'{label}  guest-entry/{name}')
    return int(bool(missing or added != ADDED or changed != CHANGED))


if __name__ == '__main__':
    raise SystemExit(main())
