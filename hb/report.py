"""Rows first, then defects, then gates, then the score (only if every gate is green)."""
import html


def _yn(b):
    return "FLAG" if b else "allow"


def sections(r):
    """Shared structure for text and HTML: list of (title, header, rows)."""
    rows = [(x["id"], x["arm"], x["strategy"], x["text"], _yn(x["expected"]), _yn(x["got"]),
             "ok" if x["ok"] else "WRONG", x["author"] + (" [held-out]" if x["heldout"] else ""))
            for x in r["rows"]]
    out = [("1. ROWS (" + r["coverage"] + ")", ("id", "arm", "strategy", "text", "expected", "got", "", "author"), rows)]
    d = [(x["kind"], ", ".join(x.get("rules") or x.get("responsible") or []) or "-", x["text"]) for x in r["defects"]]
    out.append(("2. DEFECTS FOUND (" + str(len(d)) + ")", ("kind", "rules (removal fixes it)", "detail"), d))
    info = [("redundant pair (info)", " x ".join(e["rules"]), "rows " + ", ".join(e["rows"]) +
             ": each looks dead alone, together they are load-bearing") for e in r["redundant_pairs"]]
    info += [("control", k, f"wrong on {v['wrong']} rows; same as system: {v['same_as_system']}")
             for k, v in r["controls"].items()]
    lr = r["learning"]
    info.append(("learning", "attacker used: " + ", ".join(r["attacker_strategies"]),
                 lr["note"] + (f" (default found {lr['default_found']}, learned found {lr['learned_found']} "
                               f"on {lr['heldout_cases']} held-out case(s))" if lr.get("heldout_cases") else "")))
    out.append(("3. RULE-OFF / CONTROL / LEARNING NOTES", ("what", "which", "detail"), info))
    g = [(k, "GREEN" if v["ok"] else "RED", v["why"]) for k, v in r["gates"].items()]
    out.append(("4. GATES", ("gate", "status", "why"), g))
    return out


def score_lines(r):
    pre = r["prereg"] or {}
    f = pre.get("falsifier", {})
    lines = [f"Pre-registered falsifier ({pre.get('created_at', 'MISSING')}): {f.get('text', 'MISSING')}"]
    if r["score"] is None:
        red = [k for k, v in r["gates"].items() if not v["ok"]]
        lines.append("SCORE WITHHELD: gate(s) not green: " + ", ".join(red))
        return lines
    for k, (v, n) in r["score"].items():
        lines.append(f"{k}: {'n/a' if v is None else f'{v:.0%}'} (n={n})")
    lines += r["instrument_flags"]
    lines.append(f"Falsifier verdict: {'FALSIFIED (system is wrong)' if r['falsified'] else 'not falsified'}"
                 f" -- missed {r['missed']}, false flags {r['false_flags']}")
    return lines


def render_text(r):
    out = [f"HARNESS REPORT: {r['task']}  system={r['system']}  rules={', '.join(r['rule_names'])}", ""]
    for title, head, rows in sections(r):
        out.append(title)
        out.append("  " + " | ".join(head))
        out += ["  " + " | ".join(str(c) for c in row) for row in rows] or ["  (none)"]
        out.append("")
    out.append("5. SCORE")
    out += ["  " + x for x in score_lines(r)]
    out.append(f"\nrun dir: {r['run_dir']}")
    return "\n".join(out)


GROUPS = [  # kind -> (plain-English title, what it means)
    ("interaction", "Two rules cancel each other out",
     "Each rule works alone, but with both switched on the system gets these rows wrong."),
    ("dead_rule", "Dead rule: removing it changes nothing",
     "Switching this rule off changed no row, so no case tests it (or it never fires)."),
    ("twin_false_flag", "Flags an innocent twin",
     "A near-identical harmless message got flagged. Removing the named rule fixes it."),
    ("task_false_flag", "Flags a must-NOT-catch case", "A message you said must pass got flagged."),
    ("task_miss", "Missed a must-catch case", "A message you said must be caught got through."),
    ("attacker_miss", "Attacker evasions", "An independent attacker disguised a must-catch case and it got through."),
]


def render_agent(r, cap=50_000):
    """Plain text for an AI coding agent (the MCP tool). Same ratified order as the page: gate STATUSES,
    then each bug with the exact row text and the rule to blame, then notes, and the score LAST.
    If the text would pass `cap` characters, bug lines are cut (and the cut is said), never the gates or
    the score, so the numbers can never appear without the rows above them being acknowledged."""
    head = [f"enki-harness: {r['task']}  system={r['system']}  rules={', '.join(r['rule_names'])}", "", "GATES"]
    head += [f"  {'✓' if v['ok'] else '✗'} {k}: {v['why']}" for k, v in r["gates"].items()]
    bugs = ["", f"BUGS ({len(r['defects'])}) rows the full system gets wrong; 'blame' = switching that rule off fixes the row"]
    if not r["defects"]:
        bugs.append("  ✓ no bugs found in these rows")
    for kind, title, means in GROUPS:
        ds = [d for d in r["defects"] if d["kind"] == kind]
        if not ds:
            continue
        bugs += [f"  ✗ {title}", f"    # {means}"]
        for d in ds:
            if kind == "interaction":
                bugs.append(f"    - blame: {d['rules'][0]} x {d['rules'][1]} ({d['text']})")
                bugs += [f"        row: \"{_row_text(r, rid)}\"" for rid in d["rows"]]
            elif kind == "dead_rule":
                bugs.append(f"    - blame: {d['rules'][0]} (switching it off changes no row: untested or dead)")
            else:
                who = d.get("responsible") or []
                blame = ("blame: " + ", ".join(who)) if who else "blame: no single rule (a rule is missing?)"
                via = f" via {d['strategy']}" if kind == "attacker_miss" else ""
                bugs.append(f"    - \"{d['text']}\"{via}  [{blame}]")
        if kind == "attacker_miss":   # the count comes AFTER its rows (units before aggregates)
            bugs.append(f"    # {len(ds)} disguised case(s) got through")
    notes = ["", "NOTES"] + [f"  - {a} {b}: {c}" for a, b, c in sections(r)[2][2]]
    tail = ["", "SCORE (last, after the rows)"] + ["  " + x for x in score_lines(r)]
    tail += [f"  full rows ({len(r['rows'])}): {r['run_dir']}\\report.json".replace("\\", "/")]

    def size(parts):
        return sum(len(x) + 1 for x in parts)
    fixed = size(head) + size(notes) + size(tail) + 200
    if fixed + size(bugs) > cap:
        keep, used = [], fixed
        for line in bugs:
            if used + len(line) + 1 > cap:
                break
            keep.append(line)
            used += len(line) + 1
        bugs = keep + [f"  ... {len(bugs) - len(keep)} more bug line(s) cut at the {cap}-character output cap; "
                       f"every row is in report.json"]
        if fixed + size(bugs) > cap:   # pathological notes: drop them, keep gates + score
            notes = ["", "NOTES cut at the output cap"]
    return "\n".join(head + bugs + notes + tail)


def _row_text(r, rid):
    for x in r["rows"]:
        if x["id"] == rid:
            return x["text"]
    return rid


def render_html(r):
    """Terminal-style report. Order is the ratified 'units before aggregates': gate STATUSES, then concrete
    defects (row text), then every row, then notes, and only then any computed number (score last)."""
    e = html.escape
    out = ["<section class='out' aria-label='Results'>",
           f"<div class='cmd'><span class='pr'>enki &#9656;</span> run harness <span class='str'>&quot;{e(r['task'])}&quot;</span></div>",
           f"<div class='ln dim'>system {e(r['system'])} &middot; rules: "
           + " ".join(f"<span class='tag'>{e(n)}</span>" for n in r["rule_names"]) + "</div>",
           "<div class='h'>&#9472;&#9472; <span class='hh'>Gates</span></div>"]
    for k, v in r["gates"].items():
        out.append(f"<div class='ln chip {'ok' if v['ok'] else 'bad'}'><span class='mk'>{'&#10003;' if v['ok'] else '&#10007;'}</span>"
                   f" <b>{e(k)}</b> <span class='why'>{e(v['why'])}</span></div>")

    out.append("<div class='h'>&#9472;&#9472; <span class='hh'>Bugs found</span></div>")
    if not r["defects"]:
        out.append("<div class='ln good'>&#10003; No bugs found in these rows.</div>")
    for kind, title, means in GROUPS:
        ds = [d for d in r["defects"] if d["kind"] == kind]
        if not ds:
            continue
        out.append(f"<div class='bug {e(kind)}'><div class='ln'><span class='bad'>&#10007;</span> <b>{e(title)}</b></div>"
                   f"<div class='ln dim'># {e(means)}</div><ul>")
        for d in ds:
            if kind == "interaction":
                rows = "".join(f"<li>&ldquo;{e(_row_text(r, rid))}&rdquo;</li>" for rid in d["rows"])
                out.append(f"<li><span class='tag gold'>{e(d['rules'][0])}</span> &times; "
                           f"<span class='tag gold'>{e(d['rules'][1])}</span> <span class='dim'>{e(d['text'])}</span><ul>{rows}</ul></li>")
            elif kind == "dead_rule":
                out.append(f"<li><span class='tag gold'>{e(d['rules'][0])}</span></li>")
            else:
                who = d.get("responsible") or []
                extra = f" <span class='dim'>via {e(d['strategy'])}</span>" if kind == "attacker_miss" else ""
                fix = (" <span class='dim'>&rarr; removing</span> " + " ".join(f"<span class='tag gold'>{e(n)}</span>" for n in who)
                       + " <span class='dim'>fixes it</span>") if who and kind != "attacker_miss" else ""
                out.append(f"<li>&ldquo;{e(d['text'])}&rdquo;{extra}{fix}</li>")
        out.append("</ul>")
        if kind == "attacker_miss":   # the count comes AFTER its rows (units before aggregates)
            out.append(f"<div class='ln dim'># {len(ds)} disguised case(s) got through.</div>")
        out.append("</div>")

    out.append(f"<details class='rowsbox'{' open' if len(r['rows']) <= 40 else ''}><summary class='h'>&#9472;&#9472; "
               f"<span class='hh'>Every row</span> <span class='dim'>({e(r['coverage'])}; wrong rows highlighted)</span></summary>"
               "<div class='scroll'><table>"
               "<tr><th>id</th><th>arm</th><th>strategy</th><th>text</th><th>expected</th><th>got</th><th></th><th>author</th></tr>")
    for x in r["rows"]:
        out.append(f"<tr class='{'' if x['ok'] else 'wrong'}'><td>{e(x['id'])}</td><td>{e(x['arm'])}</td><td>{e(x['strategy'])}</td>"
                   f"<td>{e(x['text'])}</td><td>{_yn(x['expected'])}</td><td>{_yn(x['got'])}</td>"
                   f"<td>{'ok' if x['ok'] else '<b>WRONG</b>'}</td><td>{e(x['author'] + (' [held-out]' if x['heldout'] else ''))}</td></tr>")
    out.append("</table></div></details>")

    notes = sections(r)[2][2]
    out.append("<div class='h'>&#9472;&#9472; <span class='hh'>Notes</span></div>"
               + "".join(f"<div class='ln'><span class='dim'>&middot;</span> <b>{e(a)}</b> {e(b)}: <span class='dim'>{e(c)}</span></div>"
                         for a, b, c in notes))

    out.append("<div class='score'><div class='h'>&#9472;&#9472; <span class='hh'>Score</span></div>"
               + "".join(f"<div class='ln {'flag' if 'check the instrument' in x else ''}'>{e(x)}</div>" for x in score_lines(r))
               + f"<div class='ln dim'>run dir: {e(r['run_dir'])}</div></div>")
    out.append("<div class='cmd'><span class='pr'>enki &#9656;</span> <span class='cur'></span></div></section>")
    return "\n".join(out)
