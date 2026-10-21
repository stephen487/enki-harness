"""Acceptance: the harness finds all four planted defects, and reports none on the correct filter."""


def kinds(r, kind):
    return [d for d in r["defects"] if d["kind"] == kind]


# ---- planted filter: all four defects found -------------------------------------------------
def test_a_overfire_on_twin_found_and_attributed(task, go):
    r = go(task, "planted_filter.py")
    tw = kinds(r, "twin_false_flag")
    assert tw and all(d["responsible"] == ["hate_word"] for d in tw)
    assert any("whatever" in d["text"] for d in tw)


def test_b_dead_rule_found(task, go):
    r = go(task, "planted_filter.py")
    assert r["dead_rules"] == ["loser_caps"]


def test_c_cancelling_pair_found_as_interaction(task, go):
    r = go(task, "planted_filter.py")
    inter = kinds(r, "interaction")
    assert [d["rules"] for d in inter] == [["quote_exception", "game_exception"]]
    # it is also visible as a plain task false flag on that row
    assert any("kill the boss in the game" in d["text"] for d in kinds(r, "task_false_flag"))


def test_d_missed_must_catch_found(task, go):
    r = go(task, "planted_filter.py")
    assert [d["text"] for d in kinds(r, "task_miss")] == ["you are so dumb"]


def test_attacker_finds_unnormalised_evasions(task, go):
    r = go(task, "planted_filter.py")
    assert any(d["strategy"] == "leet" for d in kinds(r, "attacker_miss"))


def test_planted_is_falsified(task, go):
    r = go(task, "planted_filter.py")
    assert r["falsified"] is True


# ---- correct filter: no false defect reports --------------------------------------------------
def test_correct_filter_has_zero_defects(task, go):
    r = go(task, "correct_filter.py")
    assert r["defects"] == []
    assert r["dead_rules"] == [] and r["interactions"] == []
    assert r["falsified"] is False


def test_correct_filter_redundant_pair_is_info_not_defect(task, go):
    r = go(task, "correct_filter.py")
    assert [e["rules"] for e in r["redundant_pairs"]] == [["quote_exception", "game_exception"]]


def test_correct_filter_all_gates_green_and_score_shown(task, go):
    r = go(task, "correct_filter.py")
    assert r["all_gates_green"], r["gates"]
    assert r["score"] is not None


def test_controls_fail_as_required(task, go):
    r = go(task, "correct_filter.py")
    c = r["controls"]
    assert set(c) == {"CONTROL always-allow", "CONTROL always-block"}
    assert all(v["wrong"] > 0 and not v["same_as_system"] for v in c.values())
