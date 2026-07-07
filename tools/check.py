"""Check runner: discovers tests/test_*.py and runs each with this interpreter.

Every suite is a plain assert-style script (see docs/INTERFACES.md test
conventions): exit code 0 means PASS. One line is streamed per suite as it
finishes; a summary follows; the exit code is non-zero if anything failed.

Usage:
    .venv/bin/python tools/check.py            # run everything
    .venv/bin/python tools/check.py --only market   # substring filter

Stdlib only, offline. Suites run with cwd = repo root, exactly like
running `.venv/bin/python tests/test_<name>.py` by hand.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(ROOT, "tests")
SUITE_TIMEOUT_S = 300  # per-suite ceiling; the whole set runs in seconds


def discover(only: str | None = None) -> list[str]:
    """Sorted test file paths, optionally filtered by substring."""
    try:
        names = sorted(os.listdir(TESTS_DIR))
    except FileNotFoundError:
        return []
    paths = [
        os.path.join(TESTS_DIR, n)
        for n in names
        if n.startswith("test_") and n.endswith(".py")
    ]
    if only:
        paths = [p for p in paths if only in os.path.basename(p)]
    return paths


def _as_str(x) -> str:
    """TimeoutExpired carries bytes on POSIX but str on Windows (the
    stdlib re-reads via communicate() when text=True); accept both."""
    if isinstance(x, bytes):
        return x.decode(errors="replace")
    return x or ""


def run_suite(path: str) -> tuple[bool, float, str]:
    """Run one suite; return (passed, seconds, captured output)."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=SUITE_TIMEOUT_S,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        passed = proc.returncode == 0
    except subprocess.TimeoutExpired as exc:
        out = (_as_str(exc.stdout) + _as_str(exc.stderr)
               + f"\nTIMEOUT after {SUITE_TIMEOUT_S}s")
        passed = False
    except OSError as exc:  # interpreter/file vanished mid-run
        out, passed = f"could not run: {exc}", False
    return passed, time.monotonic() - t0, out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default=None, metavar="SUBSTR",
                    help="run only suites whose filename contains SUBSTR")
    args = ap.parse_args(argv)

    suites = discover(args.only)
    if not suites:
        print(f"no suites matched"
              f"{' --only ' + args.only if args.only else ''} in {TESTS_DIR}")
        return 2

    width = max(len(os.path.basename(p)) for p in suites)
    failures: list[tuple[str, str]] = []
    total_t = 0.0
    for path in suites:
        name = os.path.basename(path)
        passed, secs, out = run_suite(path)
        total_t += secs
        status = "PASS" if passed else "FAIL"
        print(f"{status}  {name:<{width}}  {secs:6.2f}s", flush=True)
        if not passed:
            failures.append((name, out))

    print(f"\n{len(suites) - len(failures)}/{len(suites)} suites passed"
          f" in {total_t:.1f}s")
    for name, out in failures:
        print(f"\n---- {name} output ----")
        print(out.rstrip() or "(no output)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
