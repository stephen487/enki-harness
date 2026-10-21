"""CLI and the one-page local web UI. Both call the same engine.

  python -m hb run examples/insult_task.json [--system examples/correct_filter.py] [--json]
  python -m hb run-text task.txt            (plain-English task file)
  python -m hb export-shapes [out.json]     (local file only; nothing is sent anywhere)
  python -m hb serve [--port 8765]          (http://127.0.0.1:8765, localhost only)
"""
import argparse
import hmac
import json
import secrets
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .engine import run
from .learning import DATA, Store, export_shapes, load_settings
from .report import render_html, render_text
from . import suggest, web
from .task import LIMITS, Task, parse_plain

ROOT = Path(__file__).resolve().parent.parent

EXAMPLE_TEXT = """Name: kids-chat insult filter
Catch insults and threats aimed at another player. Do not catch reported speech or talk about the game.
System: examples/planted_filter.py
Must catch:
- I hate you
- you are so stupid
- you are an idiot
- what a loser
- I will kill you
- you are so dumb
Must not catch:
- I love this level
- He said "you are stupid" and I told the teacher
- I will kill the dragon in the game
- She said "kill the boss in the game" to me
- good game everyone
Falsifier: The filter is wrong if it misses ANY must-catch case or flags ANY must-not-catch case or twin.
"""


def _resolve(task):
    p = Path(task.system)
    task.system = str(p if p.is_absolute() else ROOT / p)
    return task


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "run-text"):
        s = sub.add_parser(name)
        s.add_argument("task")
        s.add_argument("--system")
        s.add_argument("--json", action="store_true")
        s.add_argument("--out", help="write the JSON report to this file (used by the MCP server)")
    s = sub.add_parser("export-shapes")
    s.add_argument("out", nargs="?", default=str(DATA / "shapes_export.json"))
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8765)
    s = sub.add_parser("mcp", help="MCP server on stdio, for AI coding tools (registered by `install`)")
    s.add_argument("--allow", action="append", default=[], help="extra folder whose .py filters may be tested")
    s.add_argument("--no-cwd-root", action="store_true", help="do not trust the folder the tool starts us in")
    for name in ("install", "uninstall"):
        s = sub.add_parser(name, help=f"{name} the MCP server in the AI coding tools found on this machine")
        s.add_argument("--yes", action="store_true", help="no questions: every detected (or --tools) tool")
        s.add_argument("--tools", help="comma list, e.g. claude-code,cursor,vscode,codex,claude-desktop,windsurf")
        s.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
        if name == "install":
            s.add_argument("--allow", action="append", default=[], help="folder the server may test filters in "
                           "(needed for Claude Desktop, which has no project folder)")
    a = ap.parse_args(argv)

    if a.cmd == "mcp":
        from . import mcp_server
        return mcp_server.main(allow=a.allow, use_cwd=not a.no_cwd_root)
    if a.cmd in ("install", "uninstall"):
        from . import install
        return install.main(a.cmd, yes=a.yes, tools=a.tools, dry_run=a.dry_run, allow=getattr(a, "allow", []))
    if a.cmd in ("run", "run-text"):
        task = Task.load(a.task) if a.cmd == "run" else parse_plain(Path(a.task).read_text(encoding="utf-8"))
        if a.system:
            task.system = a.system
        r = run(_resolve(task))
        if a.out:
            Path(a.out).write_text(json.dumps(r, default=str), encoding="utf-8")
        else:
            print(json.dumps(r, indent=2, default=str) if a.json else render_text(r))
        return 0 if r["all_gates_green"] else 2          # exit 2 = score withheld (a gate is red)
    if a.cmd == "export-shapes":
        shapes = export_shapes(Store(), a.out)
        print(f"wrote {len(shapes)} attack shapes to {a.out} (local file; nothing sent)")
        return 0
    serve(a.port)
    return 0


EXAMPLE_TASK = json.loads((ROOT / "examples" / "insult_task.json").read_text(encoding="utf-8"))
EXAMPLE_FORM = dict(EXAMPLE_TASK, domain="insults", falsifier=EXAMPLE_TASK["falsifier"]["text"])
AI_STATUS = suggest.ai_status
MAX_BODY = 64 * 1024
TOKEN = secrets.token_urlsafe(32)   # per server session; embedded in the page, required on every POST


class Refused(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def check_system_path(task):
    """The server imports and runs this file, so it must exist and be a .py file."""
    p = Path(task.system)
    if not task.system or p.suffix != ".py" or not p.is_file():
        raise Refused(400, f"System under test must be an existing .py file; got {task.system or '(empty)'!r}.")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _page(self, form=None, report="", msg="", plain_text=EXAMPLE_TEXT, code=200):
        body = web.page(form or EXAMPLE_FORM, report=report, msg=msg, db=str(DATA / "learning.db"),
                        share=load_settings()["share_shapes"], ai=AI_STATUS(), plain_text=plain_text,
                        token=TOKEN).encode()
        self._send(code, body, "text/html; charset=utf-8")

    def _local_only(self):
        """Bound to 127.0.0.1, and also: Host must be 127.0.0.1:<port> or localhost:<port> (DNS rebinding),
        and any Origin must be this same host (cross-site POST)."""
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        if (self.headers.get("Host") or "") not in allowed:
            raise Refused(403, "Refused: Host header is not this local server.")
        if origin is not None and urllib.parse.urlparse(origin).netloc not in allowed:
            raise Refused(403, "Refused: cross-origin request.")

    def _refuse(self, exc, as_json=False):
        if as_json:
            return self._json(exc.code, {"error": str(exc)})
        self._send(exc.code, str(exc).encode(), "text/plain; charset=utf-8")

    def do_GET(self):
        try:
            self._local_only()
        except Refused as exc:
            return self._refuse(exc)
        self._page()

    def do_POST(self):
        path = self.path.split("#")[0]
        is_json = path in ("/guard", "/suggest_ai")
        try:
            self._local_only()
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                left = min(n, 4 * MAX_BODY)            # drain a little so the client can read the refusal
                while left > 0:
                    chunk = self.rfile.read(min(left, 65536))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                raise Refused(413, f"Refused: request is {n} bytes; the limit is {MAX_BODY}.")
            raw = self.rfile.read(n).decode("utf-8", "replace")
            if is_json:
                if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                    raise Refused(415, "JSON only.")
                token = self.headers.get("X-HB-Token", "")
            else:
                form = urllib.parse.parse_qs(raw)
                token = (form.get("csrf") or [""])[0]
            if not hmac.compare_digest(token.encode(), TOKEN.encode()):
                raise Refused(403, "Refused: missing or wrong page token. Reload the page.")
        except Refused as exc:
            return self._refuse(exc, is_json)

        if is_json:
            try:
                body = json.loads(raw or "{}")
            except ValueError:
                return self._json(400, {"error": "bad JSON"})
            if path == "/guard":
                return self._json(200, {"blocked": suggest.guard(str(body.get("text", ""))[:2000])})
            return self._suggest_ai(body)
        if path == "/export":
            out = DATA / "shapes_export.json"
            shapes = export_shapes(Store(), out)
            return self._page(msg=f"wrote {len(shapes)} shapes to {out}")
        plain = "task" in form
        text = form.get("task", [""])[0]
        task = parse_plain(text) if plain else Task.from_form(form)
        shown = dict(task.to_dict(), domain=(form.get("domain") or ["custom"])[0], falsifier=task.falsifier["text"])
        keep = text if plain else EXAMPLE_TEXT
        errs = task.limit_errors()
        if errs:
            return self._page(form=shown, report=web.error_block("Input over the limit (nothing was run)", errs),
                              plain_text=keep, code=400)
        try:
            check_system_path(_resolve(task))
        except Refused as exc:
            return self._page(form=shown, report=web.error_block("Could not run", [str(exc)]), plain_text=keep,
                              code=exc.code)
        try:
            report = render_html(run(task))
        except Exception as exc:  # show the error on the page rather than a stack trace
            report = web.error_block("Could not run", [type(exc).__name__ + ": " + str(exc)])
        self._page(form=shown, report=report, plain_text=keep)

    def _suggest_ai(self, body):
        d = suggest.domain(str(body.get("domain", "")))
        desc = str(body.get("description", ""))
        if not d or d.get("guarded") or suggest.guard(desc):
            return self._json(403, {"error": "Local AI is off here: this area is guarded. Use the vetted datasets instead.",
                                    "blocked": True})
        if d["id"] != "custom":
            return self._json(400, {"error": "Local AI is for custom domains; use the curated suggestions above."})
        if not desc.strip():
            return self._json(400, {"error": "Describe what your filter does first."})
        if len(desc) > LIMITS["ai_description"]:
            return self._json(400, {"error": f"Description for suggestions is {len(desc)} characters; "
                                             f"the limit is {LIMITS['ai_description']}."})
        try:
            return self._json(200, suggest.suggest_ai(desc, str(body.get("kind", ""))))
        except suggest.Blocked:
            return self._json(403, {"error": "Local AI is off here: this area is guarded.", "blocked": True})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:
            return self._json(503, {"error": f"Local AI unavailable: {type(exc).__name__}"})

    def log_message(self, *a):
        pass


def serve(port=8765):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Harness Builder UI on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
