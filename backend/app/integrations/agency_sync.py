"""Sync from the existing agency system (REQUIREMENTS 9.1).

Conflict rule: the existing system wins for policy data; ansurance wins for documents,
messages, consents, campaigns and tasks (it never touches them) and for what its users have
typed into a client (contact details and language are only filled in when empty).
Idempotent: running it again with the same data changes nothing."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit import chain
from app.ids import is_valid_israeli_id, normalize_id_number
from app.integrations.agency import AgencySystem, ExtClient, ExtPolicy
from app.integrations.calls import logged_call
from app.models import Client, ExternalId, InboxItem, Insurer, Policy, SyncState
from app.text import build_search, to_hebrew, to_latin

LANGS = {"he", "ar", "ru", "fr", "en"}


@dataclass
class Report:
    clients_created: int = 0
    clients_linked: int = 0
    clients_filled: int = 0
    clients_unchanged: int = 0
    policies_created: int = 0
    policies_updated: int = 0
    policies_unchanged: int = 0
    errors: list[str] = field(default_factory=list)

    def changed(self) -> bool:
        return bool(
            self.clients_created
            or self.clients_linked
            or self.clients_filled
            or self.policies_created
            or self.policies_updated
        )

    def as_json(self) -> dict[str, object]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _sync_client(db: Session, system: str, ext: ExtClient, rep: Report) -> Client | None:
    nid = normalize_id_number(ext.id_number) if ext.id_number else None
    if nid is None or not is_valid_israeli_id(nid):
        rep.errors.append(f"client {ext.external_id}: invalid ID number {ext.id_number!r}")
        return None
    link = db.scalar(
        select(ExternalId).where(
            ExternalId.system == system, ExternalId.external_id == ext.external_id
        )
    )
    client = db.get(Client, link.client_id) if link else None
    if client is None:
        client = db.scalar(select(Client).where(Client.id_number == nid))
    if client is not None and link is not None and client.id_number != nid:
        rep.errors.append(
            f"client {ext.external_id}: ID number changed from {client.id_number} "
            f"to {nid}; resolve by hand"
        )
        return None
    if client is None:
        plain, skel = build_search(ext.name, nid)
        client = Client(
            id_number=nid,
            name=ext.name,
            name_translit_he=to_hebrew(ext.name),
            name_translit_lat=to_latin(ext.name),
            search_text=plain,
            search_skel=skel,
            preferred_language=ext.language if ext.language in LANGS else "he",
            phone=ext.phone or None,
            email=ext.email or None,
            status="active",
        )
        db.add(client)
        db.flush()
        db.add(ExternalId(client_id=client.id, system=system, external_id=ext.external_id))
        rep.clients_created += 1
        return client
    changed = False
    if link is None:
        db.add(ExternalId(client_id=client.id, system=system, external_id=ext.external_id))
        rep.clients_linked += 1
        changed = True
    filled = False
    if not client.phone and ext.phone:
        client.phone, filled = ext.phone, True
    if not client.email and ext.email:
        client.email, filled = ext.email, True
    if filled:
        rep.clients_filled += 1
    if not changed and not filled:
        rep.clients_unchanged += 1
    return client


def _sync_policy(db: Session, ext: ExtPolicy, rep: Report) -> None:
    nid = normalize_id_number(ext.client_id_number) if ext.client_id_number else None
    client = db.scalar(select(Client).where(Client.id_number == nid)) if nid else None
    if client is None:
        rep.errors.append(f"policy {ext.external_id}: no client with ID {ext.client_id_number!r}")
        return
    insurer = db.scalar(select(Insurer).where(Insurer.name == ext.insurer))
    if insurer is None:
        insurer = Insurer(name=ext.insurer)
        db.add(insurer)
        db.flush()
    pol = db.scalar(
        select(Policy).where(
            Policy.insurer_id == insurer.id, Policy.policy_number == ext.policy_number
        )
    )
    if pol is None:
        db.add(
            Policy(
                client_id=client.id,
                insurer_id=insurer.id,
                policy_number=ext.policy_number,
                product=ext.product,
                premium_ils=ext.premium_ils,
                renewal_date=ext.renewal_date,
                status=ext.status,
            )
        )
        rep.policies_created += 1
        return
    new = (client.id, ext.product, round(ext.premium_ils, 2), ext.renewal_date, ext.status)
    old = (
        pol.client_id,
        pol.product,
        round(float(pol.premium_ils), 2),
        pol.renewal_date,
        pol.status,
    )
    if new == old:
        rep.policies_unchanged += 1
        return
    # the existing system wins for policy data
    pol.client_id, pol.product = client.id, ext.product
    pol.premium_ils = Decimal(str(ext.premium_ils))
    pol.renewal_date, pol.status = ext.renewal_date, ext.status
    rep.policies_updated += 1


def run_sync(
    db: Session,
    adapter: AgencySystem,
    clients: list[ExtClient] | None = None,
    policies: list[ExtPolicy] | None = None,
    dry_run: bool = False,
    actor: str = "system",
) -> Report:
    """Pull from the adapter (or use the given rows) and apply. With dry_run nothing is kept."""
    maker = sessionmaker(db.get_bind())
    rep = Report()
    state = db.get(SyncState, adapter.name) or SyncState(system=adapter.name, cursor="")
    if state not in db:
        db.add(state)
    if clients is None or policies is None:
        if not logged_call(maker, adapter.name, "health", "-", adapter.health):
            raise OSError(f"{adapter.name} is not reachable")
        ext_clients, c_cursor = logged_call(
            maker,
            adapter.name,
            "list_clients",
            state.cursor,
            lambda: adapter.list_clients(state.cursor or None),
        )
        ext_policies, p_cursor = logged_call(
            maker,
            adapter.name,
            "list_policies",
            state.cursor,
            lambda: adapter.list_policies(state.cursor or None),
        )
        clients, policies = ext_clients, ext_policies
        state.cursor = f"{c_cursor}|{p_cursor}"
    with db.begin_nested() as sp:
        for c in clients:
            _sync_client(db, adapter.name, c, rep)
        for p in policies:
            _sync_policy(db, p, rep)
        if dry_run:
            sp.rollback()
    if not dry_run:
        state.last_run = datetime.now(UTC)
        state.last_result = rep.as_json()
        chain.append(
            db,
            actor,
            "agency.sync",
            f"system:{adapter.name}",
            {k: v for k, v in rep.as_json().items() if k != "errors"} | {"errors": len(rep.errors)},
        )
        if rep.errors:
            _alert(db, adapter.name, rep)
    return rep


def _alert(db: Session, system: str, rep: Report) -> None:
    title = f"Import from {system}: {len(rep.errors)} row(s) need attention"
    exists = db.scalar(
        select(InboxItem.id).where(
            InboxItem.role == "backoffice", InboxItem.status == "open", InboxItem.title == title
        )
    )
    if exists is None:
        db.add(
            InboxItem(
                type="other",
                role="backoffice",
                urgency="important",
                title=title,
                detail="; ".join(rep.errors[:5])[:2000],
            )
        )
