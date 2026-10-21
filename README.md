# enki-harness

**A test harness for your AI filters that refuses to flatter you.**

You wrote a filter. You wrote its tests. It passes. That proves less than you think: the person who wrote the
code wrote the tests, so the tests check the cases you already thought of.

`enki-harness` builds the harness *for* you, and adds the arms you would skip:

| arm | what it does |
|---|---|
| **twins** | for every case it must catch, a near-identical innocent case it must **not** flag |
| **control** | dumb stand-ins (always-allow, always-block) that **must fail**, or the harness itself is broken |
| **rules off** | switches each rule off, alone **and in pairs**, to find dead rules and rules that cancel each other out |
| **attacker** | an independent slice written by a different generator than your cases (leet, spacing, stretching) |
| **falsifier** | you state what result proves you wrong **before** it runs; it is timestamped |

**No score until every gate is green.** Rows come first, numbers last. A 0% or 100% rate is flagged
"check the instrument", because a check that never fires and a check that always fires look the same.

## Quick start

```bash
python -m venv venv
venv\Scripts\python -m pip install pytest mcp  # tests only (mcp = the official SDK client the protocol tests use); the tool itself is standard library
venv\Scripts\python -m hb serve                # http://127.0.0.1:8765  (localhost only)
venv\Scripts\python -m hb run examples\insult_task.json
```

The page opens with a working example (a filter with four planted bugs). Press run and watch it find them.

## Use it inside your AI coding tool

One command registers it as an MCP server in the tools it finds on your machine (Claude Code, VS Code with GitHub
Copilot, Cursor, OpenAI Codex, Claude Desktop, Windsurf). It asks before touching each one:

```bash
venv\Scripts\python -m hb install
```

Then just ask your agent:

> test my filter in filters/swear.py with enki-harness

The agent gets the gates, every bug with the exact message and the rule to blame, and the score last. It can also
call `suggest_terms` (case ideas and hard negatives for a domain) and `find_test_data` (vetted public datasets).

- Backs up every config file before editing it, and touches only its own `enki-harness` entry.
- A config it cannot parse is left exactly as it was, and it tells you.
- `--yes` for no questions, `--tools cursor,vscode` to choose, `--dry-run` to preview, `python -m hb uninstall` to remove.
- The server only runs filters inside the project folder your tool opens. Claude Desktop has no project folder, so
  name one: `python -m hb install --tools claude-desktop --allow path/to/your/project`.
- The server is standard-library Python too: nothing to install.

## Your filter

Point it at a Python module that exposes `RULES` and `classify(text, enabled_rules) -> bool`. That is the whole
contract.

## Local by default

Runs on your machine. No account, no telemetry, no network calls. Optional suggestions from a local Ollama
model are off unless you ask for them.

---

Made by [Enki Labs](https://enkilabs.co.uk).
