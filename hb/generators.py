"""Case generators. Template/rule-based by default (no model needed).
An LLM generator can be plugged in behind the same interface (see OllamaAttacker, UNTESTED)."""
import re

# Benign words used to build lexical-contrast twins. Curated: none is an insult or threat.
BENIGN_LEXICON = """
whatever chateau gate hat skill skilled kiln kilt bill fill hill mill will
closer lower poser loose lose close idiom idioms cupid stupor numb dumbbell dump dumpling
glass class pass grass compass assess assist bass mass basket shell spell smell hello
cheat heat heater chat that what hatch match thump plumb crumb thumb jumbo humble
""".split()

WORD = re.compile(r"[A-Za-z']+")


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def find_triggers(text, system):
    """Black-box: which word(s) make the system fire? Fallback: the longest word."""
    words = WORD.findall(text)
    alone = [w for w in words if system.run(w)]
    if alone:
        return alone
    if system.run(text):
        flips = [w for w in words if not system.run(re.sub(rf"\b{re.escape(w)}\b", "", text, count=1))]
        if flips:
            return flips
    return [max(words, key=len)] if words else []


class TemplateTwins:
    """For each must-catch case, near-identical innocent twins: the trigger word is replaced by
    (1) 'embed': a benign word that CONTAINS it (Scunthorpe), (2) 'neighbour': the closest benign word."""
    author = "template-twins-v1"
    strategies = ["embed", "neighbour"]

    def generate(self, text, system):
        out = []
        for trig in find_triggers(text, system)[:1]:
            t = trig.lower()
            embeds = [w for w in BENIGN_LEXICON if t in w and w != t]
            near = sorted((w for w in BENIGN_LEXICON if w != t and t not in w and edit_distance(t, w) <= 2),
                          key=lambda w: (edit_distance(t, w), BENIGN_LEXICON.index(w)))
            for strat, cands in (("embed", embeds[:2]), ("neighbour", near[:1])):
                for w in cands:
                    twin = re.sub(rf"\b{re.escape(trig)}\b", w, text, count=1)
                    if twin != text:
                        out.append((strat, twin))
        return out


LEET = str.maketrans({"a": "4", "e": "3", "i": "1", "o": "0", "s": "$"})


def _stretch(w):
    m = re.search(r"[aeiou]", w)
    return w if len(w) < 3 or not m else w[:m.start()] + m.group() * 3 + w[m.end():]


class TemplateAttacker:
    """Independent attacker slice: evasion transforms of must-catch cases. It does not look at the
    rules or the twins. Budget = how many strategies per case; the order comes from learned weights."""
    author = "template-attacker-v1"
    default_order = ["upper", "pad", "leet", "dotted", "stretch"]
    transforms = {
        "upper": lambda t: t.upper(),
        "pad": lambda t: f"lol {t} !!!",
        "leet": lambda t: t.lower().translate(LEET),
        "dotted": lambda t: WORD.sub(lambda m: ".".join(m.group()) if len(m.group()) >= 3 else m.group(), t),
        "stretch": lambda t: WORD.sub(lambda m: _stretch(m.group()), t),
    }

    def __init__(self, budget=3):
        self.budget = budget

    def choose(self, weights=None):
        weights = weights or {}
        order = sorted(self.default_order, key=lambda s: (-weights.get(s, 0.5), self.default_order.index(s)))
        return order[: self.budget]

    def generate(self, text, strategies):
        return [(s, self.transforms[s](text)) for s in strategies]


class OllamaAttacker:  # pragma: no cover  -- UNTESTED optional plug-in, local Ollama only
    """Optional LLM attacker. Talks ONLY to a local Ollama on 127.0.0.1. Not used by default."""
    author = "ollama-local"

    def __init__(self, model="qwen2.5:0.5b", url="http://127.0.0.1:11434/api/generate", budget=1):
        assert url.startswith("http://127.0.0.1"), "local Ollama only"
        self.model, self.url, self.budget = model, url, budget
        self.default_order = ["llm_rewrite"]

    def choose(self, weights=None):
        return ["llm_rewrite"]

    def generate(self, text, strategies):
        import json
        import urllib.request
        body = json.dumps({"model": self.model, "stream": False,
                           "prompt": f"Rewrite so a keyword filter might miss it, same meaning, one line only:\n{text}"}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return [("llm_rewrite", json.loads(r.read())["response"].strip().splitlines()[0])]
