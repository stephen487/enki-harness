"""Suggestions: a curated domain library, verified public datasets, a safety guard, and an
OPTIONAL local-AI helper (Ollama on 127.0.0.1 only, off until the user clicks).

Guard (non-negotiable): no example content is ever listed or generated for sexual content,
anything involving minors, self-harm/suicide, or slurs/hate speech. For those, only pointers to
vetted datasets/resources are shown, and the local-AI helper refuses."""
import json
import re
import urllib.request
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA = "http://127.0.0.1:11434"
SMALL_MODELS = ["qwen2.5:3b", "llama3.2:3b", "qwen2.5:1.5b", "llama3.2:1b", "gemma2:2b", "phi3:mini"]
MAX_AI = 10

# Each guarded class: whole-word / phrase patterns. Word boundaries stop substring traps
# ("classic", "Sussex", "assessment", "Scunthorpe", "kidding", "minor typos").
GUARD = {
    "sexual_content": r"sex|sexual|sexually|sexy|sexting|porn|porno|pornography|pornographic|nsfw|nude|nudes|nudity|"
                      r"naked|erotic|erotica|xxx|fetish|rape|explicit content|adult content|onlyfans",
    "child_safety": r"child|children|childs|kid|kids|minors|underage|under[\s-]?age|under[\s-]?18s?|teen|teens|teenager|"
                    r"teenagers|preteen|preteens|juvenile|juveniles|schoolgirl|schoolboy|csam|grooming|"
                    r"paedophile|pedophile|paedo|pedo|loli",
    "self_harm": r"self[\s_-]?harm|self[\s_-]?injury|suicide|suicidal|kill myself|killing myself|end my life|"
                 r"cutting myself|overdose|eating disorder|anorexia|bulimia|pro[\s-]?ana",
    "hate_speech": r"slur|slurs|hate[\s_-]?speech|hateful|racist|racism|racial|homophobic|homophobia|transphobic|"
                   r"antisemitic|antisemitism|islamophobic|bigot|bigotry|bigoted|n[\s-]?word|ethnic",
}
GUARDED_IDS = set(GUARD)
_GUARD_RX = {k: re.compile(r"(?<![a-z0-9])(?:" + v + r")(?![a-z0-9])", re.I) for k, v in GUARD.items()}
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "@": "a", "$": "s", "7": "t"})

GUARD_NOTE = ("We never list or generate example content for this area. Collecting your own test data here can "
              "itself cause harm (and for anything involving minors, can be illegal). Use the vetted datasets and "
              "services below, under their own terms.")


def guard(text):
    """Return the guarded class the text touches, or None. Checks the raw text and a leet-folded copy."""
    t = (text or "").lower().replace("_", " ")
    for s in (t, t.translate(_LEET)):
        for k, rx in _GUARD_RX.items():
            if rx.search(s):
                return k
    return None


@lru_cache(maxsize=1)
def library():
    return json.loads((HERE / "domains.json").read_text(encoding="utf-8"))["domains"]


@lru_cache(maxsize=1)
def datasets(include_unverified=False):
    rows = json.loads((HERE / "datasets_verified.json").read_text(encoding="utf-8"))
    return [d for d in rows if include_unverified or d.get("verified") is True]


def domain(did):
    for d in library():
        if d["id"] == did:
            return d
    return None


def for_ui():
    """Everything the page needs, with guarded domains stripped to pointers only."""
    out = []
    for d in library():
        sets = [dict(name=x["name"], use=x["use"], licence=x["licence"], url=x["url"], checked_url=x["checked_url"])
                for x in datasets()
                if d["id"] in x["domains"] or (not d.get("guarded") and "general_hard_negatives" in x["domains"]
                                                and not set(x["domains"]) & GUARDED_IDS)]
        if d.get("guarded"):
            out.append(dict(id=d["id"], label=d["label"], blurb=d["blurb"], guarded=True, note=GUARD_NOTE,
                            must_catch=[], must_not_catch=[], evasions=[], datasets=sets))
        else:
            out.append(dict(id=d["id"], label=d["label"], blurb=d["blurb"], guarded=False,
                            must_catch=d.get("must_catch", []), must_not_catch=d.get("must_not_catch", []),
                            evasions=d.get("evasions", []), datasets=sets))
    return out


# ---- optional local AI ------------------------------------------------------------------------

def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url, body, timeout):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ai_status(host=OLLAMA):
    """Which small local model is installed? Lists tags only; never runs a model."""
    assert host.startswith(("http://127.0.0.1", "http://localhost")), "local Ollama only"
    try:
        names = [m["name"] for m in _get(host + "/api/tags", 1.5).get("models", [])]
    except Exception:
        return dict(available=False, model=None, note="Local AI is off: Ollama is not running on this machine.")
    for m in SMALL_MODELS:
        if m in names:
            return dict(available=True, model=m, note=f"Runs on your machine ({m} via Ollama). Nothing leaves it.")
    return dict(available=False, model=None,
                note="Local AI is off: no small model installed (try `ollama pull qwen2.5:3b`).")


class Blocked(Exception):
    pass


def suggest_ai(description, kind, host=OLLAMA, model=None, n=MAX_AI):
    """Ask a small LOCAL model for up to n example messages. kind: 'must_catch' or 'must_not_catch'."""
    assert host.startswith(("http://127.0.0.1", "http://localhost")), "local Ollama only"
    if kind not in ("must_catch", "must_not_catch"):
        raise ValueError("kind must be must_catch or must_not_catch")
    hit = guard(description)
    if hit:
        raise Blocked(hit)
    model = model or ai_status(host)["model"]
    if not model:
        raise RuntimeError("no small local model available")
    want = ("messages the filter SHOULD flag" if kind == "must_catch" else
            "tricky innocent messages the filter must NOT flag (look-alikes: quotes, reported speech, games, "
            "fiction, educational use, words that merely contain a trigger word)")
    prompt = (f"A developer is testing a text filter. What it does: {description.strip()[:400]}\n"
              f"List up to {n} short, mild, realistic example {want}. One sentence each. "
              'Reply ONLY as JSON: {"items": ["...", "..."]}')
    out = _post(host + "/api/generate", dict(model=model, prompt=prompt, stream=False, format="json", keep_alive=0,
                                             options=dict(num_predict=400, num_ctx=2048, temperature=0.7)), 120)
    try:
        items = json.loads(out.get("response", "{}")).get("items", [])
    except Exception:
        items = []
    clean, seen = [], set()
    for it in items:
        if not isinstance(it, str):
            continue
        it = it.strip().strip("\"'").strip()[:200]
        if it and it.lower() not in seen and not guard(it):   # output is guarded too
            seen.add(it.lower())
            clean.append(it)
    return dict(model=model, items=clean[:n])
