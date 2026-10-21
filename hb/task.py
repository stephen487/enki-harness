"""A task: what must be caught, what must not, which system, and the pre-registered falsifier."""
import json
import re
from dataclasses import dataclass, field

DEFAULT_FALSIFIER = {
    "text": "The system is wrong if it misses ANY must-catch case (task or attacker) "
            "or flags ANY must-not-catch case or twin.",
    "max_missed": 0,
    "max_false_flags": 0,
}


LIMITS = {"name": 60, "description": 280, "system": 260, "case": 500, "cases": 50, "falsifier": 300, "ai_description": 200}
LABELS = {"name": "Task name", "description": "What does it do?", "system": "System under test",
          "falsifier": "Falsifier", "must_catch": "Must catch", "must_not_catch": "Must NOT catch"}


def limit_errors(name, description, system, falsifier_text, must_catch, must_not_catch):
    """Over-limit input is REJECTED, never truncated (a truncated case changes what is measured)."""
    errs = []
    for key, val in (("name", name), ("description", description), ("system", system), ("falsifier", falsifier_text)):
        if len(val or "") > LIMITS[key]:
            errs.append(f"{LABELS[key]} is {len(val)} characters; the limit is {LIMITS[key]}.")
    for key, rows in (("must_catch", must_catch), ("must_not_catch", must_not_catch)):
        if len(rows) > LIMITS["cases"]:
            errs.append(f"{LABELS[key]} has {len(rows)} cases; the limit is {LIMITS['cases']}.")
        for i, r in enumerate(rows, 1):
            if len(r) > LIMITS["case"]:
                errs.append(f"{LABELS[key]} case {i} is {len(r)} characters; the limit is {LIMITS['case']}.")
    return errs


@dataclass
class Task:
    name: str
    must_catch: list
    must_not_catch: list
    system: str = ""
    description: str = ""
    author: str = "user"
    falsifier: dict = field(default_factory=lambda: dict(DEFAULT_FALSIFIER))

    @classmethod
    def from_dict(cls, d):
        f = dict(DEFAULT_FALSIFIER)
        f.update(d.get("falsifier") or {})
        return cls(name=d.get("name", "task"), must_catch=list(d.get("must_catch", [])),
                   must_not_catch=list(d.get("must_not_catch", [])), system=d.get("system", ""),
                   description=d.get("description", ""), author=d.get("author", "user"), falsifier=f)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    @classmethod
    def from_form(cls, form):
        """The structured web form (parse_qs output: field -> list of values) -> the SAME Task the
        JSON/CLI path builds. Blank list rows are dropped; the falsifier text is editable, its limits stay 0."""
        one = lambda k, default="": ((form.get(k) or [default])[0] or default).strip()  # noqa: E731
        rows = lambda k: [x.strip() for x in form.get(k, []) if x and x.strip()]  # noqa: E731
        d = {"name": one("name", "task"), "description": one("description"), "system": one("system"),
             "author": "user", "must_catch": rows("must_catch"), "must_not_catch": rows("must_not_catch")}
        if one("falsifier"):
            d["falsifier"] = {"text": one("falsifier")}
        return cls.from_dict(d)

    def limit_errors(self):
        return limit_errors(self.name, self.description, self.system, self.falsifier.get("text", ""),
                            self.must_catch, self.must_not_catch)

    def to_dict(self):
        return dict(self.__dict__)


_HEADS = [
    (re.compile(r"^\s*(must[\s-]*not[\s-]*catch|do\s*not\s*catch|don'?t\s*catch|allow|must not flag)\s*:\s*(.*)$", re.I), "must_not_catch"),
    (re.compile(r"^\s*(must[\s-]*catch|catch|flag|block)\s*:\s*(.*)$", re.I), "must_catch"),
    (re.compile(r"^\s*(system|filter|module)\s*:\s*(.*)$", re.I), "system"),
    (re.compile(r"^\s*(falsifier|wrong if)\s*:\s*(.*)$", re.I), "falsifier"),
    (re.compile(r"^\s*(name|task)\s*:\s*(.*)$", re.I), "name"),
]


def parse_plain(text: str) -> Task:
    """Plain-English form. Lines like 'Catch: ...', "Don't catch: ...", 'System: path',
    'Falsifier: ...'. Lines under a heading with no text after the colon belong to it.
    Any other line is kept as the description."""
    d = {"must_catch": [], "must_not_catch": [], "description": ""}
    current = None
    desc = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        for rx, key in _HEADS:
            m = rx.match(line)
            if m:
                current, rest = key, m.group(2).strip()
                break
        else:
            rest = None
        if rest is None:
            item = line.lstrip("-* ").strip()
            if current in ("must_catch", "must_not_catch"):
                d[current].append(item)
            else:
                desc.append(line)
            continue
        if current in ("must_catch", "must_not_catch"):
            if rest:
                d[current].append(rest)
        elif current == "falsifier":
            if rest:
                d["falsifier"] = {"text": rest}
        elif rest:
            d[current] = rest
    d["description"] = " ".join(desc)
    return Task.from_dict(d)
