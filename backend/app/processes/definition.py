"""Process definitions as validated data, and their Mermaid diagrams.

A definition is a pipeline of stages. Each stage has an owner role, the stages it depends
on (none: it can start at once, so stages without a dependency between them run in
parallel; otherwise it is locked until they are final) and a checklist.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Role = Literal["agent", "backoffice", "manager", "marketing"]
Kind = Literal["fill", "verify", "send", "do", "heading"]


class Text(BaseModel):
    en: str = Field(min_length=1, max_length=400)
    he: str = Field(min_length=1, max_length=400)


class CheckDef(BaseModel):
    text: Text
    kind: Kind = "do"
    verify_note: bool = False  # legal or tax statement to confirm against current rules


class StageDef(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    name: Text
    role: Role
    depends_on: list[str] = []
    four_eyes: bool = False  # the finalizer must not be anyone who ticked a check
    checks: list[CheckDef] = Field(min_length=1)

    @property
    def real_checks(self) -> list[tuple[int, CheckDef]]:
        """(index in the checklist, check) for the items that can be ticked (not headings)."""
        return [(i, c) for i, c in enumerate(self.checks) if c.kind != "heading"]


class ProcessDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    source_flow: str = ""
    owner_role: Role
    name: Text
    stages: list[StageDef] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_graph(self) -> "ProcessDefinition":
        ids = [s.id for s in self.stages]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate stage ids")
        known = set()
        for s in self.stages:  # dependencies must point to earlier stages: acyclic by construction
            for d in s.depends_on:
                if d == s.id:
                    raise ValueError(f"stage {s.id} depends on itself")
                if d not in ids:
                    raise ValueError(f"stage {s.id} depends on unknown stage {d}")
                if d not in known:
                    raise ValueError(f"stage {s.id} depends on later stage {d} (cycle risk)")
            known.add(s.id)
            if not s.real_checks:
                raise ValueError(f"stage {s.id} has no tickable checks")
        return self

    def stage(self, key: str) -> StageDef:
        for s in self.stages:
            if s.id == key:
                return s
        raise KeyError(key)


# ---- Mermaid ------------------------------------------------------------------------
_CLASS = {"agent": "agent", "backoffice": "back", "manager": "mgr", "marketing": "mkt"}
_ROLE_NAME = {
    "agent": ("Agent", "סוכן"),
    "backoffice": ("Back-office", "משרד אחורי"),
    "manager": ("Manager", "מנהל"),
    "marketing": ("Marketing", "שיווק"),
}


def _mq(s: str) -> str:
    return re.sub(r"[<>]", "", s.replace('"', "#quot;"))


def mermaid(d: ProcessDefinition, lang: Literal["en", "he"] = "en") -> str:
    def t(x: Text) -> str:
        return x.he if lang == "he" else x.en

    role_label = "תפקיד: " if lang == "he" else "Role: "
    o = [
        '%%{init: {"flowchart": {"wrappingWidth": 380}}}%%',
        "flowchart LR",
        "  classDef agent fill:#dbe8fd,stroke:#1f6feb,color:#111",
        "  classDef back fill:#d9f2e4,stroke:#2a9d63,color:#111",
        "  classDef mgr fill:#fbefd5,stroke:#b7791f,color:#111",
        "  classDef mkt fill:#ece1fb,stroke:#8250df,color:#111",
        "  classDef end1 fill:#fff,stroke:#333,color:#111",
        f'  start(["{_mq(t(d.name))}"]):::end1',
    ]
    for i, s in enumerate(d.stages):
        role = _ROLE_NAME[s.role][1 if lang == "he" else 0]
        label = f"<b>{i + 1}. {_mq(t(s.name))}</b><br/>{_mq(role_label + role)}"
        for c in s.checks:
            label += (
                f"<br/><u>{_mq(t(c.text))}</u>"
                if c.kind == "heading"
                else (f"<br/>☐ {_mq(t(c.text))}")
            )
        o.append(f'  s_{s.id}["{label}"]:::{_CLASS[s.role]}')
    has_out: set[str] = set()
    for s in d.stages:
        if not s.depends_on:
            o.append(f"  start --> s_{s.id}")
        for dep in s.depends_on:
            has_out.add(dep)
            o.append(f"  s_{dep} --> s_{s.id}")
    done = "הושלם: כל השלבים סגורים" if lang == "he" else "Complete: every stage final"
    o.append(f'  fin(["{done}"]):::end1')
    for s in d.stages:
        if s.id not in has_out:
            o.append(f"  s_{s.id} --> fin")
    return "\n".join(o)
