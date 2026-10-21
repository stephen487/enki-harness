import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hb.engine import run  # noqa: E402
from hb.learning import Store  # noqa: E402
from hb.task import Task  # noqa: E402


@pytest.fixture
def task():
    t = Task.load(ROOT / "examples" / "insult_task.json")
    t.system = str(ROOT / "examples" / "planted_filter.py")
    return t


@pytest.fixture
def go(tmp_path):
    def _go(task, system_file=None, **kw):
        if system_file:
            task.system = str(ROOT / "examples" / system_file)
        kw.setdefault("store", Store(tmp_path / "learning.db"))
        return run(task, run_root=tmp_path / "runs", **kw)
    return _go
