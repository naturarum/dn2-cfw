# SPDX-License-Identifier: GPL-2.0-or-later
"""Dynamic in-OS upgrade flash watch for the brick checker (check 12, T1.7b).

Drives the DN2 1.12 in-OS OS-upgrade WRITER FUN_40139104 in digikit's emulator
over the candidate's own container and watches every flash erase/program
command and its wire address against T1.4's allowed window
([0x80000, 0x380000), no protect/OTP opcode). The heavy lifting is in
scripts/t17b_flashwatch.py (built and demonstrated under T1.7b); this wrapper
runs it in digikit's environment as a child process (like bc_emuboot) and
reads back its watch.json.

Why the stock warm snapshot is faithful for a candidate: the writer
FUN_40139104 and the whole NOR driver are kept byte-identical by check 6, so
the code that issues the erase/program frames is the same bytes a candidate
would run on the unit. The only candidate-specific input is the container
length, which the harness takes from the candidate's own container and which
drives the erase/program extent exactly as on the unit. So this is a runtime
cross-check of check 3's in-window guarantee through the real DSPI0 driver, not
a re-run of stock. It is meaningful only when check 6 passes (the writer is
stock); the caller gates on that.

What it does NOT do (stated so the verdict stays honest): it drives the writer
directly, not organically through a SysEx-over-DIN upgrade session (assessed as
high-cost / low-value in T1.7b, because the pinned writer+driver produce the
same wire addresses either way); and, like the cold-boot watch, it only sees
the code the watched session runs, so it does not execute a flash gadget that
sits off the upgrade path (that is check 6/7's job).
"""
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(ROOT, 'scripts', 't17b_flashwatch.py')
VENV_PY = os.path.join(ROOT, 'vendor', 'digikit', '.venv', 'bin', 'python')
DIGIKIT = os.path.join(ROOT, 'vendor', 'digikit')
WARM_SNAP = os.path.join(ROOT, 'out', 'T2.4a', 'warm', 'warm800M.snap')
STOCK_SYX = os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx')


def available(snap=None):
    snap = snap or WARM_SNAP
    return (os.path.exists(SCRIPT) and os.path.exists(VENV_PY) and os.path.exists(snap)
            and os.path.exists(STOCK_SYX))


def run(candidate_syx, out_dir, snap=None, stock_syx=None, budget_s=240):
    """Run the dynamic upgrade watch on `candidate_syx`. The snapshot is restored
    with the stock .syx it was built from (so its flash manifest matches); the
    candidate's container is staged as the upgrade blob the writer reads. The
    writer+driver are byte-pinned to stock by check 6, so this is faithful.
    -> dict with the watch record (verdict under 'verdict') or {'error': ...}."""
    snap = snap or WARM_SNAP
    stock_syx = stock_syx or STOCK_SYX
    os.makedirs(out_dir, exist_ok=True)
    cmd = [VENV_PY, SCRIPT, '--snap', os.path.abspath(snap),
           '--syx', os.path.abspath(stock_syx),
           '--container-syx', os.path.abspath(candidate_syx),
           '--out', os.path.abspath(out_dir), '--mode', 'stock']
    log = os.path.join(out_dir, 'flashwatch.log')
    t0 = time.time()
    with open(log, 'w') as fh:
        p = subprocess.run(cmd, cwd=DIGIKIT, stdout=fh, stderr=subprocess.STDOUT,
                           env=dict(os.environ, DT2_SECTIONS='out/sections/dn2-1.12',
                                    PYTHONUNBUFFERED='1'),
                           timeout=budget_s + 60)
    wall = round(time.time() - t0, 1)
    wj = os.path.join(out_dir, 'watch.json')
    if p.returncode or not os.path.exists(wj):
        return {'error': 'flashwatch exited %d (log %s)' % (p.returncode, log), 'seconds': wall,
                'log': log}
    rec = json.load(open(wj))
    rec['wall_seconds'] = wall
    rec['log'] = log
    return rec
