"""Named checks inside the harness. Each can be switched off (HB_DISABLE=a,b) so the
harness's own tests can prove every check is load-bearing (mutation of the harness)."""
import os

CHECKS = [
    # detectors (arms / defect finders)
    "task_miss", "task_false_flag", "twin_arm", "twin_false_flag", "attacker_arm",
    "attacker_miss", "dead_rule", "interaction", "control_arm", "falsifier",
    # gates
    "gate_prereg", "gate_arms", "gate_control", "gate_independence",
    "instrument_flag", "score_withhold", "heldout_guard",
]


def disabled(name: str, extra=frozenset()) -> bool:
    env = {x.strip() for x in os.environ.get("HB_DISABLE", "").split(",") if x.strip()}
    return name in env or name in extra
