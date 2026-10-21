import json

from hb.learning import Store, export_shapes, load_settings


def test_learning_moves_attacker_toward_bug_finding_strategies(task, go, tmp_path):
    store = Store(tmp_path / "l.db")
    r1 = go(task, "planted_filter.py", store=store)
    r2 = go(task, "planted_filter.py", store=store)
    assert r1["attacker_strategies"] == ["upper", "pad", "leet"]
    assert r2["attacker_strategies"][0] == "leet"            # it found the most bugs
    assert set(r2["attacker_strategies"]) != set(r1["attacker_strategies"])


def test_heldout_rows_never_recorded(task, go, tmp_path):
    store = Store(tmp_path / "l.db")
    r = go(task, "planted_filter.py", store=store)
    held_att = [x for x in r["rows"] if x["arm"] == "attacker" and x["heldout"]]
    assert held_att                                          # there IS a held-out slice
    att_rows = [x for x in r["rows"] if x["arm"] == "attacker"]
    recorded = sum(s["tries"] for s in store.stats() if s["arm"] == "attacker")
    assert recorded == len(att_rows) - len(held_att)


def test_learning_helped_only_reported_from_heldout(task, go, tmp_path):
    store = Store(tmp_path / "l.db")
    r1 = go(task, "planted_filter.py", store=store)
    assert r1["learning"]["helped"] is False                 # no history yet
    r2 = go(task, "planted_filter.py", store=store)
    lr = r2["learning"]
    assert lr["heldout_cases"] >= 1
    assert lr["helped"] == (lr["learned_found"] > lr["default_found"])


def test_shared_off_by_default_and_export_has_no_user_data(task, go, tmp_path):
    assert load_settings(tmp_path / "missing.json")["share_shapes"] is False
    store = Store(tmp_path / "l.db")
    go(task, "planted_filter.py", store=store)
    out = tmp_path / "shapes.json"
    export_shapes(store, out)
    blob = out.read_text()
    for secret in task.must_catch + task.must_not_catch + ["hate_word", "loser_caps", "quote_exception"]:
        assert secret not in blob
    assert json.loads(blob)["shapes"]
