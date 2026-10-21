"""Installer: FAKE home / AppData folders only (never the real machine). Detect only what is installed, back
up before editing, touch only our entry, leave malformed files untouched, idempotent, clean uninstall, CLI
preferred where the tool ships one (driven here by a fake CLI), and the command it writes really starts a
protocol-correct server."""
import asyncio
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from hb import install as I

ROOT = I.ROOT
MAIN = str(ROOT / "hb" / "__main__.py")


class Fake:
    def __init__(self, tmp, answers=None, which=None, run=None):
        self.home, self.appdata, self.local = tmp / "home", tmp / "home" / "AppData" / "Roaming", tmp / "home" / "AppData" / "Local"
        for d in (self.home, self.appdata, self.local):
            d.mkdir(parents=True, exist_ok=True)
        self.lines, self.answers = [], list(answers or [])
        self.env = I.Env(home=self.home, appdata=self.appdata, localappdata=self.local, platform="win32", environ={},
                         which=which or (lambda n: None), run=run, ask=self._ask, out=self.lines.append,
                         python=sys.executable)

    def _ask(self, q):
        self.lines.append(q)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def all_tools(self):
        for d in (self.home / ".cursor", self.home / ".codex", self.appdata / "Code" / "User", self.appdata / "Claude",
                  self.home / ".codeium" / "windsurf"):
            d.mkdir(parents=True, exist_ok=True)
        (self.home / ".claude.json").write_text('{"numStartups": 3, "mcpServers": {}}', encoding="utf-8")
        return self

    @property
    def text(self):
        return "\n".join(self.lines)

    def run(self, cmd, **kw):
        return I.main(cmd, env=self.env, **kw)


def backups(d):
    return sorted(p.name for p in d.rglob("*.enki-backup-*"))


def test_nothing_installed_nothing_written(tmp_path):
    f = Fake(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*"))
    assert f.run("install", yes=True) == 1 and "No supported AI coding tool found" in f.text
    assert sorted(p for p in tmp_path.rglob("*")) == before


def test_install_all_tier1_and_2_with_yes(tmp_path):
    f = Fake(tmp_path).all_tools()
    assert f.run("install", yes=True) == 0, f.text
    want_args = ["-I", MAIN, "mcp"]
    cc = json.loads((f.home / ".claude.json").read_text(encoding="utf-8"))
    assert cc["numStartups"] == 3 and cc["mcpServers"]["enki-harness"] == {"type": "stdio", "command": sys.executable,
                                                                           "args": want_args}
    vs = json.loads((f.appdata / "Code" / "User" / "mcp.json").read_text(encoding="utf-8"))
    assert vs["servers"]["enki-harness"] == {"type": "stdio", "command": sys.executable, "args": want_args}
    cu = json.loads((f.home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert cu["mcpServers"]["enki-harness"]["args"] == want_args
    cx = tomllib.loads((f.home / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert cx["mcp_servers"]["enki-harness"] == {"command": sys.executable, "args": want_args}
    cd = json.loads((f.appdata / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8"))
    assert cd["mcpServers"]["enki-harness"] == {"command": sys.executable, "args": want_args + ["--no-cwd-root"]}
    ws = json.loads((f.home / ".codeium" / "windsurf" / "mcp_config.json").read_text(encoding="utf-8"))
    assert ws["mcpServers"]["enki-harness"]["command"] == sys.executable
    assert Path(sys.executable).is_absolute()
    assert "will refuse to run filters until you re-run" in f.text          # Desktop without --allow is told


def test_reinstall_is_idempotent_and_uninstall_is_clean(tmp_path):
    f = Fake(tmp_path).all_tools()
    cur = f.home / ".cursor" / "mcp.json"
    cur.write_text('{"mcpServers": {"other": {"command": "node", "args": ["x.js"]}}, "keep": [1, 2]}', encoding="utf-8")
    orig = json.loads(cur.read_text(encoding="utf-8"))
    toml = f.home / ".codex" / "config.toml"
    toml_orig = 'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "node"\nargs = ["y.js"]\n'
    toml.write_text(toml_orig, encoding="utf-8")
    f.run("install", yes=True)
    snap = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and "enki-backup" not in p.name}
    n_backups = len(backups(tmp_path))
    f.lines.clear()
    assert f.run("install", yes=True) == 0
    assert f.text.count("already registered (unchanged)") == 6
    assert {p: p.read_bytes() for p in snap} == snap and len(backups(tmp_path)) == n_backups
    assert f.run("uninstall", yes=True) == 0
    assert json.loads(cur.read_text(encoding="utf-8")) == orig                   # only our entry went
    assert toml.read_text(encoding="utf-8") == toml_orig                         # byte-exact for our TOML block
    for p in (f.appdata / "Code" / "User" / "mcp.json", f.home / ".claude.json"):
        assert "enki-harness" not in p.read_text(encoding="utf-8")
    f.lines.clear()
    assert f.run("uninstall", yes=True) == 0 and f.text.count("not registered") == 6


def test_backup_holds_the_original_bytes(tmp_path):
    f = Fake(tmp_path)
    (f.home / ".cursor").mkdir()
    cur = f.home / ".cursor" / "mcp.json"
    raw = b'{\n    "mcpServers": {"a": {"command": "x"}}\n}\n'
    cur.write_bytes(raw)
    f.run("install", yes=True, tools="cursor")
    (b,) = (f.home / ".cursor").glob("mcp.json.enki-backup-*")
    assert b.read_bytes() == raw


@pytest.mark.parametrize("raw", ['{"mcpServers": {', "[1, 2]", '{"mcpServers": ["x"]}', '// comment\n{"mcpServers": {}}'])
def test_malformed_json_left_untouched_and_reported(tmp_path, raw):
    f = Fake(tmp_path)
    (f.home / ".cursor").mkdir()
    (f.appdata / "Claude").mkdir()
    cur = f.home / ".cursor" / "mcp.json"
    cur.write_text(raw, encoding="utf-8")
    assert f.run("install", yes=True) == 1
    assert cur.read_text(encoding="utf-8") == raw and not list((f.home / ".cursor").glob("*.enki-backup-*"))
    assert "✗ Cursor" in f.text and "left untouched" in f.text
    assert "enki-harness" in (f.appdata / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8")  # others go on


def test_undecodable_file_left_untouched(tmp_path):
    f = Fake(tmp_path)
    (f.home / ".cursor").mkdir()
    cur = f.home / ".cursor" / "mcp.json"
    cur.write_bytes(b'{"mcpServers": {"\xff\xfe": 1}}')
    assert f.run("install", yes=True) == 1 and cur.read_bytes() == b'{"mcpServers": {"\xff\xfe": 1}}'
    assert "unreadable" in f.text


@pytest.mark.parametrize("raw,why", [
    ("model = \n", "not valid TOML"),
    ('[mcp_servers.enki-harness]\ncommand = "someone-else"\n', "did not write"),
    ('mcp_servers = { other = { command = "x" } }\n', "invalid TOML"),
])
def test_codex_toml_left_untouched(tmp_path, raw, why):
    f = Fake(tmp_path)
    (f.home / ".codex").mkdir()
    toml = f.home / ".codex" / "config.toml"
    toml.write_text(raw, encoding="utf-8")
    assert f.run("install", yes=True, tools="codex") == 1
    assert toml.read_text(encoding="utf-8") == raw and why in f.text


def test_codex_home_env_and_crlf_preserved(tmp_path):
    f = Fake(tmp_path)
    ch = tmp_path / "codexhome"
    ch.mkdir()
    f.env.environ["CODEX_HOME"] = str(ch)
    (ch / "config.toml").write_bytes(b'model = "o4"\r\n')
    f.run("install", yes=True, tools="codex")
    raw = (ch / "config.toml").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert tomllib.loads(raw.decode())["mcp_servers"]["enki-harness"]["args"][-1] == "mcp"
    f.run("uninstall", yes=True, tools="codex")
    assert (ch / "config.toml").read_bytes() == b'model = "o4"\r\n'


def test_asks_per_tool_and_only_yes_changes(tmp_path):
    f = Fake(tmp_path, answers=["y", "n", "", "yes"]).all_tools()
    (f.appdata / "Claude").rmdir()
    shutil.rmtree(f.home / ".codeium")
    assert f.run("install") == 0
    assert "enki-harness" in (f.home / ".claude.json").read_text(encoding="utf-8")         # y
    assert not (f.appdata / "Code" / "User" / "mcp.json").exists()                     # n
    assert not (f.home / ".cursor" / "mcp.json").exists()                              # blank = No
    assert "enki-harness" in (f.home / ".codex" / "config.toml").read_text(encoding="utf-8")  # yes
    assert f.text.count("[y/N]") == 4


def test_eof_on_question_means_no(tmp_path):
    f = Fake(tmp_path).all_tools()
    assert f.run("install") == 0 and "skipped" in f.text
    assert "enki-harness" not in (f.home / ".claude.json").read_text(encoding="utf-8")


def test_tools_filter_and_unknown_tool(tmp_path):
    f = Fake(tmp_path).all_tools()
    assert f.run("install", yes=True, tools="cursor") == 0
    assert (f.home / ".cursor" / "mcp.json").exists() and not (f.appdata / "Code" / "User" / "mcp.json").exists()
    assert f.run("install", yes=True, tools="cursor,emacs") == 2 and "unknown tool(s): emacs" in f.text


def test_dry_run_writes_nothing(tmp_path):
    f = Fake(tmp_path).all_tools()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert f.run("install", yes=True, dry_run=True) == 0 and "would add" in f.text
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_claude_desktop_msix_and_allow_folder(tmp_path):
    f = Fake(tmp_path, answers=["y", str(tmp_path)])
    msix = f.local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"
    msix.mkdir(parents=True)
    (f.appdata / "Claude").mkdir()
    assert f.run("install", tools="claude-desktop") == 0
    for d in (f.appdata / "Claude", msix):
        e = json.loads((d / "claude_desktop_config.json").read_text(encoding="utf-8"))["mcpServers"]["enki-harness"]
        assert e["args"][-3:] == ["--allow", str(tmp_path.resolve()), "--no-cwd-root"]


def test_claude_desktop_current_store_layout(tmp_path):
    """As measured on the dev machine 2026-10-21: the app's live config is under Claude-Data\\claude_<pkg>; the two
    older folders hold only a '.user-data-left' marker, so no stray config is created there."""
    f = Fake(tmp_path)
    old = [f.appdata / "Claude", f.local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"]
    for d in old:
        d.mkdir(parents=True)
        (d / ".user-data-left.claude_pzs8sxrjxfjjc").write_text("", encoding="utf-8")
    live = f.local / "Claude-Data" / "claude_pzs8sxrjxfjjc"
    live.mkdir(parents=True)
    (live / "claude_desktop_config.json").write_text('{"preferences": {"x": 1}, "coworkUserFilesPath": "C:/w"}',
                                                     encoding="utf-8")
    assert f.run("install", yes=True, tools="claude-desktop", allow=[str(tmp_path)]) == 0
    d = json.loads((live / "claude_desktop_config.json").read_text(encoding="utf-8"))
    assert d["preferences"] == {"x": 1} and d["coworkUserFilesPath"] == "C:/w" and "enki-harness" in d["mcpServers"]
    assert not any((x / "claude_desktop_config.json").exists() for x in old)
    assert "quit Claude Desktop fully first" in f.text


def test_allow_must_be_a_folder(tmp_path):
    f = Fake(tmp_path).all_tools()
    assert f.run("install", yes=True, allow=[str(tmp_path / "nope")]) == 2


# ---- CLI preferred where the tool ships one (fake CLIs; the real ones are never run) ----------------------

class FakeCLI:
    """Behaves like `claude mcp` / `codex mcp` on the fake files, and records every call."""

    def __init__(self, f, works=True):
        self.f, self.calls, self.works = f, [], works

    def __call__(self, argv):
        self.calls.append(argv)
        ok = subprocess.CompletedProcess(argv, 0, "", "")
        if not self.works:
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        if argv[0].endswith("claude.exe"):
            p = self.f.home / ".claude.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            if argv[1:3] == ["mcp", "add"]:
                cmd = argv[argv.index("--") + 1:]
                d.setdefault("mcpServers", {})[argv[5]] = {"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": {}}
            elif argv[1:3] == ["mcp", "remove"]:
                d.get("mcpServers", {}).pop(argv[3], None)
            p.write_text(json.dumps(d), encoding="utf-8")
        else:
            p = self.f.home / ".codex" / "config.toml"
            if argv[1:3] == ["mcp", "add"]:
                cmd = argv[argv.index("--") + 1:]
                p.write_text(p.read_text(encoding="utf-8") + f"\n[mcp_servers.{argv[3]}]\ncommand = {json.dumps(cmd[0])}\n"
                             f"args = {json.dumps(cmd[1:])}\n", encoding="utf-8")
            elif argv[1:3] == ["mcp", "remove"]:
                txt = p.read_text(encoding="utf-8")
                cut = txt.find(f"\n[mcp_servers.{argv[3]}]")
                if cut >= 0:
                    p.write_text(txt[:cut], encoding="utf-8")
        return ok


def _cli_fake(tmp_path, works=True, exe_suffix=".exe"):
    f = Fake(tmp_path).all_tools()
    (f.home / ".codex" / "config.toml").write_text('model = "o4"\n', encoding="utf-8")
    cli = FakeCLI(f, works)
    f.env.which = lambda n: f"C:/fake/{n}{exe_suffix}" if n in ("claude", "codex") else None
    f.env.run = cli
    return f, cli


def test_claude_code_and_codex_use_their_cli_and_verify(tmp_path):
    f, cli = _cli_fake(tmp_path)
    assert f.run("install", yes=True, tools="claude-code,codex") == 0, f.text
    assert ["C:/fake/claude.exe", "mcp", "add", "--scope", "user", "enki-harness", "--", sys.executable, "-I", MAIN, "mcp"] in cli.calls
    assert ["C:/fake/codex.exe", "mcp", "add", "enki-harness", "--", sys.executable, "-I", MAIN, "mcp"] in cli.calls
    assert f.text.count("verified in file") == 2
    assert len(list(f.home.glob(".claude.json.enki-backup-*"))) == 1
    n = len(cli.calls)
    f.run("install", yes=True, tools="claude-code,codex")
    assert len(cli.calls) == n and f.text.count("already registered (unchanged)") == 2   # idempotent: no CLI call
    assert f.run("uninstall", yes=True, tools="claude-code,codex") == 0
    assert ["C:/fake/claude.exe", "mcp", "remove", "enki-harness", "--scope", "user"] in cli.calls
    assert ["C:/fake/codex.exe", "mcp", "remove", "enki-harness"] in cli.calls
    assert "enki-harness" not in (f.home / ".claude.json").read_text(encoding="utf-8")


def test_a_cli_that_fails_is_reported_not_trusted(tmp_path):
    f, cli = _cli_fake(tmp_path, works=False)
    assert f.run("install", yes=True, tools="claude-code,codex") == 1
    assert f.text.count("did not register it") == 2


def test_cmd_shim_with_risky_arguments_falls_back_to_file_edit(tmp_path):
    f, cli = _cli_fake(tmp_path, exe_suffix=".cmd")
    f.env.python = r"C:\Program Files\Py & Co\python.exe"
    assert f.run("install", yes=True, tools="claude-code") == 0
    assert cli.calls == []
    e = json.loads((f.home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]["enki-harness"]
    assert e["command"] == r"C:\Program Files\Py & Co\python.exe"


def test_never_real_machine_under_pytest():
    with pytest.raises(RuntimeError):
        I.main("install", yes=True, dry_run=True)


# ---- the command the installer writes really starts a protocol-correct server ----------------------------

def test_registered_command_starts_a_working_server(tmp_path):
    mcp = pytest.importorskip("mcp")
    from mcp.client.stdio import StdioServerParameters
    f = Fake(tmp_path)
    (f.home / ".cursor").mkdir()
    f.run("install", yes=True, tools="cursor")
    e = json.loads((f.home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["enki-harness"]
    proj = tmp_path / "proj"
    (proj / "filters").mkdir(parents=True)
    shutil.copy(ROOT / "examples" / "planted_filter.py", proj / "filters" / "swear.py")

    async def go():
        params = StdioServerParameters(command=e["command"], args=e["args"], cwd=str(proj),
                                       env={"HB_DATA": str(tmp_path / "hbdata")})
        async with mcp.Client(params, mode="legacy") as c:
            r = await c.call_tool("build_harness", {"system": "filters/swear.py", "must_catch": ["I hate you", "what a loser"],
                                                    "must_not_catch": ["good game everyone"]})
            return r.is_error, r.content[0].text
    err, txt = asyncio.run(go())
    assert not err and "blame: hate_word" in txt and txt.index("BUGS") < txt.index("SCORE")
