"""Found on the real machine 2026-10-21: `hb install` wrote the config, then CRASHED printing a check mark on a
standard Windows console (cp1252), so the user saw a traceback after a successful install. The other tests inject
their own printer, so the real print path was never exercised. These run the real CLI in a subprocess, with a
cp1252 console and FAKE home/appdata folders (never the real machine's)."""
import json
import os
import subprocess
import sys
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _real_configs():
    """The REAL machine's Claude Desktop configs (Store build + classic), hashed: they must never change here."""
    e = os.environ
    found = list(Path(e.get("LOCALAPPDATA", "")).glob("Claude-Data/*/claude_desktop_config.json")) if e.get(
        "LOCALAPPDATA") else []
    if e.get("APPDATA"):
        found.append(Path(e["APPDATA"]) / "Claude" / "claude_desktop_config.json")
    return {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in found if f.exists()}


@pytest.fixture(autouse=True)
def real_machine_untouched():
    before = _real_configs()
    yield
    assert _real_configs() == before, "a REAL Claude Desktop config changed during a test"


def _run(tmp_path, *args):
    home, appdata, local = tmp_path / "home", tmp_path / "appdata", tmp_path / "local"
    for d in (home, appdata / "Claude", local):
        d.mkdir(parents=True, exist_ok=True)
    # Every folder the installer reads is redirected below, so the pytest guard marker can be cleared here.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "HB_", "PYTEST_"))}
    env.update({"USERPROFILE": str(home), "HOME": str(home), "APPDATA": str(appdata), "LOCALAPPDATA": str(local),
                "PYTHONIOENCODING": "cp1252", "HB_DATA": str(tmp_path / "hbdata")})
    r = subprocess.run([sys.executable, "-m", "hb", *args], cwd=ROOT, env=env, capture_output=True, timeout=120)
    return r, appdata / "Claude" / "claude_desktop_config.json"


def test_install_on_a_cp1252_console_finishes_cleanly(tmp_path):
    r, cfg = _run(tmp_path, "install", "--tools", "claude-desktop", "--allow", str(tmp_path), "--yes")
    err = r.stderr.decode("cp1252", "replace")
    assert "UnicodeEncodeError" not in err and "Traceback" not in err, err[-800:]
    assert r.returncode == 0, (r.returncode, err[-800:])
    assert "enki-harness" in json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]


def test_uninstall_on_a_cp1252_console_finishes_cleanly(tmp_path):
    _run(tmp_path, "install", "--tools", "claude-desktop", "--allow", str(tmp_path), "--yes")
    r, cfg = _run(tmp_path, "uninstall", "--tools", "claude-desktop", "--yes")
    err = r.stderr.decode("cp1252", "replace")
    assert "Traceback" not in err and r.returncode == 0, err[-800:]
    assert "enki-harness" not in json.loads(cfg.read_text(encoding="utf-8")).get("mcpServers", {})
