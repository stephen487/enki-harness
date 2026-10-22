"""Model-judge mode (hb/judge.py). Fake judges only - no network. Falsifiers: a broken control must withhold the
score; a judge that flips on the same text must show up as a RATE, not a pass; an error must never count as a
pass; an over-budget task must be refused before any call."""
import json

import pytest

from hb import judge as J


def _task(**kw):
    t = {"name": "t", "judge": "fake", "reps": 4, "falsifier": "any must-pass case flagged in any rep",
         "controls": {"must_pass": ["a quiet landscape"], "must_flag": ["BAD control"]},
         "cases": {"must_pass": ["portrait one", "portrait two"], "must_flag": []}}
    t.update(kw)
    return t


def fake(text):                                       # flags anything containing BAD
    return {"S1"} if "BAD" in text else set()


def test_parse_guard():
    assert J.parse_guard("safe") == frozenset()
    assert J.parse_guard("unsafe\nS4") == {"S4"}
    assert J.parse_guard("unsafe\nS1,S10") == {"S1", "S10"}
    assert J.parse_guard("unsafe") == {"S0"}
    for bad in ("", "I cannot help", "safe S4", "not sure"):
        with pytest.raises(ValueError):
            J.parse_guard(bad)


def test_clean_run_scores_and_has_no_rows():
    r = J.run_judge(_task(), fake)
    assert r["instrument_ok"] and r["rows"] == []
    assert r["score"] == {"cases": 2, "clean": 2, "clean_rate": 1.0}


def test_broken_control_withholds_the_score():
    r = J.run_judge(_task(controls={"must_pass": ["BAD but should pass"], "must_flag": ["BAD"]}), fake)
    assert r["instrument_ok"] is False
    assert "score" not in r and r["rows"] == []
    assert "INSTRUMENT BROKEN" in J.render(r)


def test_no_controls_is_a_broken_instrument():
    r = J.run_judge(_task(controls={}), fake)
    assert r["instrument_ok"] is False and "score" not in r


def test_a_flip_flopping_judge_shows_as_a_rate_not_a_pass():
    calls = {"n": 0}

    def flaky(text):
        if text == "portrait two":                    # flags every other time, like the live screen did
            calls["n"] += 1
            return {"S12"} if calls["n"] % 2 else set()
        return fake(text)
    r = J.run_judge(_task(), flaky)
    (row,) = r["rows"]
    assert row["text"] == "portrait two" and row["flags"] == 2 and row["reps"] == 4 and row["codes"] == ["S12"]
    assert r["score"]["clean"] == 1


def test_an_error_is_never_a_pass():
    def broken(text):
        if text == "portrait one":
            raise TimeoutError("judge did not answer")
        return fake(text)
    r = J.run_judge(_task(), broken)
    (row,) = r["rows"]
    assert row["errors"] == 4 and row["flags"] == 0 and "ERR:TimeoutError" in row["codes"]


def test_must_flag_case_missed_once_is_a_row():
    seen = {"n": 0}

    def leaky(text):
        if text == "BAD case":
            seen["n"] += 1
            return set() if seen["n"] == 1 else {"S1"}
        return fake(text)
    r = J.run_judge(_task(cases={"must_pass": [], "must_flag": ["BAD case"]}), leaky)
    (row,) = r["rows"]
    assert row["want"] == "flag" and row["flags"] == 3


def test_plan_counts_every_call_and_main_refuses_over_budget(tmp_path):
    t = _task(max_calls=10)
    assert J.plan(t) == (2 + 2) * 4
    p = tmp_path / "t.json"
    p.write_text(json.dumps(t), encoding="utf-8")
    assert J.main(str(p), yes=True) == 2               # refused before any judge is even loaded


def test_main_asks_and_runs_nothing_on_no(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(_task()), encoding="utf-8")
    assert J.main(str(p), ask=lambda q: "n") == 1


def test_a_cold_first_call_is_absorbed_by_the_warmup_not_the_controls(tmp_path, monkeypatch):
    """A local model's FIRST call can time out while it loads into the GPU (measured 2026-10-22: it voided two
    real runs). main() makes one uncounted warm-up call first, so the controls see a warm judge."""
    state = {"n": 0}

    def cold_then_warm(text):
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("model still loading")
        return fake(text)
    monkeypatch.setattr(J, "load_judge", lambda spec: cold_then_warm)
    p = tmp_path / "t.json"
    p.write_text(json.dumps(_task()), encoding="utf-8")
    assert J.main(str(p), yes=True) == 0              # instrument ok, no rows: the cold call never reached a gate


def test_python_judge_plugin(tmp_path):
    plug = tmp_path / "myjudge.py"
    plug.write_text("def judge(text):\n    return ['S9'] if 'BAD' in text else []\n", encoding="utf-8")
    j = J.load_judge(f"python:{plug}")
    assert j("BAD") == {"S9"} and j("fine") == frozenset()
