"""Example system under test with FOUR PLANTED DEFECTS (acceptance test fixture).

(a) hate_word uses a substring match -> over-fires on innocent twins ("whatever", "chateau").
(b) loser_caps is dead: an uppercase pattern run on lowercased text never matches.
(c) quote_exception and game_exception each TOGGLE the verdict instead of clearing it:
    each alone looks correct; together they cancel and the text is flagged again.
(d) insult_words is missing "dumb" -> a must-catch case is missed.
Also: no leet/stretch normalisation, which the attacker arm should find.
"""
import re


def low(t: str) -> str:
    return t.lower()


FLAG_RULES = {
    "hate_word": lambda t: "hate" in low(t),                                          # (a)
    "kill_word": lambda t: re.search(r"\bkill\b", low(t)) is not None,
    "insult_words": lambda t: re.search(r"\b(stupid|idiot|loser)\b", low(t)) is not None,  # (d)
    "loser_caps": lambda t: re.search(r"\bLOSER\b", low(t)) is not None,              # (b)
}
EXCEPTION_RULES = {
    "quote_exception": lambda t: '"' in t,
    "game_exception": lambda t: re.search(r"\bgame\b", low(t)) is not None,
}
RULES = {**FLAG_RULES, **EXCEPTION_RULES}


def classify(text: str, enabled_rules) -> bool:
    enabled = set(enabled_rules)
    hit = any(RULES[n](text) for n in FLAG_RULES if n in enabled)
    verdict = hit
    for n in EXCEPTION_RULES:
        if n in enabled and hit and RULES[n](text):
            verdict = not verdict                                                     # (c)
    return verdict
