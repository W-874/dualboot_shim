#!/usr/bin/env python3
"""Run and judge fuxi QEMU scenarios on offline, test-only overlays."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from prepare import (EVIDENCE, RUN_ERRORS, add_arguments, check_space, prepare,
                     replay, require, run, sha)
import verdict
import volumes


QEMU = os.environ.get('QEMU') or shutil.which('qemu-system-aarch64')
CLANG = (Path(os.environ['ANDROID_NDK_HOME']) / 'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang'
         if 'ANDROID_NDK_HOME' in os.environ else None)
SCENARIOS = ('S1', 'S2', 'S3', 'S4', 'S5')


def vm(args, work, out, overlay, limit):
    base = work / 'twin/disk.raw'
    kernel = work / 'kernel'
    backing = {'driver': 'raw', 'read-only': True, 'file': {'driver': 'file', 'filename': str(base)}}
    block = {'driver': 'qcow2', 'node-name': 'twin', 'backing': backing,
             'file': {'driver': 'file', 'filename': str(overlay)}}
    cmdline = (kernel / 'cmdline.txt').read_text().strip()
    cmd = ['timeout', '--kill-after=10s', f'{limit}s', str(args.qemu), '-machine', 'virt,accel=tcg',
           '-cpu', 'max', '-smp', '4', '-m', '4096', '-nodefaults', '-no-user-config', '-nic', 'none',
           '-display', 'none', '-serial', 'stdio', '-monitor', 'none', '-no-reboot',
           '-kernel', str(kernel / 'Image'), '-initrd', str(kernel / 'initramfs.img'), '-append', cmdline,
           '-blockdev', json.dumps(block), '-device', 'qemu-xhci,id=xhci', '-device', 'usb-storage,drive=twin,bus=xhci.0']
    # A test guest with an Android UART shell can take `reboot -p` via this optional socket.
    with tempfile.TemporaryDirectory(prefix='r26-uart-') as td:
        if args.console:
            tty = Path(td) / 'tty'
            cmd[cmd.index('-serial')+1] = 'chardev:uart'
            require(',' not in str(out), '--console does not support commas in the output path')
            cmd += ['-chardev', f'socket,id=uart,path={tty},server=on,wait=off,logfile={out / "serial.log"}']
            print(f'{out.name} test UART: socat STDIO,rawer UNIX-CONNECT:{tty}', flush=True)
        version = run(args.qemu, '--version', capture_output=True, text=True).stdout
        (out / 'command.log').write_text(version + shlex.join(cmd) + '\n')
        with (out / ('launcher.log' if args.console else 'serial.log')).open('xb') as f:
            rc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT).returncode
    status = {0: 'qemu-exit-inspect-serial', 124: 'host-timeout', 137: 'sigkill'}.get(rc, 'launcher-error')
    replay.write_json(out / 'exit.json', dict(work=str(work), device='fuxi', exit_status=rc, status=status,
                                              limit=limit, prepared_sha256=sha(work / 'prepared.json')))
    print(f'{out.name}: {status} ({rc})', flush=True)
    require((out / 'serial.log').exists(), 'QEMU did not open the serial device')


def readback(work, out, overlay):
    rows = volumes.ranges(work)
    offset, length = volumes.extent(work, 'userdata')
    with tempfile.TemporaryDirectory(prefix='readback-', dir=out) as td:
        tmp = Path(td)
        raw = tmp / 'after.raw'
        run('qemu-img', 'convert', '-f', 'qcow2', '-O', 'raw', '-S', '4k', overlay, raw)
        with (out / 'ranges.log').open('w') as f, raw.open('rb') as disk:
            good = True
            at = 0
            print('range\tstatus\tpolicy\texpected_sha256\tactual_sha256', file=f)
            for row in rows:
                lo, size = int(row['offset']), int(row['size'])
                require(lo == at and size > 0, 'range manifest does not cover disk exactly once')
                at += size
                if row['policy'] == 'protected':
                    actual = replay.twin.sum_range(disk, lo, size)
                    ok = actual == row['sha256']
                    good &= ok
                    print(row['range'], 'OK' if ok else 'CHANGED', 'protected', row['sha256'], actual,
                          file=f, sep='\t')
            require(at == raw.stat().st_size, 'range manifest does not cover actual disk')
            disk.seek(volumes.extent(work, 'misc')[0])
            bcb = disk.read(2048)
            require(len(bcb) == 2048, 'short misc read')
            (out / 'bcb.bin').write_bytes(bcb)
        root = tmp / 'root.ext4'
        run('qemu-img', 'convert', '--image-opts', '-O', 'raw', '-S', '4k',
            f'driver=raw,offset={offset},size={length},file.driver=file,file.filename={str(raw).replace(",", ",,")}', root)
        raw.unlink()
        with (out / 'private-volume-proof.log').open('w') as log:
            state = volumes.proof(root, tmp, log)
    replay.write_json(out / 'readback.json', dict(protected_ranges_ok=good, volumes=state,
        bcb_sha256=hashlib.sha256(bcb).hexdigest(), bcb_restored_bytes=bcb == (out / 'before-bcb.bin').read_bytes(),
        command=bcb[:32].split(b'\0', 1)[0].decode(errors='replace'),
        recovery=bcb[64:832].split(b'\0', 1)[0].decode(errors='replace').splitlines()))


def checked_overlay(work, overlay):
    replay.twin.regular(overlay)
    info = json.loads(run('qemu-img', 'info', '-f', 'qcow2', '--output=json', overlay, capture_output=True).stdout)
    require(info.get('backing-filename') == str(work / 'twin/disk.raw')
            and info.get('backing-filename-format') == 'raw', 'wrong overlay backing image')


def scenario(args, profile, root, work, name):
    out = root / name
    out.mkdir(mode=0o700)
    check_space(out, out / 'df.log')
    overlay = out / 'disk.qcow2'
    if name in ('S2', 'S3'):
        first = root / 'S1'
        require((first / 'exit.json').is_file() and (first / 'readback.json').is_file(), 'S1 seed unavailable')
        require(json.loads((first / 'readback.json').read_text())['protected_ranges_ok'], 'S1 protected ranges changed')
        checked_overlay(work, first / overlay.name)
        run('cp', '--reflink=auto', '--sparse=always', first / overlay.name, overlay)
        replay.write_json(out / 'seed.json', dict(scenario='S1', overlay_sha256=sha(overlay),
                        exit=json.loads((first / 'exit.json').read_text())['status'],
                        orderly_shutdown=verdict.events(first / 'serial.log')['orderly_shutdown'] is not None))
    else:
        run('qemu-img', 'create', '-f', 'qcow2', '-F', 'raw', '-b', work / 'twin/disk.raw', overlay)
    checked_overlay(work, overlay)
    volumes.slice_bcb(work, overlay, out / 'before-bcb.bin')
    raw = (out / 'before-bcb.bin').read_bytes()
    require(len(raw) == 2048 and raw[0] == raw[64] == 0, 'seed BCB already requests recovery or other work')
    if name in ('S3', 'S4'):
        volumes.inject(work, overlay, out, name, profile, args.clang, args.structural_s3)
    check_space(out, out / 'df.log')
    vm(args, work, out, overlay, args.watchdog_limit if name == 'S4' else args.limit)
    # VM has returned and no longer writes the overlay.
    checked_overlay(work, overlay)
    readback(work, out, overlay)
    return verdict.judge(name, out)


def matrix(args, root):
    work, device = replay.prepared(root / 'work', verify=True)
    require(device == 'fuxi' and not (work / 'twin/disk.raw').stat().st_mode & 0o222,
            'expected fuxi and a read-only base disk')
    profile = json.loads((work / 'prepared.json').read_text())['package']
    profile = Path(profile).name.removeprefix('uwu-fuxi-').removesuffix('-test.zip')
    require(profile in ('official', 'softkm'), 'unexpected prepared package profile')
    selected = set(args.scenarios)
    if selected & {'S2', 'S3', 'S5'}:
        selected.add('S1')
    if 'S5' in selected:
        selected.add('S2')
    results = {}
    for name in SCENARIOS:
        if name not in selected:
            continue
        out = root / name
        try:
            if (out / 'verdict.json').is_file() and args.prepared:
                results[name] = json.loads((out / 'verdict.json').read_text())
            elif name == 'S5':
                out.mkdir(mode=0o700)
                results[name] = verdict.s5(root)
            else:
                results[name] = scenario(args, profile, root, work, name)
        except RUN_ERRORS as err:
            results[name] = dict(result='blocked', error=str(err))
            if out.is_dir():
                replay.write_json(out / 'error.json', results[name])
            print(f'{name}: {err}', file=sys.stderr)
            if 'less than 100 GiB' in str(err):
                break
    replay.write_json(root / 'matrix.json', results)
    print(', '.join(f'{s}: {item["result"]}' for s, item in results.items()), flush=True)
    return int(len(results) != len(selected) or any(v['result'] != 'pass' for v in results.values()))


def main():
    # A prepared control can be resumed without rebuilding its verified package.
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--prepared', type=lambda s: Path(s).resolve(), help='resume an existing work directory')
    cli.add_argument('--scenarios', nargs='+', choices=SCENARIOS, default=SCENARIOS)
    cli.add_argument('--limit', type=int, help='S1–S3 host timeout (official: 750 s, softkm: 1200 s)')
    cli.add_argument('--watchdog-limit', type=int, default=900, help='S4 host timeout; must exceed 600 s')
    cli.add_argument('--qemu', type=Path, default=QEMU, required=QEMU is None)
    cli.add_argument('--clang', type=lambda s: Path(s).resolve(), default=CLANG)
    cli.add_argument('--structural-s3', action='store_true', help='permit invalid test key if no real softkm keys exist')
    cli.add_argument('--console', action='store_true', help='expose a temporary UART socket (only for offline test UART guests)')
    add_arguments(cli, optional=True)
    args = cli.parse_args()
    if args.prepared:
        require(not any((args.guest_entry, args.images, args.out, args.android_out, args.five_images,
                         args.system_init, args.usb_prefix, args.build_lock)) and args.prepared.is_relative_to(EVIDENCE),
                '--prepared cannot be combined with pack arguments and must be under SKYWARD_REPLAY_OUT')
        root = args.prepared
    else:
        if not (args.guest_entry and args.images and args.out):
            cli.error('guest_entry, images and --out are required unless --prepared is supplied')
        root = prepare(args)
    if args.limit is None:
        args.limit = 1200 if (root / 'package/uwu-fuxi-softkm-test.zip').is_file() else 750
    require(args.limit > 0 and args.watchdog_limit > 600, 'invalid timeout')
    check_space(root, root / 'df.log')
    return matrix(args, root)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except RUN_ERRORS as err:
        sys.exit(f'harness: {err}')
