"""The existing agency system (REQUIREMENTS 9.1): one adapter contract for any vendor, plus a
file-drop adapter (the scheduled CSV export most systems can produce) and a fake for tests."""

import csv
import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ExtClient:
    external_id: str
    id_number: str
    name: str
    phone: str = ""
    email: str = ""
    language: str = ""


@dataclass(frozen=True)
class ExtPolicy:
    external_id: str
    client_id_number: str
    insurer: str
    policy_number: str
    product: str
    premium_ils: float
    renewal_date: date | None = None
    status: str = "active"


class AgencySystem(Protocol):
    name: str

    def health(self) -> bool: ...

    def list_clients(self, since: str | None) -> tuple[list[ExtClient], str]:
        """Clients changed since the cursor; returns (clients, new cursor)."""
        ...

    def list_policies(self, since: str | None) -> tuple[list[ExtPolicy], str]: ...

    def get_document(self, external_id: str) -> bytes: ...

    def push_task(self, client_external_id: str, text: str) -> None:
        """Optional write-back: a task or note in the old system."""
        ...


class FakeAgencySystem:
    name = "fake"

    def __init__(
        self, clients: list[ExtClient] | None = None, policies: list[ExtPolicy] | None = None
    ) -> None:
        self.clients, self.policies = clients or [], policies or []
        self.up = True
        self.tasks: list[tuple[str, str]] = []

    def health(self) -> bool:
        return self.up

    def list_clients(self, since: str | None) -> tuple[list[ExtClient], str]:
        if not self.up:
            raise OSError("agency system unreachable")
        return list(self.clients), str(len(self.clients))

    def list_policies(self, since: str | None) -> tuple[list[ExtPolicy], str]:
        if not self.up:
            raise OSError("agency system unreachable")
        return list(self.policies), str(len(self.policies))

    def get_document(self, external_id: str) -> bytes:
        return b"%PDF-1.4 fake"

    def push_task(self, client_external_id: str, text: str) -> None:
        self.tasks.append((client_external_id, text))


# ---- CSV ----
CLIENT_COLUMNS = ["external_id", "id_number", "name", "phone", "email", "language"]
POLICY_COLUMNS = [
    "external_id",
    "client_id_number",
    "insurer",
    "policy_number",
    "product",
    "premium_ils",
    "renewal_date",
    "status",
]


class CsvError(ValueError):
    pass


def _rows(text: str, required: list[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = [c for c in required if c not in (reader.fieldnames or [])]
    if missing:
        raise CsvError(f"missing columns: {', '.join(missing)}")
    return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def parse_clients_csv(text: str) -> list[ExtClient]:
    return [
        ExtClient(
            r["external_id"],
            r["id_number"],
            r["name"],
            r.get("phone", ""),
            r.get("email", ""),
            r.get("language", ""),
        )
        for r in _rows(text, ["external_id", "id_number", "name"])
    ]


def parse_policies_csv(text: str) -> list[ExtPolicy]:
    out = []
    for n, r in enumerate(
        _rows(text, ["external_id", "client_id_number", "insurer", "policy_number"]), start=2
    ):
        try:
            premium = float(r.get("premium_ils") or 0)
            renewal = date.fromisoformat(r["renewal_date"]) if r.get("renewal_date") else None
        except ValueError as e:
            raise CsvError(f"row {n}: {e}") from e
        out.append(
            ExtPolicy(
                r["external_id"],
                r["client_id_number"],
                r["insurer"],
                r["policy_number"],
                r.get("product", "other") or "other",
                premium,
                renewal,
                r.get("status", "active") or "active",
            )
        )
    return out


class FileDropAgency:
    """Reads `clients*.csv` and `policies*.csv` dropped in a folder by the other system's
    scheduled export. Each run reads the newest file of each kind (a full export), so a repeated
    run changes nothing; processed files are moved aside only by `archive()`."""

    name = "filedrop"

    def __init__(self, folder: str) -> None:
        self._dir = Path(folder)

    def _newest(self, prefix: str) -> Path | None:
        files = sorted(self._dir.glob(f"{prefix}*.csv"), key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None

    def health(self) -> bool:
        return self._dir.is_dir()

    def list_clients(self, since: str | None) -> tuple[list[ExtClient], str]:
        f = self._newest("clients")
        if f is None:
            return [], since or ""
        return parse_clients_csv(f.read_text("utf-8")), f"{f.name}:{f.stat().st_mtime_ns}"

    def list_policies(self, since: str | None) -> tuple[list[ExtPolicy], str]:
        f = self._newest("policies")
        if f is None:
            return [], since or ""
        return parse_policies_csv(f.read_text("utf-8")), f"{f.name}:{f.stat().st_mtime_ns}"

    def get_document(self, external_id: str) -> bytes:
        raise NotImplementedError("the file drop carries no documents")

    def push_task(self, client_external_id: str, text: str) -> None:
        return  # one-way
