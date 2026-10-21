# SPDX-License-Identifier: GPL-2.0-or-later
"""memwatch: keep this repo's jobs from taking the Mac's memory (2026-10-18: a sharcvm leak froze the machine).

Every few seconds it lists the processes whose working directory or command line is inside this repo (the
emulators, sharcvm runs, brickcheck, build scripts; never the Claude app or anything else) and:
  * kills any one of them whose resident memory passes --max-proc-mb (default 6144);
  * when the system-wide free memory (memory_pressure -Q) falls under --min-free-pct (default 12), kills the
    largest of them, one per pass, until it recovers.
SIGTERM first, SIGKILL 5 s later. Every kill is appended to out/memwatch.log with the process's command line.

    python3 tools/memwatch.py [--interval 10] [--max-proc-mb 6144] [--min-free-pct 12]
"""
import argparse
import os
import re
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "out" / "memwatch.log"
ME = os.getpid()


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def free_pct():
    try:
        out = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=20).stdout
        m = re.search(r"free percentage:\s*(\d+)%", out)
        return int(m.group(1)) if m else 100
    except Exception:
        return 100


def cwd_of(pid):
    try:
        out = subprocess.run(["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"], capture_output=True,
                             text=True, timeout=10).stdout
        for line in out.splitlines():
            if line.startswith("n"):
                return line[1:]
    except Exception:
        pass
    return ""


def repo_jobs():
    """(pid, rss_mb, command) of this user's processes that belong to this repo"""
    out = subprocess.run(["ps", "-x", "-o", "pid=,rss=,command="], capture_output=True, text=True).stdout
    jobs = []
    root = str(ROOT)
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, rss, cmd = int(parts[0]), int(parts[1]) // 1024, parts[2]
        if pid == ME or "memwatch.py" in cmd:
            continue
        if "/Applications/" in cmd or "Claude" in cmd:
            continue                      # never the app or its helpers
        if not re.search(r"(^|/)(python3?(\.\d+)?|Python)\b", cmd.split()[0]):
            continue                      # our heavy jobs are all Python (sharcvm, digikit, brickcheck)
        if root in cmd or cwd_of(pid).startswith(root):
            jobs.append((pid, rss, cmd))
    return jobs


def kill(pid, why, cmd):
    log(f"KILL pid {pid}: {why}: {cmd[:300]}")
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(5)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as e:
        log(f"  kill failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--max-proc-mb", type=int, default=6144)
    ap.add_argument("--min-free-pct", type=int, default=12)
    a = ap.parse_args()
    log(f"start: every {a.interval} s, max {a.max_proc_mb} MB per job, min {a.min_free_pct} % free")
    while True:
        jobs = repo_jobs()
        for pid, rss, cmd in jobs:
            if rss > a.max_proc_mb:
                kill(pid, f"rss {rss} MB > {a.max_proc_mb} MB", cmd)
        fp = free_pct()
        if fp < a.min_free_pct:
            jobs = sorted(repo_jobs(), key=lambda j: -j[1])
            if jobs:
                pid, rss, cmd = jobs[0]
                kill(pid, f"system free {fp} % < {a.min_free_pct} %, largest repo job ({rss} MB)", cmd)
            else:
                log(f"system free {fp} %, no repo job to kill")
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
