"""The base frame. Given a Task and a System, build every arm, run it, and gate the score.

Arms: task cases, twins, controls (must FAIL), rule-off singles + pairs, independent attacker,
pre-registered falsifier. No score is returned until every gate is green."""
import hashlib
import itertools
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import adapter
from .checks import disabled
from .generators import TemplateAttacker, TemplateTwins
from .learning import DATA, Store


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def write_prereg(task, run_dir):
    """Record the falsifier BEFORE any result exists. Returns the record (with its hash)."""
    body = {"task": task.name, "system": task.system, "falsifier": task.falsifier,
            "must_catch": task.must_catch, "must_not_catch": task.must_not_catch, "created_at": _now(), "seq": time.perf_counter_ns()}
    raw = json.dumps(body, sort_keys=True)
    rec = dict(body, sha256=hashlib.sha256(raw.encode()).hexdigest())
    (Path(run_dir) / "prereg.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def check_prereg(run_dir, results_at, results_seq=None):
    p = Path(run_dir) / "prereg.json"
    if not p.exists():
        return False, "no prereg.json: falsifier was not recorded before the run"
    rec = json.loads(p.read_text(encoding="utf-8"))
    body = {k: v for k, v in rec.items() if k != "sha256"}
    if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != rec.get("sha256"):
        return False, "prereg.json hash mismatch: edited after it was recorded"
    # wall clock can tie on Windows (coarse resolution), so order is proven by a monotonic counter
    if rec["created_at"] > results_at or (results_seq is not None and not rec["seq"] < results_seq):
        return False, "prereg.json is not older than the results"
    return True, f"recorded {rec['created_at']} sha256 {rec['sha256'][:12]}"


def heldout_split(cases):
    """Deterministic held-out slice of must-catch cases (~20%, at least 1 when there are 2+)."""
    if len(cases) < 2:
        return set()
    ranked = sorted(cases, key=lambda c: hashlib.sha1(c.encode()).hexdigest())
    return set(ranked[: max(1, len(cases) // 5)])


def run(task, system=None, store=None, run_root=None, disable=frozenset(), attacker=None, twins=None):
    off = lambda name: disabled(name, disable)  # noqa: E731
    system = system or adapter.load_system(task.system)
    store = store or Store()
    attacker = attacker or TemplateAttacker()
    twins = twins or TemplateTwins()
    run_dir = Path(run_root or DATA / "runs") / time.strftime("%Y%m%d-%H%M%S") / f"{time.time_ns()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    prereg = write_prereg(task, run_dir)  # 6. falsifier first, before any system call

    # ---- build rows -------------------------------------------------------------------------
    rows = []

    def add(arm, strategy, text, expected, author, parent=None, heldout=False):
        rows.append(dict(id=f"r{len(rows) + 1}", arm=arm, strategy=strategy, text=text, expected=expected,
                         author=author, parent=parent, heldout=heldout))
        return rows[-1]["id"]

    held = heldout_split(task.must_catch) if not off("heldout_guard") else set()
    parents = {}
    for t in task.must_catch:
        parents[t] = add("task", "must_catch", t, True, task.author, heldout=t in held)
    for t in task.must_not_catch:
        add("task", "must_not_catch", t, False, task.author)
    twin_missing = []
    if not off("twin_arm"):
        for t in task.must_catch:
            made = twins.generate(t, system)
            if not made:
                twin_missing.append(t)
            for strat, tw in made:
                add("twin", strat, tw, False, twins.author, parent=parents[t], heldout=t in held)
    weights = store.weights("attacker")
    chosen = attacker.choose(weights)
    if not off("attacker_arm"):
        for t in task.must_catch:
            for strat, at in attacker.generate(t, chosen):
                add("attacker", strat, at, True, attacker.author, parent=parents[t], heldout=t in held)

    # ---- execute -----------------------------------------------------------------------------
    names = system.rule_names()
    full = {r["id"]: system.run(r["text"]) for r in rows}
    for r in rows:
        r["got"] = full[r["id"]]
        r["ok"] = r["got"] == r["expected"]
    single = {n: {r["id"]: system.run(r["text"], [m for m in names if m != n]) for r in rows} for n in names}
    pairs = {(a, b): {r["id"]: system.run(r["text"], [m for m in names if m not in (a, b)]) for r in rows}
             for a, b in itertools.combinations(names, 2)}
    controls = {}
    if not off("control_arm"):
        for ctl in (adapter.always_allow(names), adapter.always_block(names)):
            out = {r["id"]: ctl.run(r["text"]) for r in rows}
            wrong = sum(out[r["id"]] != r["expected"] for r in rows)
            controls[ctl.name] = dict(wrong=wrong, same_as_system=out == full)
    results_at, results_seq = _now(), time.perf_counter_ns()

    # ---- defects -------------------------------------------------------------------------------
    defects = []

    def responsible(rid, expected):
        return [n for n in names if single[n][rid] == expected]

    kinds = [("task_miss", "task", True), ("task_false_flag", "task", False),
             ("twin_false_flag", "twin", False), ("attacker_miss", "attacker", True)]
    for kind, arm, exp in kinds:
        if off(kind):
            continue
        for r in rows:
            if r["arm"] == arm and r["expected"] == exp and not r["ok"]:
                defects.append(dict(kind=kind, row=r["id"], text=r["text"], strategy=r["strategy"],
                                    responsible=responsible(r["id"], exp)))

    interactions, redundant = [], []
    for (a, b), out in pairs.items():
        hit = [r for r in rows if out[r["id"]] != single[a][r["id"]] and out[r["id"]] != single[b][r["id"]]]
        if not hit:
            continue
        bad = [r for r in hit if not r["ok"]]
        entry = dict(rules=[a, b], rows=[r["id"] for r in (bad or hit)])
        (interactions if bad else redundant).append(entry)
        if bad and not off("interaction"):
            defects.append(dict(kind="interaction", rules=[a, b], rows=entry["rows"],
                                text=f"{a} x {b}: wrong with both on, right with either one removed"))
    dead = []
    if not off("dead_rule"):
        in_pair = {n for e in interactions + redundant for n in e["rules"]}
        for n in names:
            if all(single[n][rid] == full[rid] for rid in full) and n not in in_pair:
                dead.append(n)
                defects.append(dict(kind="dead_rule", rules=[n], text=f"removing {n} changes no row: untested or dead"))

    # ---- learning (held-out rows are never recorded) -----------------------------------------
    for arm, strats in (("attacker", chosen), ("twin", twins.strategies)):
        for s in strats:
            rs = [r for r in rows if r["arm"] == arm and r["strategy"] == s and not r["heldout"]]
            if rs:
                store.record(arm, s, len(rs), sum(not r["ok"] for r in rs))
    learning = heldout_eval(task, system, attacker, weights, held)

    # ---- gates -----------------------------------------------------------------------------------
    gates = {}
    ok, why = check_prereg(run_dir, results_at, results_seq)
    gates["prereg"] = (True, "check disabled") if off("gate_prereg") else (ok, why)
    missing = []
    if not any(r["arm"] == "task" and r["expected"] for r in rows):
        missing.append("no must-catch cases")
    if not any(r["arm"] == "task" and not r["expected"] for r in rows):
        missing.append("no must-not-catch cases")
    if twin_missing or not any(r["arm"] == "twin" for r in rows):
        missing.append(f"twins missing for {len(twin_missing) or len(task.must_catch)} must-catch case(s)")
    if not any(r["arm"] == "attacker" for r in rows):
        missing.append("attacker slice did not run")
    if not controls:
        missing.append("controls did not run")
    if len(single) != len(names) or (len(names) >= 2 and not pairs):
        missing.append("rule-off arms incomplete")
    gates["arms"] = (True, "check disabled") if off("gate_arms") else (not missing, "; ".join(missing) or "all arms ran")
    bad_ctl = [f"{k} did not fail" if v["wrong"] == 0 else f"system indistinguishable from {k}"
               for k, v in controls.items() if v["wrong"] == 0 or v["same_as_system"]]
    gates["control"] = ((True, "check disabled") if off("gate_control") else
                        (bool(controls) and not bad_ctl,
                         "; ".join(bad_ctl) or ("both controls failed as required" if controls else "no controls")))
    indep = attacker.author not in (task.author, twins.author)
    gates["independence"] = ((True, "check disabled") if off("gate_independence") else
                             (indep, f"attacker={attacker.author} task={task.author} twins={twins.author}"))
    all_green = all(v[0] for v in gates.values())

    # ---- score (only when every gate is green) -----------------------------------------------
    def rate(sel, val):
        rs = [r for r in rows if sel(r)]
        return (sum(r["got"] == val for r in rs) / len(rs), len(rs)) if rs else (None, 0)

    rates = {
        "task catch rate": rate(lambda r: r["arm"] == "task" and r["expected"], True),
        "attacker catch rate": rate(lambda r: r["arm"] == "attacker", True),
        "task false-flag rate": rate(lambda r: r["arm"] == "task" and not r["expected"], True),
        "twin false-flag rate": rate(lambda r: r["arm"] == "twin", True),
    }
    instrument = [] if off("instrument_flag") else [
        f"{k} = {v[0]:.0%}: check the instrument" for k, v in rates.items() if v[0] in (0.0, 1.0)]
    score = rates if (all_green or off("score_withhold")) else None

    missed = sum(d["kind"] in ("task_miss", "attacker_miss") for d in defects)
    false_flags = sum(d["kind"] in ("task_false_flag", "twin_false_flag") for d in defects)
    f = task.falsifier
    falsified = None if off("falsifier") else (missed > f.get("max_missed", 0) or false_flags > f.get("max_false_flags", 0))

    report = dict(task=task.name, system=system.name, rule_names=names, prereg=prereg, results_at=results_at,
                  rows=rows, defects=defects, dead_rules=dead, interactions=interactions, redundant_pairs=redundant,
                  controls=controls, gates={k: dict(ok=v[0], why=v[1]) for k, v in gates.items()},
                  all_gates_green=all_green, score=score, instrument_flags=instrument,
                  falsified=falsified, missed=missed, false_flags=false_flags,
                  attacker_strategies=chosen, learning=learning,
                  coverage=f"{len(rows)} rows generated / {len(full)} executed on the full system; "
                           f"{len(rows) * (len(single) + len(pairs))} rule-off executions",
                  run_dir=str(run_dir))
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def heldout_eval(task, system, attacker, weights, held):
    """Does learning help? Compare defects found on the HELD-OUT slice by the default strategy
    choice vs the learned one (same budget). The learner never saw these rows."""
    if not held:
        return dict(heldout_cases=0, helped=False, note="no held-out slice (need 2+ must-catch cases)")
    found = {}
    for label, strats in (("default", attacker.choose({})), ("learned", attacker.choose(weights))):
        found[label] = sum(not system.run(t) for c in held for _, t in attacker.generate(c, strats))
    helped = found["learned"] > found["default"]
    return dict(heldout_cases=len(held), default_found=found["default"], learned_found=found["learned"],
                helped=helped, note="learning helped on held-out" if helped else "no evidence learning helped (held-out)")
