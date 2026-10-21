"""Example system under test: a kids'-chat insult filter (CORRECT version).

Adapter contract: RULES: dict[name, callable(text)->bool]; classify(text, enabled_rules)->bool.
Flag rules fire on normalised text; exception rules unflag a safe context.
"""
import re

LEET = str.maketrans({"1": "i", "0": "o", "3": "e", "4": "a", "$": "s", "@": "a"})


def normalise(text: str) -> str:
    t = text.lower().translate(LEET)
    t = re.sub(r"(?<=\w)[.\-_*](?=\w)", "", t)      # h.a.t.e -> hate
    t = re.sub(r"(\w)\1{2,}", r"\1", t)              # haaate -> hate
    return t


FLAG_RULES = {
    "hate_word": lambda t: re.search(r"\bhate\b", normalise(t)) is not None,
    "kill_word": lambda t: re.search(r"\bkill\b", normalise(t)) is not None,
    "insult_words": lambda t: re.search(r"\b(stupid|idiot|loser|dumb)\b", normalise(t)) is not None,
}
EXCEPTION_RULES = {
    "quote_exception": lambda t: '"' in t,                       # reported speech
    "game_exception": lambda t: re.search(r"\bgame\b", normalise(t)) is not None,
}
RULES = {**FLAG_RULES, **EXCEPTION_RULES}


def classify(text: str, enabled_rules) -> bool:
    enabled = set(enabled_rules)
    verdict = any(RULES[n](text) for n in FLAG_RULES if n in enabled)
    for n in EXCEPTION_RULES:
        if n in enabled and verdict and RULES[n](text):
            verdict = False
    return verdict
