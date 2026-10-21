"""Local-first learning store (SQLite). Records which arm/strategy found defects; the next
generation weights toward strategies that found bugs. Held-out rows are never recorded.
OPT-IN SHARED is a setting that is OFF; the MVP makes NO network calls -- it only exports
attack SHAPES (strategy names + counts, never user text or rule names) to a local file."""
import json
import os
import sqlite3
from pathlib import Path

DATA = Path(os.environ.get("HB_DATA") or Path(__file__).resolve().parent.parent / "data")  # HB_DATA: tests, MCP child runs


class Store:
    def __init__(self, path=None):
        self.path = Path(path or DATA / "learning.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("CREATE TABLE IF NOT EXISTS stats (arm TEXT, strategy TEXT, tries INT, defects INT,"
                        " PRIMARY KEY (arm, strategy))")
        self.db.commit()

    def record(self, arm, strategy, tries, defects):
        self.db.execute("INSERT INTO stats VALUES (?,?,?,?) ON CONFLICT(arm,strategy) DO UPDATE SET "
                        "tries=tries+excluded.tries, defects=defects+excluded.defects", (arm, strategy, tries, defects))
        self.db.commit()

    def stats(self):
        return [dict(arm=a, strategy=s, tries=t, defects=d)
                for a, s, t, d in self.db.execute("SELECT arm,strategy,tries,defects FROM stats ORDER BY arm,strategy")]

    def weights(self, arm):
        # Laplace-smoothed defect rate; an untried strategy sits at the 0.5 prior, so it is explored.
        return {r["strategy"]: (r["defects"] + 1) / (r["tries"] + 2) for r in self.stats() if r["arm"] == arm}


def settings_path():
    return DATA / "settings.json"


def load_settings(path=None):
    p = Path(path or settings_path())
    s = {"share_shapes": False}
    try:
        s.update(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return s


def export_shapes(store, out_path):
    """Attack SHAPES only: arm, strategy name, tries, defects. No case text, no rule names."""
    shapes = [{k: r[k] for k in ("arm", "strategy", "tries", "defects")} for r in store.stats()]
    Path(out_path).write_text(json.dumps({"kind": "hb-attack-shapes-v1", "shapes": shapes}, indent=2), encoding="utf-8")
    return shapes
