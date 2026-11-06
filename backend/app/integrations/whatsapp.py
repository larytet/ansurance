"""WhatsApp Business (Meta Cloud API). A real adapter over HTTP and a fake for tests."""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx


class WhatsAppError(Exception):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class WhatsApp(Protocol):
    def send_text(self, to_e164: str, body: str) -> str:
        """Send a message; returns the provider's message id."""
        ...

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        """(bytes, mime type) of a media item a client sent."""
        ...


class MetaCloudWhatsApp:
    def __init__(
        self, phone_id: str, token: str, api_base: str, http: httpx.Client | None = None
    ) -> None:
        self._phone, self._base = phone_id, api_base.rstrip("/")
        self._http = http or httpx.Client(timeout=20)
        self._headers = {"authorization": f"Bearer {token}"}

    def send_text(self, to_e164: str, body: str) -> str:
        r = self._http.post(
            f"{self._base}/{self._phone}/messages",
            headers=self._headers,
            json={
                "messaging_product": "whatsapp",
                "to": to_e164.lstrip("+"),
                "type": "text",
                "text": {"body": body},
            },
        )
        if r.status_code >= 400:
            # 4xx other than rate limiting is the request's fault: retrying cannot help
            raise WhatsAppError(
                f"{r.status_code} {r.text[:200]}",
                retryable=r.status_code >= 500 or r.status_code == 429,
            )
        try:
            return str(r.json()["messages"][0]["id"])
        except (KeyError, IndexError, ValueError) as e:
            raise WhatsAppError("unexpected response from WhatsApp") from e

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        meta = self._http.get(f"{self._base}/{media_id}", headers=self._headers)
        if meta.status_code >= 400:
            raise WhatsAppError(
                f"media lookup {meta.status_code}", retryable=meta.status_code >= 500
            )
        info = meta.json()
        blob = self._http.get(info["url"], headers=self._headers)
        if blob.status_code >= 400:
            raise WhatsAppError(
                f"media download {blob.status_code}", retryable=blob.status_code >= 500
            )
        return blob.content, str(info.get("mime_type", "application/octet-stream"))


class FakeWhatsApp:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.media: dict[str, tuple[bytes, str]] = {}
        self.fail_with: WhatsAppError | None = None

    def send_text(self, to_e164: str, body: str) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((to_e164, body))
        return f"wamid.FAKE{len(self.sent)}"

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        if media_id not in self.media:
            raise WhatsAppError("no such media", retryable=False)
        return self.media[media_id]


# ---- webhook ----------------------------------------------------------------------------------
def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256: HMAC-SHA256 of the raw body with the app secret."""
    if not header or not secret:
        return False
    return hmac.compare_digest(sign(secret, body), header.strip())


@dataclass
class InboundItem:
    provider_id: str
    from_address: str
    kind: str  # text | media
    body: str = ""
    media_id: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class StatusItem:
    provider_id: str
    status: str  # sent | delivered | read | failed


def parse_webhook(payload: dict[str, Any]) -> tuple[list[InboundItem], list[StatusItem]]:
    inbound: list[InboundItem] = []
    statuses: list[StatusItem] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for m in value.get("messages", []):
                ts = (
                    datetime.fromtimestamp(int(m.get("timestamp", 0)) or 0, UTC)
                    if m.get("timestamp")
                    else datetime.now(UTC)
                )
                kind = m.get("type", "")
                if kind == "text":
                    inbound.append(
                        InboundItem(m["id"], m["from"], "text", m["text"]["body"], at=ts)
                    )
                elif kind in ("image", "document"):
                    media = m.get(kind, {})
                    inbound.append(
                        InboundItem(
                            m["id"],
                            m["from"],
                            "media",
                            media.get("caption", ""),
                            media.get("id", ""),
                            ts,
                        )
                    )
            for st in value.get("statuses", []):
                statuses.append(StatusItem(st["id"], st["status"]))
    return inbound, statuses


STOP_WORDS = {
    "stop",
    "unsubscribe",
    "הפסק",
    "הסר",
    "הסרה",
    "עצור",
    "стоп",
    "arrêt",
    "arret",
    "إيقاف",
}


def is_stop(text: str) -> bool:
    return text.strip().strip(".!").casefold() in STOP_WORDS
