"""The one-page local UI (served by cli.Handler): a terminal session in the browser.
Inline CSS/JS, no build step, no CDN. Enki brand tokens from Enki Website/deploy/site.css.
Keyboard-first: Enter = next field / new case row, Ctrl+Enter = run. Works at ~380px and on desktop."""
import html
import json

from . import suggest
from .task import DEFAULT_FALSIFIER, LIMITS

CSS = """
:root{--bg:#030712;--bg2:#070d18;--panel:#0b1220;--panel2:#0e1626;--line:#152033;--glow:#0ea5e9;--glow-soft:#38bdf8;
--gold:#f5b955;--text:#f2f7fc;--ink:#c7d5e4;--muted:#7d93a8;--dim:#728599;--ok:#4ade80;--bad:#fb7185;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg)}
body{color:var(--ink);font:14px/1.6 var(--mono);-webkit-font-smoothing:antialiased;
background:radial-gradient(900px 420px at 0% -10%,rgba(14,165,233,.12),transparent 60%),var(--bg)}
.wrap{max-width:960px;margin:0 auto;padding:16px 16px 48px}
.term{border:1px solid var(--line);border-radius:12px;background:rgba(7,13,24,.92);box-shadow:0 0 0 1px rgba(14,165,233,.06),0 20px 60px rgba(0,0,0,.5);overflow:hidden}
.bar{display:flex;align-items:center;gap:8px;padding:9px 12px;border-bottom:1px solid var(--line);background:var(--panel);color:var(--dim);font-size:12px}
.bar i{width:10px;height:10px;border-radius:50%;background:#1d2a3d;display:inline-block;flex:none}
.bar i:first-child{background:var(--glow);box-shadow:0 0 8px var(--glow-soft)}
.bar .t{flex:1;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .internal{color:var(--gold);white-space:nowrap}
.body{padding:16px}
.banner{color:var(--glow-soft);margin:0 0 4px;font-weight:700;letter-spacing:.04em}
.ln{margin:2px 0;overflow-wrap:anywhere}
.dim{color:var(--dim)} .good{color:var(--ok)} .bad{color:var(--bad)} .gold{color:var(--gold)}
.pr{color:var(--glow);text-shadow:0 0 10px rgba(56,189,248,.45);font-weight:700;white-space:nowrap}
.q{color:var(--text)}
.field{display:grid;grid-template-columns:minmax(0,15em) minmax(0,1fr);gap:4px 12px;align-items:center;margin:8px 0}
@media(max-width:640px){.field{grid-template-columns:1fr}}
.field label{color:var(--text)}
.field label .pr{margin-right:6px}
.inp{display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--line);min-width:0}
.inp:focus-within{border-bottom-color:var(--glow);box-shadow:0 1px 0 0 var(--glow)}
input[type=text],select,textarea{flex:1;min-width:0;width:100%;background:transparent;color:var(--text);border:0;outline:none;
font:14px var(--mono);padding:6px 0;caret-color:var(--glow-soft)}
select option{background:var(--panel);color:var(--text)}
textarea{border:1px solid var(--line);border-radius:8px;padding:10px;min-height:170px;font-size:13px}
textarea:focus{border-color:var(--glow)}
input::placeholder{color:#4f6378}
.cnt{font-size:11px;color:var(--dim);white-space:nowrap}
.cnt.near{color:var(--gold)} .cnt.full{color:var(--bad)}
.sec{margin:18px 0 6px;color:var(--text)}
.rows .row{display:flex;align-items:center;gap:8px;margin:2px 0}
.rows .row::before{content:'+';color:var(--glow-soft)}
.rows[data-list=must_not_catch] .row::before{content:'-';color:var(--gold)}
button{font:13px var(--mono);border-radius:6px;cursor:pointer;border:1px solid var(--line);background:var(--panel2);color:var(--ink);padding:5px 10px}
button:hover:not(:disabled){border-color:var(--glow);color:var(--text)}
button:focus-visible,select:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--glow-soft);outline-offset:2px}
button:disabled{opacity:.4;cursor:not-allowed}
.x{border:0;background:none;color:var(--dim);padding:2px 6px;font-size:16px;line-height:1}
.x:hover{color:var(--bad)}
.run{background:var(--glow);border-color:var(--glow);color:#02101c;font-weight:700;padding:9px 16px;box-shadow:0 0 22px rgba(14,165,233,.35)}
.run:hover:not(:disabled){background:var(--glow-soft);color:#02101c}
.kbd{font-size:11px;color:var(--dim)} kbd{font:11px var(--mono);border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 5px;color:var(--ink)}
.sugg{margin:6px 0 0 1.4em}
.pill{margin:0 6px 6px 0;padding:3px 8px;border:1px dashed #1f3a56;background:transparent;color:var(--ink);text-align:left}
.pill:hover{border-style:solid;border-color:var(--glow-soft);color:var(--text)}
.pill.used{opacity:.35}
.guardnote{border-left:2px solid var(--gold);padding:4px 10px;color:#f3dcb0;margin:8px 0}
a{color:var(--glow-soft)} a:hover{color:#7dd3fc}
.tag{border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:12px}
.tag.gold{color:var(--gold);border-color:#5c4521}
.out{margin-top:18px;border-top:1px dashed var(--line);padding-top:12px}
.cmd{margin:4px 0;color:var(--text)} .str{color:var(--gold)}
.h{color:var(--glow-soft);margin:14px 0 4px} .hh{color:var(--text);font-weight:700}
.chip b{display:inline-block;min-width:13ch;color:var(--text)}
.chip.ok .mk{color:var(--ok)} .chip.bad .mk{color:var(--bad)} .chip.bad b{color:var(--bad)}
.chip .why{color:var(--muted)}
.bug{margin:8px 0 10px}
.bug ul{margin:2px 0 0;padding-left:2.2em} .bug li{color:var(--text);overflow-wrap:anywhere}
.bug li::marker{color:var(--bad)}
details summary{cursor:pointer;list-style:none} details summary::-webkit-details-marker{display:none}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12px;min-width:640px}
th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:400}
tr.wrong td{background:#2a0f17;color:#ffe4e8}
.score .ln{color:var(--text)} .score .flag{color:var(--gold)}
.cur{display:inline-block;width:.6em;height:1.1em;background:var(--glow-soft);vertical-align:text-bottom;animation:b 1.1s steps(1) infinite}
@keyframes b{50%{opacity:0}}
@media (prefers-reduced-motion:reduce){.cur{animation:none}}
.ds{list-style:none;padding:0;margin:4px 0 0 1.4em}
.ds li{margin:0 0 8px}
.err{border-left:2px solid var(--bad);padding:4px 10px;margin:12px 0;color:#ffd5dc}
.foot{color:var(--dim);font-size:12px;margin-top:14px}
.foot form{display:inline}
"""

JS = """
const LIB = JSON.parse(document.getElementById('lib').textContent);
const $ = s => document.querySelector(s);
const LIM = LIB.limits;
function counter(inp){
  let c = inp.parentElement.querySelector('.cnt');
  if (!c) { c = document.createElement('span'); c.className = 'cnt'; c.setAttribute('aria-live', 'off'); inp.after(c); }
  const max = +inp.maxLength, n = inp.value.length;
  c.textContent = n + '/' + max;
  c.classList.toggle('near', n >= max * 0.9 && n < max); c.classList.toggle('full', n >= max);
}
function values(list){ return [...document.querySelectorAll('input[name="'+list+'"]')].map(i => i.value.trim()).filter(Boolean); }
function listCount(list){
  const n = document.querySelectorAll('input[name="'+list+'"]').length;
  $('#n_'+list).textContent = 'cases ' + values(list).length + '/' + LIM.cases;
  document.querySelector('[data-add="'+list+'"]').disabled = n >= LIM.cases;
}
function wire(row){
  const inp = row.querySelector('input'), x = row.querySelector('.x'), list = inp.name;
  x.onclick = () => { row.remove(); if (!document.querySelector('input[name="'+list+'"]')) addRow(list, ''); markUsed(); listCount(list); };
}
function addRow(list, value){
  if (document.querySelectorAll('input[name="'+list+'"]').length >= LIM.cases) return null;
  const box = document.querySelector('.rows[data-list="'+list+'"]');
  const row = document.createElement('div'); row.className = 'row';
  const wrap = document.createElement('div'); wrap.className = 'inp'; wrap.style.flex = '1';
  const inp = document.createElement('input'); inp.type = 'text'; inp.name = list; inp.value = value || '';
  inp.maxLength = LIM.case; inp.setAttribute('aria-label', list === 'must_catch' ? 'Must catch case' : 'Must not catch case');
  const x = document.createElement('button'); x.type = 'button'; x.className = 'x'; x.textContent = '\\u00d7';
  x.setAttribute('aria-label', 'Remove this case');
  wrap.append(inp); row.append(wrap, x); box.append(row); wire(row); counter(inp); listCount(list); return inp;
}
function addValue(list, v){
  if (values(list).includes(v)) return;
  const empty = [...document.querySelectorAll('input[name="'+list+'"]')].find(i => !i.value.trim());
  if (empty) { empty.value = v; counter(empty); } else addRow(list, v);
  markUsed(); listCount(list);
}
function markUsed(){
  document.querySelectorAll('.pill[data-list]').forEach(p => p.classList.toggle('used', values(p.dataset.list).includes(p.dataset.v)));
}
function pills(el, list, items){
  el.textContent = '';
  items.forEach(v => { const b = document.createElement('button'); b.type = 'button'; b.className = 'pill';
    b.dataset.list = list; b.dataset.v = v; b.textContent = '+ ' + v; b.title = 'Enter to add';
    b.onclick = () => addValue(list, v); el.append(b); });
}
let guardHit = null;
function render(){
  const d = LIB.domains.find(x => x.id === $('#domain').value) || LIB.domains[0];
  $('#blurb').textContent = '# ' + d.blurb;
  const g = d.guarded;
  $('#guard').hidden = !g; $('#guard').textContent = g ? d.note : '';
  ['must_catch','must_not_catch'].forEach(k => {
    const box = $('#s_'+k); box.hidden = g || !d[k].length;
    pills(box.querySelector('.p'), k, g ? [] : d[k]);
  });
  const ev = $('#evasions'); ev.textContent = '';
  (g ? [] : d.evasions).forEach(t => { const li = document.createElement('div'); li.className = 'ln'; li.textContent = '  ~ ' + t; ev.append(li); });
  $('#evbox').hidden = g || !d.evasions.length;
  const ds = $('#datasets'); ds.textContent = '';
  d.datasets.forEach(x => {
    const li = document.createElement('li');
    const a = document.createElement('a'); a.href = x.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = x.name;
    const lic = document.createElement('span'); lic.className = 'gold'; lic.textContent = '  [' + x.licence + ']';
    const use = document.createElement('div'); use.className = 'dim'; use.textContent = '  ' + x.use;
    li.append(a, lic, use); ds.append(li);
  });
  $('#aibox').hidden = d.id !== 'custom';
  aiState(); markUsed();
}
function aiState(){
  const long = $('#description').value.length > LIM.ai_description;
  const blocked = guardHit || !LIB.ai.available || long;
  document.querySelectorAll('.aibtn').forEach(b => b.disabled = !!blocked);
  $('#ainote').textContent = '# ' + (guardHit ? 'Local AI is off for this description: it touches an area we never generate examples for. Use the datasets below.'
    : long ? 'Shorten "what does it do?" to ' + LIM.ai_description + ' characters to ask the local AI.' : LIB.ai.note);
}
let gt = null;
function checkGuard(){
  clearTimeout(gt);
  gt = setTimeout(async () => {
    const text = $('#description').value;  // the AI prompt only uses this field
    try { const r = await fetch('/guard', {method:'POST', headers:{'Content-Type':'application/json','X-HB-Token':LIB.token}, body: JSON.stringify({text})});
      guardHit = (await r.json()).blocked; } catch(e) { guardHit = 'unknown'; }
    aiState();
  }, 250);
}
async function askAI(kind){
  const out = $('#aiout'); out.textContent = '... thinking on your machine';
  document.querySelectorAll('.aibtn').forEach(b => b.disabled = true);
  try {
    const r = await fetch('/suggest_ai', {method:'POST', headers:{'Content-Type':'application/json','X-HB-Token':LIB.token},
      body: JSON.stringify({kind, domain: $('#domain').value, description: $('#description').value})});
    const j = await r.json();
    if (!r.ok) { out.textContent = '! ' + (j.error || 'Could not get suggestions.'); }
    else { out.textContent = ''; const p = document.createElement('div'); pills(p, kind, j.items); out.append(p);
      const n = document.createElement('div'); n.className = 'dim'; n.textContent = '# from ' + j.model + ' on this machine. Check each one before you add it.'; out.append(n); markUsed(); }
  } catch(e) { out.textContent = '! Local AI did not answer.'; }
  aiState();
}
// keyboard-first: Enter = next field (or a new row in a case list), Ctrl/Cmd+Enter = run
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $('#f').requestSubmit(); return; }
  const t = e.target;
  if (e.key !== 'Enter' || !t.matches('#f input[type=text], #f select')) return;
  e.preventDefault();
  if (t.closest('.rows')) {
    const rows = [...t.closest('.rows').querySelectorAll('input')];
    if (t === rows[rows.length - 1]) { if (t.value.trim()) { const n = addRow(t.name, ''); if (n) n.focus(); } return; }
    rows[rows.indexOf(t) + 1].focus(); return;
  }
  const order = [...document.querySelectorAll('#f input[type=text], #f select')];
  const next = order[order.indexOf(t) + 1]; if (next) next.focus();
});
document.addEventListener('input', e => { if (e.target.maxLength > 0) counter(e.target);
  if (e.target.closest && e.target.closest('.rows')) { markUsed(); listCount(e.target.name); } });
$('#domain').addEventListener('change', render);
$('#description').addEventListener('input', checkGuard);
document.querySelectorAll('[data-add]').forEach(b => b.onclick = () => { const n = addRow(b.dataset.add, ''); if (n) n.focus(); });
document.querySelectorAll('.rows .row').forEach(wire);
document.querySelectorAll('.aibtn').forEach(b => b.onclick = () => askAI(b.dataset.kind));
document.querySelectorAll('input[maxlength]').forEach(counter);
['must_catch','must_not_catch'].forEach(listCount);
$('#blank').onclick = () => { ['name','description','system'].forEach(i => { $('#'+i).value = ''; counter($('#'+i)); });
  document.querySelectorAll('.rows').forEach(b => { b.textContent = ''; addRow(b.dataset.list, ''); });
  $('#falsifier').value = LIB.default_falsifier; counter($('#falsifier')); $('#domain').value = 'custom'; render(); checkGuard(); $('#name').focus(); };
render(); checkGuard();
if (location.hash === '#results' && document.getElementById('results').firstElementChild) document.getElementById('results').scrollIntoView();
"""


def _rows(name, values):
    e = html.escape
    vals = list(values) + ([""] if len(values) < LIMITS["cases"] else [])  # one blank row so "add" works without JS
    label = "Must catch case" if name == "must_catch" else "Must not catch case"
    return "".join(f'<div class="row"><div class="inp" style="flex:1"><input type="text" name="{name}" maxlength="{LIMITS["case"]}" '
                   f'aria-label="{label}" value="{e(v, quote=True)}"></div>'
                   f'<button type="button" class="x" aria-label="Remove this case">&times;</button></div>' for v in vals)


def error_block(title, lines):
    e = html.escape
    return (f"<section class='out'><div class='err'><div class='bad'>&#10007; {e(title)}</div>"
            + "".join(f"<div class='ln'>! {e(x)}</div>" for x in lines) + "</div></section>")


def _field(fid, prompt, value, limit, placeholder="", hint=""):
    e = html.escape
    return (f'<div class="field"><label for="{fid}"><span class="pr">&#9656;</span>{e(prompt)}</label>'
            f'<div class="inp"><input type="text" id="{fid}" name="{fid}" maxlength="{limit}" value="{e(value, quote=True)}"'
            f' placeholder="{e(placeholder, quote=True)}" autocomplete="off" spellcheck="false"></div></div>'
            + (f'<div class="ln dim">{e(hint)}</div>' if hint else ""))


def page(task, report="", msg="", db="", share=False, ai=None, plain_text="", token=""):
    """task: dict with name, description, domain, system, must_catch, must_not_catch, falsifier."""
    e = html.escape
    ai = ai or dict(available=False, model=None, note="Local AI is off.")
    lib = dict(domains=suggest.for_ui(), ai=ai, default_falsifier=DEFAULT_FALSIFIER["text"], limits=LIMITS, token=token)
    lib_json = json.dumps(lib).replace("<", "\\u003c")
    opts = "".join(f"<option value='{e(d['id'])}'{' selected' if d['id'] == task.get('domain') else ''}>"
                   f"{e(d['label'])}</option>" for d in lib["domains"])
    csrf = f'<input type="hidden" name="csrf" value="{e(token, quote=True)}">'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Harness Builder</title>
<style>{CSS}</style></head><body><div class="wrap"><div class="term">
<div class="bar"><i></i><i></i><i></i><span class="t">enki &mdash; harness-builder &mdash; 127.0.0.1</span><span class="internal">localhost only</span></div>
<div class="body">
<div class="banner">enki labs // harness builder</div>
<div class="ln dim"># Find the bugs in a rule-based text filter. Say what it must catch and what it must leave alone;</div>
<div class="ln dim"># it adds innocent twins, an attacker, rule switch-offs and controls, then shows every message it gets wrong.</div>
<div class="ln dim"># A working example is filled in: press <kbd>Ctrl</kbd>+<kbd>Enter</kbd> or run.</div>
<form method="post" action="/run#results" id="f">{csrf}
<div class="sec"><span class="pr">enki &#9656;</span> <span class="q">what are you testing?</span></div>
{_field("name", "task name", task.get("name", ""), LIMITS["name"], "e.g. game-chat insult filter")}
{_field("description", "what does it do?", task.get("description", ""), LIMITS["description"], "e.g. blocks insults aimed at another player")}
<div class="field"><label for="domain"><span class="pr">&#9656;</span>domain</label><div class="inp"><select id="domain" name="domain">{opts}</select></div></div>
<div class="ln dim" id="blurb"></div>
{_field("system", "system under test", task.get("system", ""), LIMITS["system"], "examples/planted_filter.py",
        "# a .py file exposing RULES and classify(text, enabled_rules). It runs on this machine: only point at code you trust.")}
<div class="guardnote" id="guard" hidden></div>
<div class="sec"><span class="pr">enki &#9656;</span> <span class="q">must catch</span> <span class="dim" id="n_must_catch"></span></div>
<div class="rows" data-list="must_catch">{_rows('must_catch', task.get('must_catch', []))}</div>
<div class="ln"><button type="button" data-add="must_catch">+ add case</button></div>
<div class="sugg" id="s_must_catch"><div class="dim"># suggestions: Tab to one, Enter to add</div><div class="p"></div></div>
<div class="sec"><span class="pr">enki &#9656;</span> <span class="q">must NOT catch</span> <span class="dim" id="n_must_not_catch"></span></div>
<div class="rows" data-list="must_not_catch">{_rows('must_not_catch', task.get('must_not_catch', []))}</div>
<div class="ln"><button type="button" data-add="must_not_catch">+ add case</button></div>
<div class="sugg" id="s_must_not_catch"><div class="dim"># hard negatives (harmless look-alikes): Tab to one, Enter to add</div><div class="p"></div></div>
<div id="aibox" hidden><div class="sec"><span class="pr">enki &#9656;</span> <span class="q">suggest with local AI</span> <span class="dim">(optional, off until you press)</span></div>
<div class="ln dim" id="ainote"></div>
<div class="ln"><button type="button" class="aibtn" data-kind="must_catch">ideas to catch</button>
<button type="button" class="aibtn" data-kind="must_not_catch">ideas to leave alone</button></div>
<div id="aiout" class="sugg"></div></div>
<div class="sec"><span class="pr">enki &#9656;</span> <span class="q">when is it wrong?</span></div>
{_field("falsifier", "falsifier", task.get("falsifier", DEFAULT_FALSIFIER["text"]), LIMITS["falsifier"], "",
        "# saved with a timestamp and hash before anything runs, so the goalposts cannot move.")}
<div class="ln" style="margin-top:14px"><button class="run">&#9654; run harness</button> <button type="button" id="blank">start blank</button>
<span class="kbd"><kbd>&#9166;</kbd> next field / new case &middot; <kbd>Ctrl</kbd>+<kbd>&#9166;</kbd> run &middot; <kbd>Tab</kbd> move</span></div>
</form>
<div id="results">{report}</div>
<div id="evbox"><div class="sec"><span class="pr">enki &#9656;</span> <span class="q">evasions to try</span></div>
<div class="ln dim"># the attacker already tries some of these; add your own disguised versions to must catch</div><div id="evasions"></div></div>
<div class="sec"><span class="pr">enki &#9656;</span> <span class="q">find real test data</span></div>
<div class="ln dim"># public datasets checked 2026-10-21 (link, licence, contents). Read each licence before use.</div>
<ul class="ds" id="datasets"></ul>
<details><summary class="sec"><span class="pr">enki &#9656;</span> <span class="q">paste a plain-English task instead</span> <span class="dim">(advanced)</span></summary>
<form method="post" action="/run#results">{csrf}<textarea name="task" aria-label="Plain-English task">{e(plain_text)}</textarea>
<div class="ln"><button>&#9654; run from text</button></div></form></details>
<div class="foot"># learning: local only ({e(db)}). Shared learning: <b>OFF</b> (share_shapes={share}).
No network calls except Ollama on 127.0.0.1 when you press a local-AI button.
<form method="post" action="/export">{csrf}<button>export attack shapes</button></form> {e(msg)}</div>
</div></div></div><script type="application/json" id="lib">{lib_json}</script><script>{JS}</script></body></html>"""
