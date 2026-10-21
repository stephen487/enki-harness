"""Form -> task equivalence, the curated library, the safety guard (+ its twins), dataset entries,
and the local-AI helper (Ollama mocked: no model is run by the tests)."""
import json
import urllib.parse

import pytest

from hb import suggest
from hb.task import DEFAULT_FALSIFIER, Task

from conftest import ROOT

GUARDED = {"sexual_content", "child_safety", "self_harm", "hate_speech"}


# ---- form -> task ---------------------------------------------------------------------------

def _browser_post(fields):
    """Encode like a browser form submit (repeated keys for list rows), decode like the server."""
    return urllib.parse.parse_qs(urllib.parse.urlencode(fields, doseq=True))


def test_form_produces_the_same_task_as_the_json_for_the_planted_example():
    want = Task.load(ROOT / "examples" / "insult_task.json")
    form = _browser_post({
        "name": want.name, "description": want.description, "domain": "insults", "system": want.system,
        "must_catch": want.must_catch + ["", "  "],          # blank rows from "+ Add" are dropped
        "must_not_catch": want.must_not_catch + [""],
        "falsifier": want.falsifier["text"],
    })
    assert Task.from_form(form).to_dict() == want.to_dict()


def test_form_defaults_falsifier_and_ignores_author_field():
    t = Task.from_form(_browser_post({"name": "x", "must_catch": ["a"], "must_not_catch": ["b"], "author": "attacker"}))
    assert t.falsifier == DEFAULT_FALSIFIER and t.author == "user"


# ---- library ---------------------------------------------------------------------------------

def test_library_has_the_requested_domains():
    ids = {d["id"] for d in suggest.library()}
    assert {"insults", "profanity", "pii", "spam", "prompt_injection", "off_topic", "custom"} <= ids
    for d in suggest.library():
        if not d.get("guarded") and d["id"] != "custom":
            assert d["must_catch"] and len(d["must_not_catch"]) >= 4 and d["evasions"], d["id"]


def test_guarded_domains_carry_no_example_content():
    for d in suggest.library():
        if d["id"] in GUARDED:
            assert d.get("guarded") is True
            assert not d.get("must_catch") and not d.get("must_not_catch") and not d.get("evasions"), d["id"]
    for d in suggest.for_ui():
        if d["guarded"]:
            assert d["must_catch"] == d["must_not_catch"] == d["evasions"] == [] and d["note"] and d["datasets"]


def test_no_library_example_touches_a_guarded_class():
    for d in suggest.library():
        for text in d.get("must_catch", []) + d.get("must_not_catch", []) + d.get("evasions", []):
            assert suggest.guard(text) is None, (d["id"], text, suggest.guard(text))


def test_hard_negatives_include_substring_traps():
    neg = " ".join(x for d in suggest.library() for x in d.get("must_not_catch", []))
    assert "Scunthorpe" in neg and "classic" in neg and 'said "' in neg


# ---- guard + twins ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,cls", [
    ("filter sexual messages", "sexual_content"), ("NSFW image captions", "sexual_content"),
    ("s3x chat detector", "sexual_content"),
    ("grooming of children in game chat", "child_safety"), ("an under-18 chat room", "child_safety"),
    ("self-harm posts", "self_harm"), ("suicide notes", "self_harm"),
    ("racist slurs", "hate_speech"), ("hate speech in comments", "hate_speech"),
])
def test_guard_blocks_matching_custom_descriptions(text, cls):
    assert suggest.guard(text) == cls


@pytest.mark.parametrize("text", [
    "classic games", "Sussex council spam filter", "Middlesex and Essex weather bot", "class assessment answers",
    "Scunthorpe United fan forum", "catch minor typos", "I was only kidding around", "book a therapist",
    "the analyst said hello", "a shell script linter",
])
def test_guard_twins_are_not_blocked(text):
    assert suggest.guard(text) is None


def test_ai_refuses_a_guarded_description_without_calling_the_model(monkeypatch):
    calls = []
    monkeypatch.setattr(suggest, "_post", lambda *a, **k: calls.append(a))
    with pytest.raises(suggest.Blocked):
        suggest.suggest_ai("detect sexual content", "must_catch", model="qwen2.5:3b")
    assert calls == []


# ---- datasets --------------------------------------------------------------------------------

def test_every_dataset_entry_has_url_licence_and_verified_flag():
    rows = suggest.datasets(include_unverified=True)
    assert rows
    for d in rows:
        assert d["url"].startswith("https://") and d["licence"] and isinstance(d["verified"], bool), d["id"]
        assert d["checked_url"].startswith("https://") and d["checked_on"], d["id"]


def test_unverified_datasets_are_never_shown():
    shown = {x["url"] for d in suggest.for_ui() for x in d["datasets"]}
    unverified = [d for d in suggest.datasets(include_unverified=True) if not d["verified"]]
    assert unverified, "the register lists jigsaw/olid as unverified"
    assert not shown & {d["url"] for d in unverified}


def test_every_domain_points_to_at_least_one_dataset():
    for d in suggest.for_ui():
        assert d["datasets"], d["id"]


# ---- local AI (mocked) -----------------------------------------------------------------------

def test_ai_status_picks_a_small_model_and_only_local(monkeypatch):
    monkeypatch.setattr(suggest, "_get", lambda url, t: {"models": [{"name": "qwen2.5:32b"}, {"name": "qwen2.5:3b"}]})
    assert suggest.ai_status()["model"] == "qwen2.5:3b"
    monkeypatch.setattr(suggest, "_get", lambda url, t: {"models": [{"name": "qwen2.5:32b"}]})
    assert suggest.ai_status()["available"] is False
    with pytest.raises(AssertionError):
        suggest.ai_status("https://api.example.com")


def test_ai_output_is_capped_deduped_and_guarded(monkeypatch):
    items = ['"example 0"'] + [f"example {i}" for i in range(15)] + ["example 1", "a sexy one", 7]
    monkeypatch.setattr(suggest, "_post", lambda url, body, t: {"response": json.dumps({"items": items})})
    out = suggest.suggest_ai("a cooking bot that must stay on topic", "must_catch", model="qwen2.5:3b")
    assert len(out["items"]) == 10 and len(set(out["items"])) == 10 and out["items"][0] == "example 0"
    assert not any(suggest.guard(x) for x in out["items"])
