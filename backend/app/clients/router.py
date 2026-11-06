import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as Db

from app.audit import chain
from app.deps import Principal, get_db, require
from app.ids import is_valid_israeli_id, normalize_id_number
from app.models import Client, FamilyLink, Insurer, Policy
from app.text import build_search, normalize, skeleton, to_hebrew, to_latin

router = APIRouter(prefix="/api")
LANGS = {"he", "ar", "ru", "fr", "en"}


class ClientIn(BaseModel):
    id_number: str
    name: str = Field(min_length=2, max_length=200)
    preferred_language: str = "he"
    phone: str | None = None
    email: str | None = None
    agent_id: uuid.UUID | None = None


class PolicyIn(BaseModel):
    insurer: str
    policy_number: str
    product: str
    premium_ils: float = Field(ge=0)
    renewal_date: str | None = None


class FamilyIn(BaseModel):
    member_id: uuid.UUID
    relation: str


def _visible(p: Principal) -> Any:
    """Agents see their own clients; roles with client.read_all see every client."""
    if "client.read_all" in p.capabilities:
        return True
    return Client.agent_id == p.user.id


def _client_dict(c: Client) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "id_number": c.id_number,
        "name": c.name,
        "name_translit_he": c.name_translit_he,
        "name_translit_lat": c.name_translit_lat,
        "preferred_language": c.preferred_language,
        "phone": c.phone,
        "email": c.email,
        "agent_id": str(c.agent_id) if c.agent_id else None,
        "status": c.status,
    }


def _get_visible(db: Db, p: Principal, client_id: uuid.UUID) -> Client:
    c = db.scalar(select(Client).where(Client.id == client_id, _visible(p)))
    if c is None:  # not found and not allowed look the same
        raise HTTPException(404, "not_found")
    return c


@router.post("/clients", status_code=201)
def create_client(
    body: ClientIn, p: Principal = Depends(require("client.write")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    if not is_valid_israeli_id(body.id_number):
        raise HTTPException(422, "invalid_id_number")
    if body.preferred_language not in LANGS:
        raise HTTPException(422, "invalid_language")
    nid = normalize_id_number(body.id_number) or ""
    agent = body.agent_id if "client.read_all" in p.capabilities else p.user.id
    plain, skel = build_search(body.name, nid)
    c = Client(
        id_number=nid,
        name=body.name,
        name_translit_he=to_hebrew(body.name),
        name_translit_lat=to_latin(body.name),
        search_text=plain,
        search_skel=skel,
        preferred_language=body.preferred_language,
        phone=body.phone,
        email=body.email,
        agent_id=agent or p.user.id,
    )
    db.add(c)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "duplicate_id_number") from e
    chain.append(db, str(p.user.id), "client.create", f"client:{c.id}", {})
    return _client_dict(c)


@router.get("/clients")
def search_clients(
    q: str = "", p: Principal = Depends(require("client.read")), db: Db = Depends(get_db)
) -> list[dict[str, Any]]:
    stmt = select(Client).where(_visible(p))
    for tok in normalize(q).split():
        conds = [Client.search_text.ilike(f"%{tok}%")]
        sk = skeleton(tok)
        if len(sk) >= 2:
            conds.append(Client.search_skel.ilike(f"%{sk}%"))
        stmt = stmt.where(or_(*conds))
    rows = db.scalars(stmt.order_by(Client.name).limit(50)).all()
    chain.append(db, str(p.user.id), "client.search", "clients", {"results": len(rows)})
    return [_client_dict(c) for c in rows]


@router.get("/clients/{client_id}")
def get_client(
    client_id: uuid.UUID,
    p: Principal = Depends(require("client.read")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    c = _get_visible(db, p, client_id)
    chain.append(db, str(p.user.id), "client.read", f"client:{c.id}", {})
    fam = db.scalars(
        select(FamilyLink).where(
            or_(FamilyLink.head_client_id == c.id, FamilyLink.member_client_id == c.id)
        )
    ).all()
    return {
        **_client_dict(c),
        "family": [
            {
                "head": str(f.head_client_id),
                "member": str(f.member_client_id),
                "relation": f.relation,
            }
            for f in fam
        ],
    }


@router.post("/clients/{client_id}/family", status_code=201)
def link_family(
    client_id: uuid.UUID,
    body: FamilyIn,
    p: Principal = Depends(require("client.write")),
    db: Db = Depends(get_db),
) -> dict[str, str]:
    head = _get_visible(db, p, client_id)
    member = _get_visible(db, p, body.member_id)
    if head.id == member.id:
        raise HTTPException(422, "self_link")
    db.add(FamilyLink(head_client_id=head.id, member_client_id=member.id, relation=body.relation))
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "already_linked") from e
    chain.append(db, str(p.user.id), "family.link", f"client:{head.id}", {"member": str(member.id)})
    return {"status": "linked"}


@router.post("/clients/{client_id}/policies", status_code=201)
def add_policy(
    client_id: uuid.UUID,
    body: PolicyIn,
    p: Principal = Depends(require("policy.write")),
    db: Db = Depends(get_db),
) -> dict[str, Any]:
    c = _get_visible(db, p, client_id)
    ins = db.scalar(select(Insurer).where(Insurer.name == body.insurer))
    if ins is None:
        ins = Insurer(name=body.insurer)
        db.add(ins)
        db.flush()
    from datetime import date

    pol = Policy(
        client_id=c.id,
        insurer_id=ins.id,
        policy_number=body.policy_number,
        product=body.product,
        premium_ils=body.premium_ils,
        renewal_date=date.fromisoformat(body.renewal_date) if body.renewal_date else None,
    )
    db.add(pol)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "duplicate_policy") from e
    chain.append(db, str(p.user.id), "policy.create", f"policy:{pol.id}", {"client": str(c.id)})
    return {"id": str(pol.id)}


@router.get("/clients/{client_id}/policies")
def list_policies(
    client_id: uuid.UUID,
    p: Principal = Depends(require("policy.read")),
    db: Db = Depends(get_db),
) -> list[dict[str, Any]]:
    c = _get_visible(db, p, client_id)
    rows = db.execute(
        select(Policy, Insurer.name).join(Insurer).where(Policy.client_id == c.id)
    ).all()
    chain.append(db, str(p.user.id), "policy.read", f"client:{c.id}", {})
    return [
        {
            "id": str(pol.id),
            "insurer": name,
            "policy_number": pol.policy_number,
            "product": pol.product,
            "premium_ils": float(pol.premium_ils),
            "renewal_date": pol.renewal_date.isoformat() if pol.renewal_date else None,
        }
        for pol, name in rows
    ]


@router.get("/marketing/contacts")
def marketing_contacts(
    p: Principal = Depends(require("client.read_min")), db: Db = Depends(get_db)
) -> list[dict[str, Any]]:
    """The only client data marketing can read: the marketing_contact view."""
    rows = (
        db.execute(
            text(
                "SELECT id, name, name_translit_he, name_translit_lat, preferred_language,"
                " phone, email"
                " FROM marketing_contact ORDER BY name LIMIT 500"
            )
        )
        .mappings()
        .all()
    )
    chain.append(db, str(p.user.id), "marketing.read", "marketing_contact", {"rows": len(rows)})
    return [{**r, "id": str(r["id"])} for r in rows]


@router.get("/audit/verify")
def verify_audit(
    p: Principal = Depends(require("audit.read")), db: Db = Depends(get_db)
) -> dict[str, Any]:
    res = chain.verify(db)
    chain.append(db, str(p.user.id), "audit.verify", "audit", {"ok": res.ok})
    return {
        "ok": res.ok,
        "entries": res.entries,
        "first_bad_seq": res.first_bad_seq,
        "reason": res.reason,
    }
