"""`python -m hb install` / `python -m hb uninstall`: register the MCP server in the AI coding tools found here.

Rules: only tools that are actually installed are offered; each is asked y/N (or --yes); every config file is
BACKED UP before an edit; only our own entry (`enki-harness`) is touched; an unreadable or malformed config is
reported and left exactly as it was; uninstall removes only our entry; re-install is idempotent. Where the tool
ships a CLI for this (Claude Code `claude mcp`, Codex `codex mcp`) it is used, then the file is re-read to verify.
The registered command is the absolute path of the Python running this installer, so no PATH change is needed.

Config locations and shapes were checked against each tool's docs on 2026-10-21 (URLs in DOCS)."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
ENTRY = "enki-harness"
DOCS = {
    "claude-code": "https://code.claude.com/docs/en/mcp",
    "vscode": "https://code.visualstudio.com/docs/agents/reference/mcp-configuration",
    "cursor": "https://cursor.com/docs/context/mcp",
    "codex": "https://learn.chatgpt.com/docs/extend/mcp?surface=cli",
    "claude-desktop": "https://modelcontextprotocol.io/docs/develop/connect-local-servers",
    "windsurf": "https://docs.devin.ai/desktop/cascade/mcp",
}
TOML_BEGIN = "# >>> enki-harness (added by `hb install`; `hb uninstall` removes this block)"
TOML_END = "# <<< enki-harness"


def _console_print(*args, **kw):
    """print() that cannot crash on a console lacking a character (a standard Windows console shows a check mark
    as '?'). Found on the real machine: the install succeeded, then printing the check mark raised a traceback."""
    try:
        print(*args, **kw)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(*(str(a).encode(enc, "replace").decode(enc) for a in args), **kw)


@dataclass
class Env:
    """Everything the installer touches on the machine, injectable so tests use FAKE folders only."""
    home: Path
    appdata: Path = None
    localappdata: Path = None
    platform: str = sys.platform
    environ: dict = field(default_factory=dict)
    which: Callable = lambda name: None
    run: Callable = None
    ask: Callable = input
    out: Callable = _console_print
    python: str = sys.executable

    @classmethod
    def real(cls):
        e = os.environ
        return cls(home=Path.home(), appdata=Path(e["APPDATA"]) if e.get("APPDATA") else None,
                   localappdata=Path(e["LOCALAPPDATA"]) if e.get("LOCALAPPDATA") else None,
                   environ=dict(e), which=shutil.which,
                   run=lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=60))


def server_command(env, allow=(), use_cwd=True):
    args = ["-I", str(ROOT / "hb" / "__main__.py"), "mcp"]
    for a in allow:
        args += ["--allow", str(Path(a).resolve())]
    if not use_cwd:
        args.append("--no-cwd-root")
    return env.python, args


# ---- file helpers: back up, touch only our entry, never overwrite what we cannot parse --------------

class Untouched(Exception):
    """The file was left exactly as it was; the message says why."""


def _read(path):
    try:
        return path.read_text(encoding="utf-8-sig") if path.exists() else None
    except (OSError, UnicodeDecodeError) as exc:
        raise Untouched(f"unreadable ({type(exc).__name__}); left untouched") from None


def _backup(path):
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    b = path.with_name(f"{path.name}.enki-backup-{stamp}")
    n = 1
    while b.exists():
        n += 1
        b = path.with_name(f"{path.name}.enki-backup-{stamp}-{n}")
    shutil.copy2(path, b)
    return b


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".enki-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path):
    raw = _read(path)
    if raw is None or not raw.strip():
        return raw, {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise Untouched(f"not valid JSON (line {getattr(exc, 'lineno', '?')}); left untouched") from None
    if not isinstance(data, dict):
        raise Untouched("top level is not a JSON object; left untouched")
    return raw, data


def json_set(path, key, entry, dry):
    raw, data = _load_json(path)
    group = data.get(key, {})
    if not isinstance(group, dict):
        raise Untouched(f"`{key}` is not an object; left untouched")
    if group.get(ENTRY) == entry:
        return "already registered (unchanged)"
    if dry:
        return "would " + ("update" if ENTRY in group else "add") + f" `{key}.{ENTRY}`"
    b = _backup(path)
    data.setdefault(key, {})[ENTRY] = entry
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    if _load_json(path)[1].get(key, {}).get(ENTRY) != entry:
        raise Untouched("write did not verify")  # pragma: no cover
    return f"registered (backup: {b.name})" if b else "registered (new file)"


def json_unset(path, key, dry):
    if not path.exists():
        return "not registered (no file)"
    raw, data = _load_json(path)
    group = data.get(key)
    if not isinstance(group, dict) or ENTRY not in group:
        return "not registered (unchanged)"
    if dry:
        return f"would remove `{key}.{ENTRY}`"
    b = _backup(path)
    del group[ENTRY]
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return f"removed (backup: {b.name})"


def _toml_parse(text):
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise Untouched(f"not valid TOML ({exc}); left untouched") from None


_BLOCK = re.compile(r"(?ms)\n?^" + re.escape(TOML_BEGIN) + r"\n.*?^" + re.escape(TOML_END) + r"[ \t]*\n?")


def toml_set(path, command, args, dry):
    """Codex without its CLI: our own clearly delimited block, verified by re-parsing before writing."""
    orig = _read(path) or ""
    crlf = "\r\n" in orig
    text = orig.replace("\r\n", "\n")
    servers = _toml_parse(text).get("mcp_servers", {})
    ours = bool(_BLOCK.search(text))
    if isinstance(servers, dict) and ENTRY in servers and not ours:
        raise Untouched(f"an `{ENTRY}` entry exists that this installer did not write; left untouched "
                        f"(remove it with `codex mcp remove {ENTRY}` first)")
    block = (f"{TOML_BEGIN}\n[mcp_servers.{ENTRY}]\ncommand = {json.dumps(command)}\n"
             f"args = [{', '.join(json.dumps(a) for a in args)}]\n{TOML_END}\n")
    base = _BLOCK.sub("", text)
    new = base + ("" if not base or base.endswith("\n") else "\n") + ("\n" if base.strip() else "") + block
    got = _toml_parse_new(new).get("mcp_servers", {}).get(ENTRY)
    if got != {"command": command, "args": list(args)}:
        raise Untouched("adding our block would change the meaning of this file; left untouched")
    if new == text:
        return "already registered (unchanged)"
    if dry:
        return f"would {'update' if ours else 'add'} our [mcp_servers.{ENTRY}] block"
    b = _backup(path)
    _write(path, new.replace("\n", "\r\n") if crlf else new)
    return f"registered (backup: {b.name})" if b else "registered (new file)"


def _toml_parse_new(text):
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise Untouched("adding our block would make this file invalid TOML; left untouched") from None


def toml_unset(path, dry):
    if not path.exists():
        return "not registered (no file)"
    orig = _read(path)
    crlf = "\r\n" in orig
    text = orig.replace("\r\n", "\n")
    _toml_parse(text)
    if not _BLOCK.search(text):
        if ENTRY in _toml_parse(text).get("mcp_servers", {}):
            raise Untouched(f"an `{ENTRY}` entry exists that this installer did not write; left untouched")
        return "not registered (unchanged)"
    if dry:
        return f"would remove our [mcp_servers.{ENTRY}] block"
    new = _BLOCK.sub("", text)
    _toml_parse(new)
    b = _backup(path)
    _write(path, new.replace("\n", "\r\n") if crlf else new)
    return f"removed (backup: {b.name})"


# ---- the tools -------------------------------------------------------------------------------------

def _cli_ok(exe, argv):
    """A Windows .cmd/.bat shim re-parses its arguments through cmd.exe: only use it for plain arguments."""
    if exe.lower().endswith((".cmd", ".bat")):
        return not any(re.search(r'[\s"&|<>^%!()]', a) for a in argv)
    return True


class Tool:
    id = label = ""
    key = "mcpServers"
    use_cwd = True
    tier = 1
    note = ""
    before = ""

    def files(self, env):
        return []

    def entry(self, command, args):
        return {"type": "stdio", "command": command, "args": list(args)}

    def install(self, env, command, args, dry):
        return [(f, json_set(f, self.key, self.entry(command, args), dry)) for f in self.files(env)]

    def uninstall(self, env, dry):
        return [(f, json_unset(f, self.key, dry)) for f in self.files(env)]


class ClaudeCode(Tool):
    id, label = "claude-code", "Claude Code"
    note = "user scope: available in every project; restart Claude Code (or run /mcp) to see it"

    def files(self, env):
        f = env.home / ".claude.json"
        return [f] if f.exists() or (env.home / ".claude").is_dir() or env.which("claude") else []

    def _cli(self, env, argv):
        exe = env.which("claude")
        return exe if exe and env.run and _cli_ok(exe, argv) else None

    def install(self, env, command, args, dry):
        f = env.home / ".claude.json"
        add = ["mcp", "add", "--scope", "user", ENTRY, "--", command, *args]
        exe = self._cli(env, add)
        if not exe:
            return super().install(env, command, args, dry)
        if dry:
            return [(f, "would run: claude " + " ".join(add))]
        if _load_json(f)[1].get("mcpServers", {}).get(ENTRY) == self.entry(command, args) | {"env": {}}:
            return [(f, "already registered (unchanged)")]
        b = _backup(f)
        env.run([exe, "mcp", "remove", ENTRY, "--scope", "user"])      # ours only; fails harmlessly if absent
        p = env.run([exe, *add])
        got = _load_json(f)[1].get("mcpServers", {}).get(ENTRY) or {}
        if p.returncode != 0 or got.get("command") != command or got.get("args") != list(args):
            raise Untouched(f"`claude mcp add` did not register it (exit {p.returncode}: "
                            f"{(p.stderr or p.stdout or '').strip()[:200]})")
        return [(f, f"registered via `claude mcp add --scope user`, verified in file"
                    + (f" (backup: {b.name})" if b else ""))]

    def uninstall(self, env, dry):
        f = env.home / ".claude.json"
        rm = ["mcp", "remove", ENTRY, "--scope", "user"]
        exe = self._cli(env, rm)
        if not exe:
            return super().uninstall(env, dry)
        if ENTRY not in _load_json(f)[1].get("mcpServers", {}):
            return [(f, "not registered (unchanged)")]
        if dry:
            return [(f, "would run: claude " + " ".join(rm))]
        b = _backup(f)
        p = env.run([exe, *rm])
        if ENTRY in _load_json(f)[1].get("mcpServers", {}):
            raise Untouched(f"`claude mcp remove` did not remove it (exit {p.returncode})")
        return [(f, f"removed via `claude mcp remove`, verified in file (backup: {b.name})")]


class VSCode(Tool):
    id, label, key = "vscode", "VS Code (GitHub Copilot agent mode)", "servers"
    note = "user profile mcp.json; reload VS Code, then pick the tools in Copilot Chat agent mode"

    def files(self, env):
        dirs = []
        for name in ("Code", "Code - Insiders"):
            if env.platform == "win32" and env.appdata:
                dirs.append(env.appdata / name / "User")
            elif env.platform == "darwin":
                dirs.append(env.home / "Library" / "Application Support" / name / "User")
            else:
                dirs.append(env.home / ".config" / name / "User")
        return [d / "mcp.json" for d in dirs if d.is_dir()]


class Cursor(Tool):
    id, label = "cursor", "Cursor"
    note = "global ~/.cursor/mcp.json; it shows under Settings > MCP"

    def files(self, env):
        d = env.home / ".cursor"
        return [d / "mcp.json"] if d.is_dir() or env.which("cursor") else []


class Codex(Tool):
    id, label = "codex", "OpenAI Codex"
    note = "~/.codex/config.toml (CODEX_HOME if set); start a new Codex session"

    def home(self, env):
        return Path(env.environ["CODEX_HOME"]) if env.environ.get("CODEX_HOME") else env.home / ".codex"

    def files(self, env):
        h = self.home(env)
        return [h / "config.toml"] if h.is_dir() or env.which("codex") else []

    def install(self, env, command, args, dry):
        f = self.home(env) / "config.toml"
        add = ["mcp", "add", ENTRY, "--", command, *args]
        exe = env.which("codex")
        if not (exe and env.run and _cli_ok(exe, add)):
            return [(f, toml_set(f, command, args, dry))]
        text = _read(f) or ""
        cur = _toml_parse(text).get("mcp_servers", {}).get(ENTRY)
        if cur and cur.get("command") == command and cur.get("args") == list(args):
            return [(f, "already registered (unchanged)")]
        if dry:
            return [(f, "would run: codex " + " ".join(add))]
        b = _backup(f)
        env.run([exe, "mcp", "remove", ENTRY])
        p = env.run([exe, *add])
        got = _toml_parse(_read(f) or "").get("mcp_servers", {}).get(ENTRY) or {}
        if p.returncode != 0 or got.get("command") != command or got.get("args") != list(args):
            raise Untouched(f"`codex mcp add` did not register it (exit {p.returncode}: "
                            f"{(p.stderr or p.stdout or '').strip()[:200]})")
        return [(f, "registered via `codex mcp add`, verified in file" + (f" (backup: {b.name})" if b else ""))]

    def uninstall(self, env, dry):
        f = self.home(env) / "config.toml"
        exe = env.which("codex")
        if not (exe and env.run):
            return [(f, toml_unset(f, dry))]
        if ENTRY not in _toml_parse(_read(f) or "").get("mcp_servers", {}):
            return [(f, "not registered (unchanged)")]
        if dry:
            return [(f, f"would run: codex mcp remove {ENTRY}")]
        b = _backup(f)
        p = env.run([exe, "mcp", "remove", ENTRY])
        if ENTRY in _toml_parse(_read(f) or "").get("mcp_servers", {}):
            raise Untouched(f"`codex mcp remove` did not remove it (exit {p.returncode})")
        return [(f, f"removed via `codex mcp remove`, verified in file (backup: {b.name})")]


class ClaudeDesktop(Tool):
    id, label = "claude-desktop", "Claude Desktop"
    use_cwd = False          # it has no project folder: only --allow folders may be tested
    note = "reopen Claude Desktop; it tests filters only inside your --allow folder(s)"
    before = "quit Claude Desktop fully first (tray icon > Quit): it can rewrite this file when it closes"

    def entry(self, command, args):
        return {"command": command, "args": list(args)}

    def files(self, env):
        dirs = []
        if env.platform == "win32":
            if env.appdata:
                dirs.append(env.appdata / "Claude")                      # documented
            if env.localappdata:
                la = env.localappdata
                dirs += sorted(p / "LocalCache" / "Roaming" / "Claude"    # Microsoft Store (MSIX), community-reported
                               for p in la.glob("Packages/Claude_*"))
                dirs += sorted(la.glob("Claude-Data/claude_*"))           # current Store build (seen 2026-10-21)
        elif env.platform == "darwin":
            dirs.append(env.home / "Library" / "Application Support" / "Claude")
        else:
            dirs.append(env.home / ".config" / "Claude")
        cfg = "claude_desktop_config.json"
        # A folder holding only the app's ".user-data-left..." marker was vacated by the app (its data moved):
        # writing a config there would create a file the app never reads.
        return [d / cfg for d in dirs if d.is_dir() and ((d / cfg).exists() or not any(d.glob(".user-data-left*")))]


class Windsurf(Tool):
    id, label, tier = "windsurf", "Windsurf / Devin Desktop", 2
    note = "refresh the MCP list in Cascade"

    def entry(self, command, args):
        return {"command": command, "args": list(args)}

    def files(self, env):
        dirs = [env.home / ".codeium" / "windsurf"]
        if env.platform == "win32" and env.appdata:
            dirs.append(env.appdata / "devin")
        else:
            dirs.append(Path(env.environ.get("XDG_CONFIG_HOME") or env.home / ".config") / "devin")
        return [d / "mcp_config.json" for d in dirs if d.is_dir()]


TOOLS = [ClaudeCode(), VSCode(), Cursor(), Codex(), ClaudeDesktop(), Windsurf()]


# ---- the command -------------------------------------------------------------------------------------

def _yes(env, q):
    try:
        return env.ask(q).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(cmd, yes=False, tools=None, dry_run=False, allow=(), env=None):
    if env is None:
        if "PYTEST_CURRENT_TEST" in os.environ:      # tests must never reach the real machine's configs
            raise RuntimeError("install.main() called without a fake Env under pytest")
        env = Env.real()
    say = env.out
    by_id = {t.id: t for t in TOOLS}
    if tools:
        want = [x.strip() for x in tools.split(",") if x.strip()]
        bad = [x for x in want if x not in by_id]
        if bad:
            say(f"unknown tool(s): {', '.join(bad)}. Known: {', '.join(by_id)}")
            return 2
        chosen = [by_id[x] for x in want]
    else:
        chosen = TOOLS
    allow = [str(Path(a).expanduser().resolve()) for a in allow]
    for a in allow:
        if not Path(a).is_dir():
            say(f"--allow {a}: not a folder")
            return 2
    found = [(t, t.files(env)) for t in chosen]
    say(f"enki-harness {cmd}{' (dry run: nothing is written)' if dry_run else ''}")
    for t, fs in found:
        say(f"  {'found    ' if fs else 'not found'} {t.label}" + (f"  ({', '.join(str(f) for f in fs)})" if fs else ""))
    if not any(fs for _, fs in found):
        say("No supported AI coding tool found here.")
        return 1
    errors = 0
    for t, fs in found:
        if not fs:
            continue
        if t.before and not dry_run:
            say(f"  ! {t.label}: {t.before}")
        if not yes and not _yes(env, f"{'Register' if cmd == 'install' else 'Remove'} {ENTRY} "
                                      f"{'in' if cmd == 'install' else 'from'} {t.label}? [y/N] "):
            say(f"  - {t.label}: skipped")
            continue
        tool_allow = list(allow)
        if cmd == "install" and not t.use_cwd and not tool_allow and not yes:
            try:
                folder = env.ask(f"  {t.label} has no project folder. Folder it may test filters in (blank = none): ").strip()
            except EOFError:
                folder = ""
            if folder and Path(folder).expanduser().is_dir():
                tool_allow = [str(Path(folder).expanduser().resolve())]
            elif folder:
                say(f"  {folder}: not a folder; registering without one")
        try:
            if cmd == "install":
                command, args = server_command(env, tool_allow, t.use_cwd)
                res = t.install(env, command, args, dry_run)
            else:
                res = t.uninstall(env, dry_run)
        except Untouched as exc:
            errors += 1
            say(f"  ✗ {t.label}: {exc}")
            continue
        except OSError as exc:
            errors += 1
            say(f"  ✗ {t.label}: {type(exc).__name__}: {exc}")
            continue
        for f, status in res:
            say(f"  ✓ {t.label}: {status}  [{f}]")
        if cmd == "install" and not dry_run:
            say(f"    {t.note}")
            if not t.use_cwd and not tool_allow:
                say(f"    note: no folder allowed yet, so {t.label} will refuse to run filters until you re-run "
                    f"`install --tools {t.id} --allow <your project folder>`")
    if cmd == "install" and not dry_run:
        say("\nThen ask your agent: test my filter in filters/swear.py with enki-harness")
    return 1 if errors else 0
