#!/usr/bin/env python3
"""Check the actual r26e images without mounting them or writing to OUT."""
import os
from pathlib import Path
import runpy
import tempfile

ROOT = Path(__file__).resolve().parents[3]
E = ROOT / 'evidence/r26e'
C = E / 'checks'
SRC = ROOT / 'uwuAOSP-src'
OUT = SRC / (ROOT / 'evidence/fuxi-r25-a-storage/build-arm64/fuxi/OUT_DIR').read_text().strip()
PROD = OUT / 'target/product/fuxi'
TOOLS = SRC / '.tools/usr'
os.environ.update(TMPDIR=str(C), LC_ALL='C', PATH='/usr/sbin:' + os.environ['PATH'])
mod = runpy.run_path(str(ROOT / 'tools/fuxi-final-lint/verify.py'))
run, sha = mod['run'], mod['sha']


def extract_run(log, *args, **kwargs):
    # rdump restores image owners; emulate only that for this read-only extractor.
    return run(log, *('fakeroot', '--', *args) if kwargs.get('debugfs') else args, **kwargs)


mod['extract'].__globals__['run'] = extract_run
with (C / 'fsck.log').open('w') as log:
    run(log, 'e2fsck', '-fn', PROD / 'system.img')
    run(log, TOOLS / 'bin/fsck.erofs', PROD / 'vendor.img',
        env=dict(os.environ, LD_LIBRARY_PATH=f'{TOOLS}/lib64:{TOOLS}/lib'))
with tempfile.TemporaryDirectory(prefix='images-', dir=C) as tmp:
    work = Path(tmp)
    for part in mod['ROM']:
        os.link(PROD / part, work / part)
    with (C / 'image-readback.log').open('w') as log:
        trees = mod['rom_roots'](log, work, OUT)
        daemon = trees['system'] / 'bin/gatekeeperd'
        built = PROD / 'system/bin/gatekeeperd'
        old = C / 'r26d.gatekeeperd'
        assert sha(log, daemon) == sha(log, built) != sha(log, old)
        assert daemon.read_bytes() == built.read_bytes()
        (C / 'r26e.gatekeeperd').write_bytes(daemon.read_bytes())
        rc = trees['system'] / 'etc/init/gatekeeperd.rc'
        assert rc.read_bytes() == (PROD / 'system/etc/init/gatekeeperd.rc').read_bytes()
        print(rc.read_text(), file=log)
        prop = trees['system'] / 'build.prop'
        guest = [x for x in prop.read_text().splitlines() if x.startswith('ro.skyward.guest=')]
        assert guest == ['ro.skyward.guest=true']
        print(prop, *guest, file=log)
        inventory = PROD / 'installed-files.txt'
        assert any(line.split()[-1] == '/system/bin/gatekeeperd'
                   for line in inventory.read_text().splitlines() if line.strip())
        print(f'{inventory}: /system/bin/gatekeeperd', file=log)
    kernel = ROOT / 'evidence/P1.3/fuxi'
    release = (kernel / 'release.txt').read_text().splitlines()[0].split()[2]
    variables = mod['records'](ROOT / 'evidence/r26d/build/vintf-build-vars.txt', mod['SKU_KEYS'])
    with (C / 'vintf.log').open('w') as log:
        mod['check_vintf'](log, trees, SRC / 'out-dada', work, variables, release, kernel / 'ikconfig')
print('PASS system and vendor fsck, system readback, fuxi VINTF')
