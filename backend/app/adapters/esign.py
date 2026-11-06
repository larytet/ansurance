"""E-signature adapter (REQUIREMENTS section 9: Comsign or DocuSign). The real providers need a
contract and credentials, so only the contract and a deterministic fake exist here; a provider
adapter implements `ESign` and is wired in `make_esign`."""

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Envelope:
    provider_ref: str


class ESign(Protocol):
    def send(
        self, *, pdf: bytes, signer_name: str, signer_contact: str, title: str
    ) -> Envelope: ...
    def fetch_signed(self, provider_ref: str) -> bytes: ...


@dataclass
class FakeESign:
    sent: list[dict[str, str]] = field(default_factory=list)
    signed: dict[str, bytes] = field(default_factory=dict)
    fail: bool = False

    def send(self, *, pdf: bytes, signer_name: str, signer_contact: str, title: str) -> Envelope:
        if self.fail:
            raise ConnectionError("e-signature provider unavailable")
        ref = f"env-{len(self.sent) + 1}"
        self.sent.append({"ref": ref, "signer": signer_name, "to": signer_contact, "title": title})
        self.signed[ref] = pdf + b"\n%% signed by " + signer_name.encode()
        return Envelope(ref)

    def fetch_signed(self, provider_ref: str) -> bytes:
        return self.signed[provider_ref]


def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_payload(secret: str, body: bytes, header: str) -> bool:
    return bool(secret) and hmac.compare_digest(sign_payload(secret, body), header or "")
