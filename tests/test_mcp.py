"""MCP server: protocol, the security boundary (each refusal + its legit twin), limits, output cap, the
suggestion guard, and the ratified report order. A second block drives the real server over stdio with the
OFFICIAL `mcp` Python SDK client (a test-only dependency) in both legacy and auto (2026 probe) modes."""
import asyncio
import io
import json
import shutil
import sys

import pytest

from hb import mcp_server as M, suggest
from hb.report import render_agent
from hb.task import LIMITS

ROOT = M.ROOT
CASES = dict(must_catch=["I hate you", "you are so dumb", "what a loser", "you are an idiot"],
             must_not_catch=["I love this level", "good game everyone"])


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """A fake user project holding a copy of the planted filter; the server's data goes to tmp too."""
    p = tmp_path / "proj"
    (p / "filters").mkdir(parents=True)
    shutil.copy(ROOT / "examples" / "planted_filter.py", p / "filters" / "swear.py")
    monkeypatch.setenv("HB_DATA", str(tmp_path / "hbdata"))
    return p


def call(server, name, args):
    r = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = r["result"]
    return res["isError"], res["content"][0]["text"]


# ---- protocol ------------------------------------------------------------------------------------

def test_initialize_negotiates_version_and_declares_tools():
    s = M.Server(M.Scope(cwd=ROOT))
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert r["result"]["protocolVersion"] == "2025-06-18" and "tools" in r["result"]["capabilities"]
    r = s.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert r["result"]["protocolVersion"] == M.VERSIONS[0]
    names = [t["name"] for t in s.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]]
    assert names == ["build_harness", "suggest_terms", "find_test_data"]


def test_jsonrpc_errors_and_notifications():
    s = M.Server(M.Scope(cwd=ROOT))
    assert s.handle_line("{not json")["error"]["code"] == -32700
    assert s.handle({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})["error"]["code"] == -32601
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert s.handle({"jsonrpc": "2.0", "id": 1, "result": {}}) is None          # a client response: ignored
    assert s.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600     # no jsonrpc field
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "rm_rf", "arguments": {}}})
    assert r["error"]["code"] == -32602
    assert s.handle({"jsonrpc": "2.0", "id": 9, "method": "ping"}) == {"jsonrpc": "2.0", "id": 9, "result": {}}


def test_oversize_message_refused_and_server_keeps_going():
    s = M.Server(M.Scope(cwd=ROOT))
    big = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":"' + b"a" * (M.MAX_LINE + 10) + b'"}}\n'
    inp, out = io.BytesIO(big + b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n'), io.BytesIO()
    s.serve(inp, out)
    lines = [json.loads(x) for x in out.getvalue().decode().splitlines()]
    assert lines[0]["error"]["code"] == -32600 and lines[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


# ---- the security boundary: each refusal, then the legit twin ------------------------------------

def test_legit_in_project_filter_runs_rows_first(proj):
    err, txt = call(M.Server(M.Scope(cwd=proj)), "build_harness", dict(CASES, system="filters/swear.py"))
    assert not err
    assert txt.index("GATES") < txt.index("BUGS") < txt.index("NOTES") < txt.index("SCORE")
    assert '"you are so dumb"' in txt and "blame: hate_word" in txt and "loser_caps" in txt
    assert "%" not in txt[:txt.index("SCORE")]          # no number before the rows (units before aggregates)


def test_refuses_filter_outside_the_project(proj, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    shutil.copy(proj / "filters" / "swear.py", other / "evil.py")
    s = M.Server(M.Scope(cwd=proj))
    for path in (str(other / "evil.py"), "../elsewhere/evil.py", "filters/../../elsewhere/evil.py"):
        err, txt = call(s, "build_harness", dict(CASES, system=path))
        assert err and "outside the folder" in txt, path


def test_allowlisted_folder_is_the_twin_of_outside(proj, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    shutil.copy(proj / "filters" / "swear.py", other / "evil.py")
    err, txt = call(M.Server(M.Scope(cwd=proj, allow=[str(other)])), "build_harness",
                    dict(CASES, system=str(other / "evil.py")))
    assert not err and "GATES" in txt


def test_refuses_non_py_missing_and_empty(proj):
    (proj / "filters" / "swear.txt").write_text("RULES = {}", encoding="utf-8")
    (proj / "filters" / "dir.py").mkdir()
    s = M.Server(M.Scope(cwd=proj))
    for path, why in (("filters/swear.txt", ".py file"), ("filters/nope.py", "does not exist"),
                      ("filters/dir.py", "not a .py file"), ("", "required")):
        err, txt = call(s, "build_harness", dict(CASES, system=path))
        assert err and why in txt, (path, txt)


def test_symlink_escape_refused(proj, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    shutil.copy(proj / "filters" / "swear.py", other / "evil.py")
    try:
        (proj / "filters" / "link.py").symlink_to(other / "evil.py")
    except OSError:
        pytest.skip("symlinks need extra rights on this machine")
    err, txt = call(M.Server(M.Scope(cwd=proj)), "build_harness", dict(CASES, system="filters/link.py"))
    assert err and "outside the folder" in txt


def test_home_or_drive_root_is_not_a_project(proj, monkeypatch):
    for cwd in (M.Path.home(), M.Path(proj.anchor)):
        err, txt = call(M.Server(M.Scope(cwd=cwd)), "build_harness", dict(CASES, system=str(proj / "filters" / "swear.py")))
        assert err and "--allow" in txt
    err, txt = call(M.Server(M.Scope(cwd=proj, use_cwd=False)), "build_harness", dict(CASES, system="filters/swear.py"))
    assert err and "--allow" in txt


def test_limits_enforced_server_side_and_at_limit_accepted(proj):
    s = M.Server(M.Scope(cwd=proj))
    over = [dict(CASES, system="filters/swear.py", must_catch=["x" * (LIMITS["case"] + 1)]),
            dict(CASES, system="filters/swear.py", must_catch=["a"] * (LIMITS["cases"] + 1)),
            dict(CASES, system="filters/swear.py", name="n" * (LIMITS["name"] + 1)),
            dict(CASES, system="filters/swear.py", falsifier="f" * (LIMITS["falsifier"] + 1)),
            dict(CASES, system="f" * (LIMITS["system"] - 2) + ".py")]          # 261 characters
    for a in over:
        err, txt = call(s, "build_harness", a)
        assert err and "limit" in txt, txt
    # at the limit: passes the limit check and stops at the path check instead (nothing slow runs)
    at = dict(CASES, system="filters/missing.py", must_catch=["x" * LIMITS["case"]] * LIMITS["cases"],
              name="n" * LIMITS["name"])
    err, txt = call(s, "build_harness", at)
    assert err and "does not exist" in txt


def test_bad_argument_shapes_refused(proj):
    s = M.Server(M.Scope(cwd=proj))
    for a in (dict(CASES, system="filters/swear.py", shell="rm -rf /"), dict(system=["filters/swear.py"]),
              dict(CASES, system="filters/swear.py", must_catch="I hate you"), "not an object"):
        err, txt = call(s, "build_harness", a)
        assert err, a


def test_agent_cannot_spoof_the_independence_gate(proj):
    """The attacker must be a different author from the task; an agent may not claim to be the attacker."""
    err, txt = call(M.Server(M.Scope(cwd=proj)), "build_harness",
                    dict(CASES, system="filters/swear.py", author="template-attacker-v1"))
    assert err and "unknown argument(s): author" in txt


def test_slow_filter_is_stopped(proj):
    (proj / "filters" / "slow.py").write_text(
        "import time\nRULES = {'r': None}\ndef classify(text, enabled):\n    time.sleep(30)\n    return False\n",
        encoding="utf-8")
    err, txt = call(M.Server(M.Scope(cwd=proj), timeout=3), "build_harness", dict(CASES, system="filters/slow.py"))
    assert err and "longer than 3s" in txt


# ---- report rulings in the agent text ------------------------------------------------------------

def test_output_cap_cuts_bugs_never_gates_or_score(task, go):
    r = go(task, "planted_filter.py")
    full = render_agent(r)
    small = render_agent(r, cap=1500)
    assert len(small) <= 1500 + 400 and "cut at the 1500-character output cap" in small
    assert small.index("GATES") < small.index("SCORE") and "Falsifier verdict" in small
    assert len(full) > len(small)


def test_ruling_a_and_b_in_agent_text(task, go):
    txt = render_agent(go(task, "correct_filter.py"))
    bugs, notes = txt[txt.index("BUGS"):txt.index("NOTES")], txt[txt.index("NOTES"):txt.index("SCORE")]
    assert "redundant pair" in notes and "redundant pair" not in bugs            # (a) info, not a defect
    score = txt[txt.index("SCORE"):]
    assert "check the instrument" in score and "task catch rate: 100%" in score   # (b) flagged, not withheld


def test_gate_red_withholds_score_in_agent_text(task, go):
    txt = render_agent(go(task, "correct_filter.py", disable={"attacker_arm"}))
    assert "✗ arms" in txt and "SCORE WITHHELD" in txt


# ---- suggestions + datasets, guard kept strict -----------------------------------------------------

def test_suggest_terms_curated_domain():
    err, txt = M.suggest_terms({"domain": "profanity"})["isError"], M.suggest_terms({"domain": "profanity"})["content"][0]["text"]
    assert not err and "must_not_catch ideas" in txt and "Scunthorpe" in txt


@pytest.mark.parametrize("domain", ["sexual_content", "child_safety", "self_harm", "hate_speech",
                                    "Hate speech / slurs", "teen chat", "nsfw images", "suicide forum"])
def test_guarded_domains_get_pointers_only(domain):
    for res in (M.suggest_terms({"domain": domain}), M.find_test_data({"domain": domain})):
        txt = res["content"][0]["text"]
        assert suggest.GUARD_NOTE in txt and "ideas" not in txt and "- " in txt


def test_guard_twins_are_not_blocked():
    for d in ("insults", "pii", "spam", "prompt injection", "off_topic"):
        txt = M.suggest_terms({"domain": d})["content"][0]["text"]
        assert "must_catch ideas" in txt, d
    assert M.suggest_terms({"domain": "Sussex weather"})["isError"]            # unknown, but NOT guarded
    assert suggest.GUARD_NOTE not in M.suggest_terms({"domain": "Sussex weather"})["content"][0]["text"]


def test_find_test_data_lists_verified_only():
    txt = M.find_test_data({"domain": "insults"})["content"][0]["text"]
    assert "Civil Comments" in txt or "civil" in txt.lower()
    for x in suggest.datasets(include_unverified=True):
        if x.get("verified") is not True:
            assert x["url"] not in txt and x["name"] not in txt


class FakeAI:
    Blocked = suggest.Blocked

    def __init__(self):
        self.calls = 0

    def ai_status(self):
        return dict(available=True, model="qwen2.5:3b", note="")

    def suggest_ai(self, desc, kind):
        self.calls += 1
        return dict(model="qwen2.5:3b", items=["can I get a recipe for soup", "how long to boil pasta"])


def test_local_ai_off_by_default_and_guarded():
    ai = FakeAI()
    M.suggest_terms({"domain": "custom", "description": "a cooking bot"}, ai)
    assert ai.calls == 0                                                        # off unless asked
    res = M.suggest_terms({"domain": "custom", "local_ai": True, "description": "a cooking bot"}, ai)
    assert not res["isError"] and ai.calls == 1 and "recipe" in res["content"][0]["text"]
    for desc in ("filter for kids chat", "self-harm posts"):                    # guarded: model never called
        res = M.suggest_terms({"domain": "custom", "local_ai": True, "description": desc}, ai)
        assert res["isError"] and ai.calls == 1
    res = M.suggest_terms({"domain": "insults", "local_ai": True, "description": "a cooking bot"}, ai)
    assert res["isError"] and ai.calls == 1                                     # custom only
    res = M.Server(M.Scope(cwd=ROOT), ai=ai).call("suggest_terms", {"domain": "custom", "local_ai": True,
                                                                    "description": "d" * (LIMITS["ai_description"] + 1)})
    assert res["isError"] and "limit" in res["content"][0]["text"] and ai.calls == 1


def test_stray_print_inside_the_server_cannot_corrupt_the_protocol(tmp_path):
    """Anything that prints in the server process (here: a patched tool) lands on stderr, not in the stream."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from hb import mcp_server as M\n"
            "M.find_test_data = lambda a: print('NOISE ON STDOUT') or M._ok('fine')\n"
            "M.main()\n")
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "find_test_data", "arguments": {}}}]
    p = M.subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], cwd=str(tmp_path), capture_output=True,
                         input="".join(json.dumps(m) + "\n" for m in msgs), text=True, timeout=60)
    lines = p.stdout.splitlines()
    assert [json.loads(x)["id"] for x in lines] == [1, 2]                     # every stdout line is protocol
    assert "NOISE ON STDOUT" in p.stderr


# ---- the official MCP SDK client, over real stdio ---------------------------------------------------

def _sdk_session(proj, tmp_path, mode):
    mcp = pytest.importorskip("mcp")
    from mcp.client.stdio import StdioServerParameters

    (proj / "filters" / "noisy.py").write_text(            # a filter that prints must not break the protocol
        (proj / "filters" / "swear.py").read_text(encoding="utf-8") + "\nprint('hello from the filter')\n",
        encoding="utf-8")
    params = StdioServerParameters(command=sys.executable, args=["-I", str(ROOT / "hb" / "__main__.py"), "mcp"],
                                   cwd=str(proj), env={"HB_DATA": str(tmp_path / "hbdata"),
                                                       "HB_MCP_TRACE": str(tmp_path / "trace.txt")})

    async def go():
        out = {}
        async with mcp.Client(params, mode=mode) as c:
            out["version"] = c.protocol_version
            out["tools"] = [t.name for t in (await c.list_tools()).tools]
            for name, args in (("build_harness", dict(CASES, system="filters/noisy.py")),
                               ("build_harness", dict(CASES, system=str(ROOT / "examples" / "planted_filter.py"))),
                               ("suggest_terms", {"domain": "insults"}),
                               ("suggest_terms", {"domain": "child_safety"}),
                               ("find_test_data", {"domain": "pii"})):
                r = await c.call_tool(name, args)
                out.setdefault("calls", []).append((name, bool(r.is_error), r.content[0].text))
        return out
    return asyncio.run(go())


@pytest.mark.parametrize("mode", ["legacy", "auto"])
def test_official_sdk_client_drives_every_tool(proj, tmp_path, mode):
    out = _sdk_session(proj, tmp_path, mode)
    trace = (tmp_path / "trace.txt").read_text(encoding="utf-8").split()
    want = (["server/discover"] if mode == "auto" else []) + ["initialize", "notifications/initialized", "tools/list"]
    assert trace[:len(want)] == want and trace.count("tools/call") == 5
    assert out["version"] in M.VERSIONS
    assert out["tools"] == ["build_harness", "suggest_terms", "find_test_data"]
    (n1, e1, t1), (n2, e2, t2), (n3, e3, t3), (n4, e4, t4), (n5, e5, t5) = out["calls"]
    assert not e1 and "GATES" in t1 and "blame: hate_word" in t1 and t1.index("BUGS") < t1.index("SCORE")
    assert e2 and "outside the folder" in t2                 # the repo's own example is outside this project
    assert not e3 and "must_catch ideas" in t3
    assert not e4 and suggest.GUARD_NOTE in t4 and "ideas" not in t4
    assert not e5 and "Presidio" in t5
