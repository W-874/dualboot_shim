#!/usr/bin/env python3
"""Replay only Dada's r18 identity gate with stock-derived DT evidence on QEMU virt."""

import argparse
import csv
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skyward-qemu-replay'))
import replay

from fixtures import PROJECT, file_sha, regular, require, run

BASELINE = PROJECT / 'host-baseline/dada/images'
WORK = PROJECT / 'evidence/guest-r18/dada-twin'
# r18 prepared.json digest from dada-run01/exit.json. The prepared artifact in
# turn pins every file in the existing kernel and disk manifests.
PREPARED_SHA = '63930f72f318687f687edd08e38c27794965039cf68c99c2e31a7b1ea8734382'
# OS4.0.0.22 OTA partition manifest and P4.6 Dada OEM board.tsv, base/overlay 0.
VENDOR_SHA = '4a8077938debda8e2872043c35d6768b65b7d13121cf081069f261ea1a418404'
DTBO_SHA = '7d6927c2511676b4bd71f38ecc4397ff000ae82ee1e5428c5d92cc88f2e07f91'
BASE_SHA = '7b4fc5af22ef5ec9368d42c79657cc3def8be99209194c3876eb4e34057cf41a'
OVERLAY_SHA = '023acba0e66f381182a35c5d7581d9dae01c1694a648280b155ef0343faa6152'
# vendor_boot v4: one 4096 B header page, then 37122089 B ramdisk rounded up.
VENDOR_DTB_OFFSET = 4096 + ((37122089 + 4095) // 4096) * 4096
TOUCH_NODE = '/soc/qcom,qupv3_1_geni_se@ac0000/spi@a88000/synaptics_tcm@0'
TOUCH_PROP = 'synaptics,default-fw-image-name'
UFS_REFUSAL = 'Dada host partitions are not on the expected UFS controller'


def extract(src, offset, size, dst, digest):
    with src.open('rb') as f:
        f.seek(offset)
        dst.write_bytes(f.read(size))
    replay.checked_input(dst, digest)


def project_identity(out, fdt_bin, virt):
    def tool(name):
        return fdt_bin / name if fdt_bin else name

    def get(tree, node, prop):
        return run(tool('fdtget'), '-t', 'bx', tree, node, prop,
                   capture_output=True, text=True).stdout.split()

    vendor = replay.checked_input(BASELINE / 'vendor_boot.img', VENDOR_SHA)
    dtbo = replay.checked_input(BASELINE / 'dtbo.img', DTBO_SHA)
    base, overlay = out / 'stock-base0.dtb', out / 'stock-overlay0.dtbo'
    applied, fixture = out / 'stock-applied.dtb', out / 'virt-dada-identity.dtb'
    extract(vendor, VENDOR_DTB_OFFSET, 519842, base, BASE_SHA)
    extract(dtbo, 64, 559935, overlay, OVERLAY_SHA)
    run(tool('fdtoverlay'), '-i', base, '-o', applied, overlay)

    def raw(values):
        return bytes(int(value, 16) for value in values)

    require(raw(get(virt, '/', 'compatible')) == b'linux,dummy-virt\0',
            'unexpected QEMU virt root compatible')
    # Only the properties read by the gate, not the stock MMIO nodes, stock
    # bootconfig, fake sysfs paths, or an invented bootloader board string.
    fixture.write_bytes(virt.read_bytes())
    props = {}
    for node, prop in (('/', 'compatible'), ('/', 'model'), (TOUCH_NODE, TOUCH_PROP)):
        value = get(applied, node, prop)
        require(value and raw(value).endswith(b'\0'), f'missing stock DT string: {node}/{prop}')
        run(tool('fdtput'), '-p', '-t', 'bx', fixture, node, prop, *value)
        require(get(fixture, node, prop) == value, f'projected DT property changed: {node}/{prop}')
        props.setdefault(node, {})[prop] = raw(value)[:-1].decode('ascii').split('\0')
    replay.write_json(out / 'dt-provenance.json', dict(
        ota='OS4.0.0.22.XOCCNXM', vendor_boot_sha256=VENDOR_SHA, dtbo_sha256=DTBO_SHA,
        test_base_index=0, test_base_offset_in_vendor_boot=VENDOR_DTB_OFFSET, test_base_sha256=BASE_SHA,
        overlay_index=0, overlay_offset_in_dtbo=64, overlay_sha256=OVERLAY_SHA,
        applied_sha256=file_sha(applied), virt_original_sha256=file_sha(virt),
        virt_projected_sha256=file_sha(fixture), projected_properties=props,
        stock_dtb_selection_on_phone_verified=False,
    ))
    return fixture


def inspect(out, work, overlay, rc):
    ranges = work / 'twin/ranges.tsv'
    with ranges.open() as f:
        misc, = (row for row in csv.DictReader(f, delimiter='\t') if row['range'] == 'misc@0')
    require(misc['size'] == '2048', 'unexpected misc BCB slice size')
    bcb = out / 'bcb.bin'
    replay.image_slice(overlay, int(misc['offset']), int(misc['size']), bcb)
    raw = bcb.read_bytes()
    require(len(raw) == 2048, 'truncated misc BCB')
    command = raw[:32].split(b'\0', 1)[0].decode(errors='replace')
    recovery = raw[64:832].split(b'\0', 1)[0].decode(errors='replace').splitlines()
    serial = (out / 'serial.log').read_text(errors='replace')
    handoff = 'handing over to /guest-entry' in serial
    identity_refused = replay.DADA_REFUSAL in serial
    ufs_refused = UFS_REFUSAL in serial and command == 'boot-recovery' and recovery == [
        'recovery', '--system_stopped', f'--reason=environment: {UFS_REFUSAL}',
    ]
    second_stage = 'init second stage started' in serial
    boot_complete = 'sys.boot_completed=1' in serial
    with tempfile.TemporaryDirectory(prefix='readback-', dir=out) as tmp:
        disk = Path(tmp) / 'after.raw'
        run('qemu-img', 'convert', '-f', 'qcow2', '-O', 'raw', '-S', '4k', overlay, disk)
        with (out / 'ranges.log').open('xb') as log:
            compared = subprocess.run([sys.executable, str(replay.twin.HERE / 'fixtures.py'), 'verify',
                                       '--disk', str(disk), '--ranges', str(ranges), '--allow-mutable'],
                                      stdout=log, stderr=subprocess.STDOUT)
    identity_observed = handoff and not identity_refused and ufs_refused
    ok = rc == 0 and identity_observed and not second_stage and not boot_complete and compared.returncode == 0
    report = dict(scope='offline identity gate only; expect UFS refusal on virtio PCI',
                  qemu_exit_status=rc, prepared_sha256=PREPARED_SHA, bcb_sha256=file_sha(bcb),
                  command=command, recovery=recovery, handoff_seen=handoff,
                  identity_refusal_seen=identity_refused, next_ufs_refusal_read_back=ufs_refused,
                  identity_gate_crossed_in_replay=identity_observed,
                  android_second_stage_seen=second_stage, boot_completed_marker_seen=boot_complete,
                  protected_ranges_unchanged=compared.returncode == 0,
                  expected_identity_only_result=ok)
    replay.write_json(out / 'readback.json', report)
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, type=Path, help='local qemu-system-aarch64 executable')
    parser.add_argument('--qemu-libs', type=Path, help='LD_LIBRARY_PATH override for the local QEMU only')
    parser.add_argument('--fdt-bin', type=Path, help='directory with fdtget/fdtput/fdtoverlay (default: PATH)')
    parser.add_argument('--out', required=True, type=Path, help='new run directory; reserve disk space for raw readback')
    args = parser.parse_args()
    replay.checked_input(WORK / 'prepared.json', PREPARED_SHA)
    work, device = replay.prepared(WORK, verify=True)
    base = regular(work / 'twin/disk.raw')
    require(device == 'dada' and not base.stat().st_mode & 0o222, 'expected read-only Dada disk')
    qemu = regular(args.qemu.resolve())
    out = args.out.resolve()
    require(not out.is_relative_to(work), 'output must not be inside the original prepared twin')
    out.mkdir(mode=0o700)

    overlay = out / 'disk.qcow2'
    run('qemu-img', 'create', '-f', 'qcow2', '-F', 'raw', '-b', base, overlay)
    kernel = work / 'kernel'
    block = {'driver': 'qcow2', 'node-name': 'twin', 'file': {'driver': 'file', 'filename': str(overlay)},
             'backing': {'driver': 'raw', 'read-only': True, 'file': {'driver': 'file', 'filename': str(base)}}}
    launch = [str(qemu)]
    if args.qemu_libs:
        launch = ['env', f'LD_LIBRARY_PATH={args.qemu_libs.resolve()}', *launch]
    version = run(*launch, '--version', capture_output=True, text=True).stdout
    machine = 'virt,accel=tcg'
    common = ['-cpu', 'max', '-smp', '4', '-m', '4096', '-no-user-config', '-nodefaults', '-display', 'none',
              '-serial', 'stdio', '-monitor', 'none', '-nic', 'none', '-no-reboot', '-kernel', str(kernel / 'Image'),
              '-initrd', str(kernel / 'initramfs.img'), '-append', (kernel / 'cmdline.txt').read_text().strip(),
              '-blockdev', json.dumps(block), '-device', 'virtio-blk-pci,drive=twin']
    virt = out / 'virt-original.dtb'
    # Escape commas for QEMU's -machine keyval syntax.
    dump = ['timeout', '--kill-after=10s', '60s', *launch, '-machine',
            machine + ',dumpdtb=' + str(virt).replace(',', ',,'), *common]
    (out / 'command.log').write_text(version + shlex.join(dump) + '\n')
    with (out / 'dumpdtb.log').open('xb') as log:
        run(*dump, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    dt = project_identity(out, args.fdt_bin, regular(virt))
    cmd = ['timeout', '--kill-after=10s', '120s', *launch, '-machine', machine, *common, '-dtb', str(dt)]
    with (out / 'command.log').open('a') as log:
        log.write(shlex.join(cmd) + '\n')
    with (out / 'serial.log').open('xb') as log:
        rc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT).returncode
    return inspect(out, work, overlay, rc)


if __name__ == '__main__':
    sys.exit(main())
