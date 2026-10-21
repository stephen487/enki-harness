"""Each gate must go red in the situation it exists for, and the score must then be withheld."""
import json
from pathlib import Path

from hb import adapter, engine
from hb.generators import TemplateAttacker


def test_score_withheld_when_system_is_a_control(task, go):
    task.system = "x"
    r = go(task, system=adapter.always_allow(["a", "b"]))
    assert not r["gates"]["control"]["ok"]
    assert "indistinguishable from CONTROL always-allow" in r["gates"]["control"]["why"]
    assert r["score"] is None and not r["all_gates_green"]


def test_control_gate_red_when_harness_has_no_negative_rows(task, go):
    task.must_not_catch = []
    r = go(task, "correct_filter.py", disable={"twin_arm"})
    assert not r["gates"]["control"]["ok"]       # always-block cannot fail
    assert "always-block did not fail" in r["gates"]["control"]["why"]
    assert not r["gates"]["arms"]["ok"]
    assert r["score"] is None


def test_prereg_written_before_results_with_hash(task, go):
    r = go(task, "correct_filter.py")
    rec = json.loads((Path(r["run_dir"]) / "prereg.json").read_text())
    assert rec["created_at"] <= r["results_at"] and len(rec["sha256"]) == 64
    assert rec["falsifier"]["text"].startswith("The filter is wrong")


def test_prereg_gate_red_when_falsifier_not_recorded(task, go, monkeypatch):
    monkeypatch.setattr(engine, "write_prereg", lambda t, d: {})
    r = go(task, "correct_filter.py")
    assert not r["gates"]["prereg"]["ok"] and r["score"] is None


def test_prereg_tamper_detected(tmp_path, task):
    engine.write_prereg(task, tmp_path)
    p = tmp_path / "prereg.json"
    rec = json.loads(p.read_text())
    rec["falsifier"]["max_missed"] = 99
    p.write_text(json.dumps(rec))
    ok, why = engine.check_prereg(tmp_path, "9999")
    assert not ok and "hash mismatch" in why


def test_independence_gate_red_when_attacker_is_task_author(task, go):
    class Same(TemplateAttacker):
        author = "user"
    r = go(task, "correct_filter.py", attacker=Same())
    assert not r["gates"]["independence"]["ok"] and r["score"] is None


def test_arms_gate_red_when_attacker_missing(task, go):
    r = go(task, "correct_filter.py", disable={"attacker_arm"})
    assert not r["gates"]["arms"]["ok"] and "attacker" in r["gates"]["arms"]["why"]
    assert r["score"] is None


def test_arms_gate_red_when_twins_missing(task, go):
    r = go(task, "correct_filter.py", disable={"twin_arm"})
    assert not r["gates"]["arms"]["ok"] and r["score"] is None


def test_arms_gate_red_when_controls_missing(task, go):
    r = go(task, "correct_filter.py", disable={"control_arm"})
    assert not r["gates"]["control"]["ok"] and not r["gates"]["arms"]["ok"]


def test_instrument_flag_on_100_and_0_percent(task, go):
    r = go(task, "correct_filter.py")
    assert "task catch rate = 100%: check the instrument" in r["instrument_flags"]
    assert "twin false-flag rate = 0%: check the instrument" in r["instrument_flags"]


def test_rows_listed_with_expected_and_got(task, go):
    r = go(task, "planted_filter.py")
    arms = {row["arm"] for row in r["rows"]}
    assert arms == {"task", "twin", "attacker"}
    assert all({"text", "expected", "got", "author"} <= set(row) for row in r["rows"])


def test_prereg_gate_red_when_recorded_after_results(tmp_path, task):
    import time
    seq = time.perf_counter_ns()
    engine.write_prereg(task, tmp_path)
    ok, why = engine.check_prereg(tmp_path, "9999", seq)
    assert not ok and "not older" in why
