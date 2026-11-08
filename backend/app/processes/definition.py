"""Process definitions as validated data, and their Mermaid diagrams.

A definition is a pipeline of stages. Each stage has an owner role, the stages it depends
on (none: it can start at once, so stages without a dependency between them run in
parallel; otherwise it is locked until they are final) and a checklist.
"""

import re
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, ValidationError, model_validator

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


class Problem(TypedDict):
    """One thing wrong with a draft, with its place so a screen can mark the stage or edge."""

    code: str
    message: str
    stage: str | None
    check: int | None
    edge: list[str] | None


def _problem(
    code: str,
    message: str,
    stage: str | None = None,
    check: int | None = None,
    edge: list[str] | None = None,
) -> Problem:
    return {"code": code, "message": message, "stage": stage, "check": check, "edge": edge}


def graph_problems(stages: list[StageDef]) -> list[Problem]:
    """Every rule about how stages relate. The one place these rules live."""
    out: list[Problem] = []
    ids = [s.id for s in stages]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        out.append(_problem("duplicate_stage_id", f"duplicate stage ids: {dup}", dup))
    known: set[str] = set()
    for s in stages:  # dependencies must point to earlier stages: acyclic by construction
        seen: set[str] = set()
        for d in s.depends_on:
            edge = [d, s.id]
            if d in seen:
                out.append(
                    _problem("duplicate_edge", f"stage {s.id} lists {d} twice", s.id, edge=edge)
                )
            seen.add(d)
            if d == s.id:
                out.append(
                    _problem("self_dependency", f"stage {s.id} depends on itself", s.id, edge=edge)
                )
            elif d not in ids:
                out.append(
                    _problem(
                        "unknown_stage",
                        f"stage {s.id} depends on unknown stage {d}",
                        s.id,
                        edge=edge,
                    )
                )
            elif d not in known:
                out.append(
                    _problem(
                        "later_stage",
                        f"stage {s.id} depends on later stage {d} (cycle risk)",
                        s.id,
                        edge=edge,
                    )
                )
        known.add(s.id)
        if not s.real_checks:
            out.append(_problem("no_tickable_check", f"stage {s.id} has no tickable checks", s.id))
    return out


class _Head(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    source_flow: str = ""
    owner_role: Role
    name: Text


def validate_document(doc: Any) -> list[Problem]:
    """All problems of a draft definition (empty: it can be published).

    Publishing and the editor's dry run both call this, so they cannot disagree.
    """
    if not isinstance(doc, dict):
        return [_problem("not_an_object", "the definition must be an object")]
    out: list[Problem] = []
    try:
        _Head.model_validate({k: v for k, v in doc.items() if k != "stages"})
    except ValidationError as e:
        out += [
            _problem("invalid", f"{'.'.join(map(str, x['loc']))}: {x['msg']}") for x in e.errors()
        ]
    raw = doc.get("stages")
    parsed: list[StageDef] = []
    if not isinstance(raw, list) or not raw:
        out.append(_problem("no_stages", "stages: List should have at least 1 item"))
        raw = []
    for n, item in enumerate(raw):
        try:
            parsed.append(StageDef.model_validate(item))
        except ValidationError as e:
            sid = (
                item.get("id")
                if isinstance(item, dict) and isinstance(item.get("id"), str)
                else f"#{n + 1}"
            )
            for x in e.errors():
                loc = list(x["loc"])
                chk = (
                    loc[1]
                    if len(loc) > 1 and loc[0] == "checks" and isinstance(loc[1], int)
                    else None
                )
                out.append(
                    _problem(
                        "invalid", f"stage {sid} {'.'.join(map(str, loc))}: {x['msg']}", sid, chk
                    )
                )
    if not out:
        out += graph_problems(parsed)
    if not out:  # safety net: the model itself must agree with the verdict
        try:
            ProcessDefinition.model_validate(doc)
        except ValidationError as e:  # pragma: no cover - would be a bug in the rules above
            out += [_problem("invalid", x["msg"]) for x in e.errors()]
    return out


class ProcessDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    source_flow: str = ""
    owner_role: Role
    name: Text
    stages: list[StageDef] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_graph(self) -> "ProcessDefinition":
        found = graph_problems(self.stages)
        if found:
            raise ValueError(found[0]["message"])
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
