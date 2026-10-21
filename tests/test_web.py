"""Redesigned UI: structured form, units-before-aggregates order, suggestion endpoints, local-only guards."""
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from hb import cli, learning, suggest
from hb.engine import run as engine_run
from hb.report import render_html


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DATA", tmp_path)
    monkeypatch.setattr(cli, "AI_STATUS", lambda: dict(available=True, model="qwen2.5:3b", note="Runs on your machine"))
    monkeypatch.setattr(cli, "run", lambda t: engine_run(t, store=learning.Store(tmp_path / "l.db"), run_root=tmp_path))
    srv = cli.ThreadingHTTPServer(("127.0.0.1", 0), cli.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(url, data=None, js=None, headers=None):
    h = dict(headers or {})
    h.setdefault("X-HB-Token", cli.TOKEN)
    if js is not None:
        data, h["Content-Type"] = json.dumps(js).encode(), "application/json"
    req = urllib.request.Request(url, data, h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _form(**over):
    t = cli.EXAMPLE_TASK
    f = {"csrf": cli.TOKEN, "name": t["name"], "description": t["description"], "domain": "insults",
                                   "system": t["system"], "must_catch": t["must_catch"] + [""],
                                   "must_not_catch": t["must_not_catch"] + [""],
         "falsifier": t["falsifier"]["text"]}
    f.update(over)
    return urllib.parse.urlencode({k: v for k, v in f.items() if v is not None}, doseq=True).encode()


def test_page_is_branded_structured_internal_and_cdn_free(server):
    page = urllib.request.urlopen(server + "/").read().decode()
    assert "#0ea5e9" in page and "#030712" in page and "#f5b955" in page
    assert "what are you testing?" in page and 'name="must_catch"' in page and 'name="must_not_catch"' in page
    assert 'id="falsifier"' in page and 'name="system"' in page and 'placeholder="examples/planted_filter.py"' in page
    assert "<script src" not in page and "http://" not in page.replace("http://127.0.0.1", "")
    assert 'name="viewport"' in page


def test_structured_form_run_finds_the_planted_defects(server):
    code, out = _post(server + "/run", _form())
    assert code == 200
    res = out[out.index('id="results"'):]
    for want in ("Two rules cancel each other out", "Dead rule", "Flags an innocent twin", "Missed a must-catch case",
                 "Attacker evasions", "loser_caps", "quote_exception", "you are so dumb", "I whatever you"):
        assert want in res, want
    assert "FALSIFIED" in res
    assert 'value="you are so dumb"' in out          # the form keeps what was submitted


def test_units_before_aggregates_no_number_above_the_rows(server):
    _, out = _post(server + "/run", _form())
    res = out[out.index('id="results"'):]
    score_at = res.index(">Score<")
    assert "%" not in res[:score_at], "a rate appears above the score section"
    assert "task catch rate:" in res[score_at:] and "Falsifier verdict" in res[score_at:]
    assert res.index("you are so dumb") < score_at and res.index("Every row") < score_at
    assert res.index("Bugs found") < res.index("Every row")


def test_render_html_keeps_instrument_flag_and_withheld_score(task, go):
    green = render_html(go(task, "correct_filter.py"))
    assert "check the instrument" in green and green.index("Every row") < green.index("check the instrument")
    red = render_html(go(task, "correct_filter.py", disable={"attacker_arm"}))
    assert "SCORE WITHHELD: gate(s) not green: arms" in red and "chip bad" in red


def test_wrong_rows_are_highlighted(task, go):
    h = render_html(go(task, "planted_filter.py"))
    assert re.search(r"<tr class='wrong'><td>r\d+</td><td>task</td><td>must_catch</td><td>you are so dumb", h)


def test_guard_endpoint_and_its_twin(server):
    assert json.loads(_post(server + "/guard", js={"text": "filter self-harm posts"})[1])["blocked"] == "self_harm"
    assert json.loads(_post(server + "/guard", js={"text": "classic games chat"})[1])["blocked"] is None


def test_ai_endpoint_refuses_guarded_domains_and_descriptions(server, monkeypatch):
    calls = []
    monkeypatch.setattr(suggest, "_post", lambda *a, **k: calls.append(a) or {"response": "{}"})
    for body in ({"domain": "sexual_content", "description": "x", "kind": "must_catch"},
                 {"domain": "custom", "description": "detect grooming of children", "kind": "must_catch"}):
        code, out = _post(server + "/suggest_ai", js=body)
        assert code == 403 and json.loads(out)["blocked"] is True
    assert calls == []


def test_ai_endpoint_custom_domain_returns_capped_items(server, monkeypatch):
    monkeypatch.setattr(suggest, "_get", lambda url, t: {"models": [{"name": "qwen2.5:3b"}]})
    monkeypatch.setattr(suggest, "_post", lambda url, body, t: {"response": json.dumps({"items": [f"s{i}" for i in range(20)]})})
    code, out = _post(server + "/suggest_ai", js={"domain": "custom", "description": "a cooking bot", "kind": "must_not_catch"})
    assert code == 200 and len(json.loads(out)["items"]) == 10


def _raw(server, method, path, body=b"", headers=None):
    """Send a request with exact headers (urllib would add its own Host)."""
    import http.client
    host, port = server.replace("http://", "").split(":")
    c = http.client.HTTPConnection(host, int(port))
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    out = r.status, r.read().decode()
    c.close()
    return out


def _host(server):
    return server.replace("http://", "")


# ---- security: each forged request is refused; the twin (the page's own POST) works ----------

def test_twin_the_real_pages_own_post_still_works(server):
    page = urllib.request.urlopen(server + "/").read().decode()
    token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    assert token == cli.TOKEN and page.count('name="csrf"') == 3          # run form, text form, export form
    code, out = _raw(server, "POST", "/run", _form(csrf=token),
                     {"Host": _host(server), "Origin": server, "Content-Type": "application/x-www-form-urlencoded"})
    assert code == 200 and "Two rules cancel each other out" in out


def test_forged_origin_is_refused(server):
    code, out = _raw(server, "POST", "/run", _form(), {"Host": _host(server), "Origin": "https://evil.example"})
    assert code == 403 and "cross-origin" in out
    code, _ = _raw(server, "POST", "/run", _form(), {"Host": _host(server), "Origin": "http://127.0.0.1:1"})
    assert code == 403                                                   # same host, other port = other origin


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1:1", "localhost", ""])
def test_wrong_host_is_refused(server, host):
    port = _host(server).split(":")[1]
    hdr = {"Host": host.format(port=port)} if host else {}
    assert _raw(server, "GET", "/", b"", hdr)[0] == 403
    assert _raw(server, "POST", "/run", _form(), hdr)[0] == 403


def test_localhost_name_with_port_is_allowed(server):
    port = _host(server).split(":")[1]
    assert _raw(server, "GET", "/", b"", {"Host": f"localhost:{port}"})[0] == 200


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
def test_missing_or_wrong_token_is_refused(server, token):
    code, out = _raw(server, "POST", "/run", _form(csrf=token), {"Host": _host(server)})
    assert code == 403 and "token" in out
    hdr = {"Host": _host(server), "Content-Type": "application/json"}
    if token is not None:
        hdr["X-HB-Token"] = token
    assert _raw(server, "POST", "/guard", b'{"text":"x"}', hdr)[0] == 403
    assert _raw(server, "POST", "/export", b"csrf=" + (token or "").encode(), {"Host": _host(server)})[0] == 403


def test_json_endpoints_need_json(server):
    assert _post(server + "/guard", b"text=x", headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 415


def test_oversize_body_is_refused(server):
    big = _form(description="x" * (cli.MAX_BODY + 10))
    code, out = _raw(server, "POST", "/run", big, {"Host": _host(server)})
    assert code == 413 and str(cli.MAX_BODY) in out


# ---- limits: rejected with the field and the limit named, never truncated ----------------------

@pytest.mark.parametrize("field,value,label,limit", [
    ("name", "n" * 61, "Task name", 60),
    ("description", "d" * 281, "What does it do?", 280),
    ("system", "s" * 261, "System under test", 260),
    ("falsifier", "f" * 301, "Falsifier", 300),
    ("must_catch", ["c" * 501], "Must catch case 1", 500),
    ("must_not_catch", [f"ok {i}" for i in range(51)], "Must NOT catch has 51 cases", 50),
])
def test_over_limit_field_is_rejected_naming_field_and_limit(server, field, value, label, limit):
    code, out = _post(server + "/run", _form(**{field: value}))
    assert code == 400 and "nothing was run" in out
    res = out[out.index('id="results"'):]
    assert label in res and f"the limit is {limit}" in res
    assert "Bugs found" not in res                                        # nothing ran


def test_at_limit_field_is_accepted_twin(server):
    code, out = _post(server + "/run", _form(name="n" * 60, must_catch=cli.EXAMPLE_TASK["must_catch"] + ["c" * 500]))
    assert code == 200 and "Bugs found" in out


def test_page_enforces_the_same_limits_in_html(server):
    page = urllib.request.urlopen(server + "/").read().decode()
    for fid, n in (("name", 60), ("description", 280), ("system", 260), ("falsifier", 300)):
        assert re.search(rf'id="{fid}" name="{fid}" maxlength="{n}"', page), fid
    assert 'name="must_catch" maxlength="500"' in page and '"cases": 50' in page and '"ai_description": 200' in page


def test_ai_description_over_200_is_rejected(server, monkeypatch):
    calls = []
    monkeypatch.setattr(suggest, "_post", lambda *a, **k: calls.append(a))
    code, out = _post(server + "/suggest_ai", js={"domain": "custom", "description": "a" * 201, "kind": "must_catch"})
    assert code == 400 and "limit is 200" in out and calls == []


@pytest.mark.parametrize("system", ["examples/does_not_exist.py", "examples/insult_task.json", ""])
def test_system_path_must_be_an_existing_py_file(server, system):
    code, out = _post(server + "/run", _form(system=system))
    assert code == 400 and "existing .py file" in out
