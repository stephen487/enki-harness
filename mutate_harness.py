"""Mutation check on the harness itself: switch off each of its own checks, one at a time,
and run the suite. Every mutant must turn the suite RED; a green mutant = an untested check.

  venv\\Scripts\\python.exe mutate_harness.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from hb.checks import CHECKS

ROOT = Path(__file__).resolve().parent


def main():
    killed, survived = [], []
    for name in CHECKS:
        env = dict(os.environ, HB_DISABLE=name)
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "tests")],
                           cwd=ROOT, env=env, capture_output=True, text=True)
        m = re.search(r"(\d+) failed", p.stdout)
        failed = int(m.group(1)) if m else 0
        (killed if failed else survived).append(name)
        print(f"{name:20s} {'RED (killed)' if failed else 'GREEN (SURVIVED: untested check)'}  failed={failed}")
    print(f"\n{len(killed)}/{len(CHECKS)} harness mutants killed; survivors: {survived or 'none'}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
