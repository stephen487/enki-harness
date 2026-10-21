from hb.task import parse_plain


def test_plain_english_parse():
    t = parse_plain("""Name: insults
Catch insults aimed at a player.
Catch: I hate you
Must catch:
- you are so dumb
Don't catch: good game
System: examples/correct_filter.py
Falsifier: wrong if anything is missed
""")
    assert t.name == "insults" and t.must_catch == ["I hate you", "you are so dumb"]
    assert t.must_not_catch == ["good game"] and t.system == "examples/correct_filter.py"
    assert t.falsifier["text"] == "wrong if anything is missed" and t.falsifier["max_missed"] == 0
    assert "aimed at a player" in t.description
