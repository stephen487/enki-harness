"""System-under-test adapter: a Python module exposing RULES (dict name->callable) and
classify(text, enabled_rules) -> bool."""
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class System:
    name: str
    rules: dict
    classify: Callable

    def run(self, text, enabled=None):
        return bool(self.classify(text, self.rule_names() if enabled is None else enabled))

    def rule_names(self):
        return list(self.rules)


def load_system(path) -> System:
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"sut_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not isinstance(getattr(mod, "RULES", None), dict) or not callable(getattr(mod, "classify", None)):
        raise ValueError(f"{path}: must expose RULES: dict and classify(text, enabled_rules)")
    return System(path.stem, mod.RULES, mod.classify)


def always_allow(rule_names) -> System:
    return System("CONTROL always-allow", {n: None for n in rule_names}, lambda t, e: False)


def always_block(rule_names) -> System:
    return System("CONTROL always-block", {n: None for n in rule_names}, lambda t, e: True)
