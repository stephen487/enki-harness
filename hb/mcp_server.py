"""MCP server for AI coding tools (Claude Code, Cursor, VS Code Copilot, Codex, Claude Desktop, ...).

  python -I <repo>/hb/__main__.py mcp [--allow DIR ...] [--no-cwd-root]      (what `hb install` registers)

Standard library only: JSON-RPC 2.0, one message per line on stdin/stdout (the MCP stdio transport), the
`initialize` handshake (protocol 2024-11-05 .. 2025-11-25). The 2026 `server/discover` probe is answered
"method not found", which tells probing clients to fall back to the handshake.

The caller may be an AI agent reading untrusted text (prompt injection), so every input is checked here,
server-side: the filter must be an existing .py file INSIDE the project folder the tool started us in (or a
folder allowlisted at install time), the same field limits as the web page apply, and the output is capped.
Each harness run happens in a child process with a timeout, so a slow or noisy filter cannot hang the
server or write into the protocol stream."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import suggest
from .report import render_agent
from .task import LIMITS, Task, parse_plain

ROOT = Path(__file__).resolve().parent.parent
NAME = "enki-harness"
VERSION = "0.1.0"
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")   # newest first
MAX_LINE = 256 * 1024          # one JSON-RPC message
MAX_OUT = 50_000               # characters of tool text returned to the agent
MAX_TASK_TEXT = 20_000         # plain-English task
MAX_DOMAIN = 60
RUN_TIMEOUT = 120              # seconds per harness run

INSTRUCTIONS = ("enki-harness tests a rule-based text filter (a Python module exposing RULES and "
                "classify(text, enabled_rules)). Call build_harness with the filter path and the cases it must "
                "catch / must not catch; it adds twins, controls, rule-off arms, an attacker slice and a "
                "pre-registered falsifier, and reports bugs row by row with the rule to blame, score last. "
                "Use suggest_terms / find_test_data for case ideas and vetted public datasets.")


class Refused(Exception):
    """Input refused before anything ran. The message is shown to the agent as-is."""


# ---- where filters may live ----------------------------------------------------------------------

def _too_broad(p):
    """A home folder, drive root or system folder is never a 'project'."""
    sysroot = os.environ.get("SystemRoot")
    return (p.parent == p or p == Path.home().resolve()
            or (sysroot and (p == Path(sysroot).resolve() or Path(sysroot).resolve() in p.parents)))


class Scope:
    def __init__(self, cwd=None, allow=(), use_cwd=True):
        self.roots = []
        c = Path(cwd or os.getcwd()).resolve()
        self.cwd = c
        if use_cwd and not _too_broad(c):
            self.roots.append(c)
        self.roots += [Path(a).resolve() for a in allow]     # explicit at install time

    def describe(self):
        return ", ".join(str(r) for r in self.roots) or "(none)"

    def check(self, system):
        """Return the resolved filter path, or raise Refused with a clear reason."""
        if not isinstance(system, str) or not system.strip():
            raise Refused("`system` is required: the path of your filter's .py file.")
        if len(system) > LIMITS["system"]:
            raise Refused(f"System under test is {len(system)} characters; the limit is {LIMITS['system']}.")
        if not self.roots:
            raise Refused("Refused: this server has no project folder to test filters in (it was started in "
                          f"{self.cwd}). Re-run the installer with --allow <your project folder>.")
        p = Path(system.strip())
        if not p.is_absolute():
            p = self.roots[0] / p
        if p.suffix != ".py":
            raise Refused(f"Refused: the filter must be a .py file; got {system!r}.")
        try:
            real = p.resolve(strict=True)
        except (OSError, RuntimeError):
            raise Refused(f"Refused: {system!r} does not exist.") from None
        if not real.is_file() or real.suffix != ".py":
            raise Refused(f"Refused: {system!r} is not a .py file.")
        if not any(real == r or r in real.parents for r in self.roots):
            raise Refused(f"Refused: {real} is outside the folder(s) this server may test: {self.describe()}. "
                          "Only filters inside your project (or a folder allowlisted at install) are run.")
        return real


# ---- tools ---------------------------------------------------------------------------------------

_STR_LIST = {"type": "array", "items": {"type": "string", "maxLength": LIMITS["case"]}, "maxItems": LIMITS["cases"]}
TOOLS = [
    {"name": "build_harness",
     "title": "Build and run a test harness for a text filter",
     "description": ("Test a rule-based text filter: a Python module exposing RULES: dict[name, callable] and "
                     "classify(text, enabled_rules) -> bool. Give the cases it must catch and must NOT catch; "
                     "the harness adds near-identical innocent twins, always-allow/always-block controls that must "
                     "fail, every rule switched off alone and in pairs, an independent attacker slice and a "
                     "pre-registered falsifier. Returns gates, then each bug with the exact message and the rule "
                     "to blame, then the score last. The filter must be inside the current project folder."),
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
         "system": {"type": "string", "maxLength": LIMITS["system"],
                    "description": "Path of the filter .py file, relative to the project folder or absolute."},
         "must_catch": dict(_STR_LIST, description="Messages the filter must flag."),
         "must_not_catch": dict(_STR_LIST, description="Messages the filter must leave alone (hard negatives)."),
         "name": {"type": "string", "maxLength": LIMITS["name"]},
         "description": {"type": "string", "maxLength": LIMITS["description"]},
         "falsifier": {"type": "string", "maxLength": LIMITS["falsifier"],
                       "description": "What result proves the filter wrong (recorded before the run)."},
         "task_text": {"type": "string", "maxLength": MAX_TASK_TEXT,
                       "description": "Alternative: the whole task in plain English ('Must catch:' / "
                                      "'Must not catch:' lists, 'System: path')."}}}},
    {"name": "suggest_terms",
     "title": "Suggest test cases for a filter domain",
     "description": ("Curated must-catch cases, hard negatives (quotes, reported speech, games, substring traps) "
                     "and evasion patterns for a domain: insults, profanity, pii, spam, prompt_injection, "
                     "off_topic, custom. Sensitive areas (sexual content, minors, self-harm, hate speech) get "
                     "pointers to vetted datasets only, never examples. local_ai=true (custom domain only) asks a "
                     "small model on this machine via Ollama; off unless asked."),
     "annotations": {"readOnlyHint": True, "openWorldHint": False},
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["domain"], "properties": {
         "domain": {"type": "string", "maxLength": MAX_DOMAIN},
         "local_ai": {"type": "boolean", "default": False},
         "description": {"type": "string", "maxLength": LIMITS["ai_description"],
                         "description": "For local_ai: what the filter does."},
         "kind": {"type": "string", "enum": ["must_catch", "must_not_catch"], "default": "must_not_catch"}}}},
    {"name": "find_test_data",
     "title": "Find vetted public test datasets",
     "description": "Verified public datasets and resources for a filter domain, with licence and source URL.",
     "annotations": {"readOnlyHint": True, "openWorldHint": False},
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["domain"], "properties": {
         "domain": {"type": "string", "maxLength": MAX_DOMAIN}}}},
]


def _args(a, allowed):
    if not isinstance(a, dict):
        raise Refused("arguments must be an object")
    extra = set(a) - set(allowed)
    if extra:
        raise Refused("unknown argument(s): " + ", ".join(sorted(extra)))
    for k, v in a.items():
        want = allowed[k]
        ok = (isinstance(v, str) if want is str else isinstance(v, bool) if want is bool else
              isinstance(v, list) and all(isinstance(x, str) for x in v))
        if not ok:
            raise Refused(f"`{k}` must be {'a string' if want is str else 'true/false' if want is bool else 'a list of strings'}")
    return a


def build_harness(a, scope, timeout=RUN_TIMEOUT):
    a = _args(a, dict(system=str, must_catch=list, must_not_catch=list, name=str, description=str,
                      falsifier=str, task_text=str))
    if "task_text" in a:
        if len(a["task_text"]) > MAX_TASK_TEXT:
            raise Refused(f"task_text is {len(a['task_text'])} characters; the limit is {MAX_TASK_TEXT}.")
        task = parse_plain(a["task_text"])
    else:
        task = Task.from_dict({})
    for k in ("name", "description"):
        if k in a:
            setattr(task, k, a[k].strip())
    for k in ("must_catch", "must_not_catch"):
        if k in a:
            if len(a[k]) > LIMITS["cases"]:      # checked before stripping, so nothing is silently dropped
                raise Refused(f"{k} has {len(a[k])} cases; the limit is {LIMITS['cases']}.")
            setattr(task, k, [x.strip() for x in a[k] if x.strip()])
    if "falsifier" in a and a["falsifier"].strip():
        task.falsifier = dict(task.falsifier, text=a["falsifier"].strip())
    if "system" in a:
        task.system = a["system"]
    errs = task.limit_errors()
    if errs:
        raise Refused("Input over the limit (nothing was run):\n  " + "\n  ".join(errs))
    task.system = str(scope.check(task.system))
    task.author = "user"                          # an agent cannot spoof the independence gate
    with tempfile.TemporaryDirectory(prefix="hb_mcp_") as tmp:
        tf, out = Path(tmp) / "task.json", Path(tmp) / "report.json"
        tf.write_text(json.dumps(task.to_dict()), encoding="utf-8")
        env = dict(os.environ)
        if env.get("HB_DATA"):                    # the child runs in ROOT, so a relative path must be fixed here
            env["HB_DATA"] = str(Path(env["HB_DATA"]).resolve())
        try:
            p = subprocess.run([sys.executable, "-I", str(ROOT / "hb" / "__main__.py"), "run", str(tf), "--out", str(out)],
                               cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return _err(f"The harness run took longer than {timeout}s and was stopped (does the filter loop?).")
        if not out.exists():
            tail = "\n".join((p.stderr or "").strip().splitlines()[-8:])[-2000:]
            return _err(f"The harness could not run this filter (exit {p.returncode}):\n{tail}")
        r = json.loads(out.read_text(encoding="utf-8"))
    return _ok(render_agent(r, MAX_OUT))


def _find_domain(name):
    """Curated domain by id or label; free text that touches a guarded area maps to that area (strict)."""
    n = name.strip().lower()
    for d in suggest.for_ui():
        if n in (d["id"], d["label"].lower(), d["id"].replace("_", " ")):
            return d
    hit = suggest.guard(name)
    if hit:
        return next(d for d in suggest.for_ui() if d["id"] == hit)
    return None


def _unknown(name):
    ids = ", ".join(d["id"] for d in suggest.for_ui())
    return _err(f"No curated domain {name!r}. Pick one of: {ids}. For anything else use domain 'custom' "
                "(and local_ai=true with a description, if you want ideas from a local model).")


def _pointers(d):
    lines = [f"- {x['name']} ({x['licence']}): {x['use']}\n  {x['url']}" for x in d["datasets"]]
    return lines or ["- (no verified dataset for this area yet)"]


def suggest_terms(a, ai=None):
    a = _args(a, dict(domain=str, local_ai=bool, description=str, kind=str))
    if len(a.get("domain", "")) > MAX_DOMAIN:
        raise Refused(f"domain is {len(a['domain'])} characters; the limit is {MAX_DOMAIN}.")
    d = _find_domain(a.get("domain", ""))
    if not d:
        return _unknown(a.get("domain", ""))
    if d["guarded"]:
        return _ok(f"{d['label']}: pointers only.\n{d['note']}\n\nVetted sources:\n" + "\n".join(_pointers(d)))
    if a.get("local_ai"):
        return _local_ai(d, a, ai or suggest)
    out = [f"{d['label']}: {d['blurb']}", "", "must_catch ideas:"] + [f"- {x}" for x in d["must_catch"]]
    out += ["", "must_not_catch ideas (hard negatives):"] + [f"- {x}" for x in d["must_not_catch"]]
    out += ["", "evasions the attacker arm tries (and you may add):"] + [f"- {x}" for x in d["evasions"]]
    out += ["", "Real data: call find_test_data with the same domain."]
    return _ok("\n".join(out))


def _local_ai(d, a, ai):
    """Same rules as the web page: custom domain only, guard first, local Ollama only, small model."""
    desc = a.get("description", "")
    if suggest.guard(desc):
        return _err("Local AI is off here: this area is guarded. Use find_test_data for vetted datasets.")
    if d["id"] != "custom":
        return _err("local_ai is for the 'custom' domain; the curated suggestions cover this one.")
    if not desc.strip():
        return _err("local_ai needs `description`: what your filter does.")
    if len(desc) > LIMITS["ai_description"]:
        raise Refused(f"description is {len(desc)} characters; the limit is {LIMITS['ai_description']}.")
    kind = a.get("kind", "must_not_catch")
    if kind not in ("must_catch", "must_not_catch"):
        raise Refused("kind must be must_catch or must_not_catch")
    st = ai.ai_status()
    if not st["available"]:
        return _err(st["note"])
    try:
        res = ai.suggest_ai(desc, kind)
    except ai.Blocked:
        return _err("Local AI is off here: this area is guarded.")
    except Exception as exc:
        return _err(f"Local AI unavailable: {type(exc).__name__}")
    items = [f"- {x}" for x in res["items"]] or ["- (the model returned nothing usable)"]
    return _ok(f"{kind} ideas from {res['model']} (local, via Ollama; nothing left this machine):\n" + "\n".join(items))


def find_test_data(a):
    a = _args(a, dict(domain=str))
    if len(a.get("domain", "")) > MAX_DOMAIN:
        raise Refused(f"domain is {len(a['domain'])} characters; the limit is {MAX_DOMAIN}.")
    d = _find_domain(a.get("domain", ""))
    if not d:
        return _unknown(a.get("domain", ""))
    head = [f"{d['label']}: verified sources (licence checked; use under their own terms)"]
    if d["guarded"]:
        head.append(d["note"])
    return _ok("\n".join(head + [""] + _pointers(d)))


def _ok(text):
    if len(text) > MAX_OUT:
        text = text[:MAX_OUT] + f"\n... cut at the {MAX_OUT}-character output cap"
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _err(text):
    return {"content": [{"type": "text", "text": text[:MAX_OUT]}], "isError": True}


# ---- JSON-RPC ------------------------------------------------------------------------------------

class Server:
    def __init__(self, scope, ai=None, timeout=RUN_TIMEOUT):
        self.scope, self.ai, self.timeout = scope, ai, timeout

    def call(self, name, args):
        try:
            if name == "build_harness":
                return build_harness(args, self.scope, self.timeout)
            if name == "suggest_terms":
                return suggest_terms(args, self.ai)
            return find_test_data(args)
        except Refused as exc:
            return _err(str(exc))
        except Exception as exc:  # a tool bug is reported to the agent, the server keeps running
            return _err(f"internal error: {type(exc).__name__}: {str(exc)[:500]}")

    def handle(self, msg):
        """One parsed message -> a response dict, or None for notifications / client responses."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _rpc_err(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
        method, mid = msg.get("method"), msg.get("id")
        if os.environ.get("HB_MCP_TRACE") and isinstance(method, str):   # method names only, never arguments
            with open(os.environ["HB_MCP_TRACE"], "a", encoding="utf-8") as fh:
                fh.write(method + "\n")
        if not isinstance(method, str):
            return None if "id" in msg and ("result" in msg or "error" in msg) else _rpc_err(mid, -32600, "Invalid Request")
        if "id" not in msg:
            return None                                       # notification (initialized, cancelled, ...)
        params = msg.get("params") or {}
        if method == "initialize":
            want = params.get("protocolVersion") if isinstance(params, dict) else None
            return _rpc_ok(mid, {"protocolVersion": want if want in VERSIONS else VERSIONS[0],
                                 "capabilities": {"tools": {"listChanged": False}},
                                 "serverInfo": {"name": NAME, "title": "Enki Harness", "version": VERSION},
                                 "instructions": INSTRUCTIONS})
        if method == "ping":
            return _rpc_ok(mid, {})
        if method == "tools/list":
            return _rpc_ok(mid, {"tools": TOOLS})
        if method == "tools/call":
            if not isinstance(params, dict) or params.get("name") not in {t["name"] for t in TOOLS}:
                return _rpc_err(mid, -32602, f"Unknown tool: {params.get('name') if isinstance(params, dict) else None}")
            return _rpc_ok(mid, self.call(params["name"], params.get("arguments") or {}))
        return _rpc_err(mid, -32601, f"Method not found: {method}")

    def handle_line(self, raw):
        try:
            msg = json.loads(raw)
        except ValueError:
            return _rpc_err(None, -32700, "Parse error")
        if isinstance(msg, list):                             # 2025-03-26 batches
            out = [r for r in (self.handle(m) for m in msg[:100]) if r is not None]
            return out or None
        return self.handle(msg)

    def serve(self, inp, out):
        while True:
            raw = inp.readline(MAX_LINE + 1)
            if not raw:
                return
            if len(raw) > MAX_LINE and not raw.endswith(b"\n"):
                while True:                                   # drain the rest of the oversize line
                    more = inp.readline(MAX_LINE)
                    if not more or more.endswith(b"\n"):
                        break
                resp = _rpc_err(None, -32600, f"Message over {MAX_LINE} bytes refused")
            elif not raw.strip():
                continue
            else:
                resp = self.handle_line(raw.decode("utf-8", "replace"))
            if resp is not None:
                out.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
                out.flush()


def _rpc_ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _rpc_err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main(allow=(), use_cwd=True):
    # The protocol owns the real stdout. Anything else that prints (our code, a filter) goes to stderr.
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    scope = Scope(allow=allow, use_cwd=use_cwd)
    print(f"enki-harness MCP server; filters allowed in: {scope.describe()}", file=sys.stderr)
    Server(scope).serve(sys.stdin.buffer, proto)
    return 0
