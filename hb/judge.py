"""Model-judge mode: test a STOCHASTIC text judge (an LLM safety screen, a moderation API, a classifier) instead
of a rule-based filter. A model judge can say SAFE once and FLAGGED the next time for the same text, so one run
proves nothing: every case runs `reps` times and the report gives a flag RATE.

Same discipline as the rule harness:
  * controls first: a must-pass control must never flag and a must-flag control must always flag, or the
    instrument is broken and NO score is given;
  * the falsifier is recorded before the run;
  * rows first (every case that misbehaved, with its codes and rate), score last;
  * an ERROR (timeout, bad reply) is never counted as a pass;
  * the number of judge calls is stated up front and must be approved (paid judges cost money).

Judges: "ollama:<model>" (local, free), "cf:<model>" (Cloudflare Workers AI; CF_ACCOUNT_ID + CF_WORKERS_AI_TOKEN
from the environment only, never stored), or "python:<file.py>" exposing judge(text) -> iterable of codes
(empty = pass). Standard library only."""
import importlib.util
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

TIMEOUT = 60


def parse_guard(reply: str) -> frozenset:
    """Llama-Guard-style reply -> hazard codes. 'safe' -> empty; 'unsafe\\nS1,S10' -> {S1, S10}; an 'unsafe' with
    no readable code -> {S0}. Anything else RAISES (an unreadable reply is an error, never a pass)."""
    up = (reply or "").upper()
    if "UNSAFE" in up:
        return frozenset("S" + n for n in re.findall(r"\bS(\d{1,2})\b", up)) or frozenset({"S0"})
    if re.findall(r"\w+", up) == ["SAFE"]:
        return frozenset()
    raise ValueError(f"unreadable judge reply ({len(up)} chars)")


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def load_judge(spec: str):
    """spec -> callable(text) -> frozenset of codes. Raises on any judge error."""
    kind, _, arg = spec.partition(":")
    if kind == "ollama":
        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")

        def j(text):
            d = _post(f"{host}/api/chat", {"model": arg, "stream": False,
                                           "messages": [{"role": "user", "content": text}]},
                      {"Content-Type": "application/json"})
            return parse_guard((d.get("message") or {}).get("content", ""))
        return j
    if kind == "cf":
        acct, tok = os.environ.get("CF_ACCOUNT_ID", ""), os.environ.get("CF_WORKERS_AI_TOKEN", "")
        if not (acct and tok):
            raise SystemExit("cf judge needs CF_ACCOUNT_ID and CF_WORKERS_AI_TOKEN in the environment")

        def j(text):
            d = _post(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{arg}",
                      {"messages": [{"role": "user", "content": text}]},
                      {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
            if d.get("success") is False:
                raise RuntimeError(f"cf error: {d.get('errors')}")
            return parse_guard(str((d.get("result") or {}).get("response") or ""))
        return j
    if kind == "python":
        spec_ = importlib.util.spec_from_file_location("hb_user_judge", arg)
        mod = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(mod)
        return lambda text: frozenset(mod.judge(text))
    raise SystemExit(f"unknown judge kind {kind!r} (use ollama:, cf: or python:)")


def plan(task: dict) -> int:
    """How many judge calls this task will make (stated, and approved, before any is made)."""
    n = sum(len(task.get("controls", {}).get(k, [])) for k in ("must_pass", "must_flag"))
    n += sum(len(task.get("cases", {}).get(k, [])) for k in ("must_pass", "must_flag"))
    return n * int(task.get("reps", 3))


_PROGRESS = {"done": 0, "total": 0}                 # progress goes to stderr so a long paid run never looks hung


def _tick():
    _PROGRESS["done"] += 1
    d, t = _PROGRESS["done"], _PROGRESS["total"]
    if t and (d % 25 == 0 or d == t):
        print(f"  ... {d}/{t} judge calls", file=sys.stderr, flush=True)


def _measure(judge, text, reps):
    flags, errors, codes = 0, 0, set()
    for _ in range(reps):
        try:
            c = judge(text)
        except Exception as e:                       # an error is never a pass
            errors += 1
            codes.add(f"ERR:{type(e).__name__}")
            _tick()
            continue
        _tick()
        if c:
            flags += 1
            codes |= set(c)
    return {"flags": flags, "errors": errors, "reps": reps, "codes": sorted(codes)}


def run_judge(task: dict, judge) -> dict:
    reps = int(task.get("reps", 3))
    _PROGRESS.update(done=0, total=plan(task))
    out = {"name": task.get("name", ""), "judge": task.get("judge", ""), "reps": reps,
           "falsifier": task.get("falsifier", ""), "calls": plan(task), "gates": [], "rows": []}
    ctl = task.get("controls", {})
    ok = True
    for want, texts in (("pass", ctl.get("must_pass", [])), ("flag", ctl.get("must_flag", []))):
        for t in texts:
            m = _measure(judge, t, reps)
            good = (m["errors"] == 0 and (m["flags"] == 0 if want == "pass" else m["flags"] == reps))
            ok &= good
            out["gates"].append({"control": want, "text": t[:80], "ok": good, **m})
    if not out["gates"]:
        ok = False
        out["gates"].append({"control": "none", "text": "no controls given", "ok": False})
    out["instrument_ok"] = ok
    if not ok:                                       # broken instrument: no rows, no score
        return out
    cases = task.get("cases", {})
    bad = total = 0
    for want, texts in (("pass", cases.get("must_pass", [])), ("flag", cases.get("must_flag", []))):
        for t in texts:
            m = _measure(judge, t, reps)
            total += 1
            wrong = m["errors"] > 0 or (m["flags"] > 0 if want == "pass" else m["flags"] < reps)
            if wrong:
                bad += 1
                out["rows"].append({"want": want, "text": t, **m})
    out["score"] = {"cases": total, "clean": total - bad, "clean_rate": round((total - bad) / total, 3) if total else None}
    return out


def render(r: dict) -> str:
    lines = [f"JUDGE REPORT: {r['name']}   judge={r['judge']}   reps={r['reps']}   calls={r['calls']}",
             f"falsifier (recorded before the run): {r['falsifier'] or '(none given)'}", "", "GATES (controls)"]
    for g in r["gates"]:
        lines.append(f"  {'OK' if g['ok'] else 'XX'} must-{g['control']:5} {g.get('flags', 0)}/{g.get('reps', 0)} flagged"
                     f"  errors={g.get('errors', 0)}  {g['text']}")
    if not r["instrument_ok"]:
        lines += ["", "INSTRUMENT BROKEN: a control misbehaved, so no case result means anything. No score."]
        return "\n".join(lines)
    lines += ["", "ROWS  # every case that misbehaved (want / flagged of reps / errors / codes)"]
    for row in r["rows"] or []:
        lines.append(f"  XX want {row['want']:4} {row['flags']}/{row['reps']} flagged  errors={row['errors']}  "
                     f"{','.join(row['codes']) or '-'}  {row['text'][:110]}")
    if not r["rows"]:
        lines.append("  (none)")
    s = r["score"]
    lines += ["", f"SCORE  # last: {s['clean']}/{s['cases']} cases clean in every rep ({s['clean_rate']})"]
    return "\n".join(lines)


def main(task_path: str, yes: bool = False, as_json: bool = False, ask=input) -> int:
    task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    n = plan(task)
    cap = int(task.get("max_calls", 500))
    if n > cap:
        print(f"refused: this task needs {n} judge calls, over its max_calls={cap}")
        return 2
    if not yes and ask(f"{n} calls to {task.get('judge')}. Run? [y/N] ").strip().lower() not in ("y", "yes"):
        print("not run")
        return 1
    j = load_judge(task["judge"])
    try:                                             # warm-up, NOT counted: a local model's first call can time out
        j("warm-up")                                 # while it loads (it voided two real runs, 2026-10-22)
    except Exception as e:
        print(f"(warm-up call failed: {type(e).__name__}; continuing - the controls will show if the judge is down)")
    r = run_judge(task, j)
    print(json.dumps(r, indent=2) if as_json else render(r))
    return 0 if r["instrument_ok"] and not r["rows"] else 2
