"""CLI and UI call the same engine; the UI binds to localhost only and shows rows before the score."""
import threading
import urllib.parse
import urllib.request

from hb import cli, learning
from hb.report import render_text


def test_text_report_rows_first_then_score(task, go):
    txt = render_text(go(task, "planted_filter.py"))
    assert txt.index("1. ROWS") < txt.index("2. DEFECTS") < txt.index("4. GATES") < txt.index("5. SCORE")
    assert "FALSIFIED" in txt


def test_text_report_says_which_gate_is_missing(task, go):
    txt = render_text(go(task, "correct_filter.py", disable={"attacker_arm"}))
    assert "SCORE WITHHELD: gate(s) not green: arms" in txt


def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(learning, "DATA", tmp_path)
    monkeypatch.setattr(cli, "run", lambda t: __import__("hb.engine", fromlist=["run"]).run(
        t, store=learning.Store(tmp_path / "l.db"), run_root=tmp_path))
    assert cli.main(["run", str(cli.ROOT / "examples/insult_task.json")]) == 0
    assert "loser_caps" in capsys.readouterr().out


def test_ui_serves_and_runs_on_localhost(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DATA", tmp_path)
    monkeypatch.setattr(cli, "run", lambda t: __import__("hb.engine", fromlist=["run"]).run(
        t, store=learning.Store(tmp_path / "l.db"), run_root=tmp_path))
    srv = cli.ThreadingHTTPServer(("127.0.0.1", 0), cli.Handler)
    assert srv.server_address[0] == "127.0.0.1"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Shared learning: <b>OFF</b>" in page
        data = urllib.parse.urlencode({"task": cli.EXAMPLE_TEXT, "csrf": cli.TOKEN}).encode()  # page token (added with CSRF guard)
        out = urllib.request.urlopen(base + "/run", data).read().decode()
        # redesigned page: statuses, then concrete bugs, then every row, then the score (units before aggregates)
        assert out.index("Bugs found") < out.index("Every row") < out.index(">Score<")
        assert "loser_caps" in out and "quote_exception x game_exception" in out
    finally:
        srv.shutdown()
