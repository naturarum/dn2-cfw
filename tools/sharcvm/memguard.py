# SPDX-License-Identifier: GPL-2.0-or-later
"""Memory guard for the scripts that run VM jobs in a process pool.

Why: on 2026-10-18 `inject_cmp.py CMP3 --workers 6` leaked each VM's ~9.6 MB C struct (vm.py had no vm_free) until six
workers held 16 to 30 GB each and the Mac had to be rebooted. The leak is fixed (VM.__del__); this makes any future one
stop the run instead of the machine.

    import memguard
    def work(job):                      # runs in the worker
        ...
        memguard.check()                # raises WorkerMemoryError once this process' peak RSS passes the limit
        return result
    with ProcessPoolExecutor(n, max_tasks_per_child=memguard.TASKS_PER_CHILD) as ex:
        for r in memguard.pmap(ex, work, jobs, chunksize=4):    # on a failure: pending jobs cancelled, workers killed,
            ...                                                 # one clear message, exit 1

The limit is MEMGUARD_MB (default 2048: a worker normally sits at about 200 MB). It is checked against the peak RSS
(ru_maxrss, free to read), which only grows, so a leak is always caught at the next job boundary.
"""
import os
import resource
import sys

LIMIT_MB = int(os.environ.get("MEMGUARD_MB", "2048"))
TASKS_PER_CHILD = int(os.environ.get("MEMGUARD_TASKS", "250"))   # a fresh worker after this many jobs: a bounded leak


class WorkerMemoryError(RuntimeError):
    pass


def peak_rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024     # macOS: bytes, Linux: KiB


def check(limit_mb=None):
    mb = peak_rss_mb()
    lim = LIMIT_MB if limit_mb is None else limit_mb
    if mb > lim:
        raise WorkerMemoryError(f"worker pid {os.getpid()} peak RSS {mb:.0f} MB passed the {lim} MB limit "
                                f"(MEMGUARD_MB): a leak? stopping the run before it eats the machine")


def _abort(ex):
    ex.shutdown(wait=False, cancel_futures=True)
    for p in list((getattr(ex, "_processes", None) or {}).values()):
        try:
            p.kill()
        except Exception:
            pass


def pmap(ex, fn, jobs, chunksize=1):
    """ex.map that, when a job raises (WorkerMemoryError, a crash, Ctrl-C), cancels what is pending and kills the
    workers at once instead of letting the pool run the remaining jobs; a memory stop exits with its message"""
    try:
        yield from ex.map(fn, jobs, chunksize=chunksize)
    except WorkerMemoryError as e:
        _abort(ex)
        raise SystemExit(f"memguard: ABORTED: {e}") from e
    except (Exception, KeyboardInterrupt):
        _abort(ex)
        raise
