"""Antivirus adapter: ClamAV (clamd INSTREAM) for real, a deterministic fake for tests."""

import socket
import struct
from dataclasses import dataclass
from typing import Protocol

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    signature: str | None = None


class ScannerUnavailable(Exception):
    pass


class Antivirus(Protocol):
    def scan(self, data: bytes) -> ScanResult: ...


class FakeAntivirus:
    """Flags the EICAR test string. Set `available = False` to simulate an outage."""

    def __init__(self) -> None:
        self.available = True

    def scan(self, data: bytes) -> ScanResult:
        if not self.available:
            raise ScannerUnavailable("fake scanner is down")
        return ScanResult(False, "Eicar-Test-Signature") if EICAR in data else ScanResult(True)


class ClamdAntivirus:
    def __init__(self, host: str, port: int = 3310, timeout: float = 30.0) -> None:
        self._addr, self._timeout = (host, port), timeout

    def scan(self, data: bytes) -> ScanResult:
        try:
            with socket.create_connection(self._addr, timeout=self._timeout) as s:
                s.sendall(b"zINSTREAM\0")
                for i in range(0, len(data), 64 * 1024):
                    chunk = data[i : i + 64 * 1024]
                    s.sendall(struct.pack("!I", len(chunk)) + chunk)
                s.sendall(struct.pack("!I", 0))
                reply = b""
                while not reply.endswith(b"\0") and (part := s.recv(4096)):
                    reply += part
        except OSError as e:
            raise ScannerUnavailable(str(e)) from e
        text = reply.rstrip(b"\0").decode(errors="replace")
        if text.endswith("OK"):
            return ScanResult(True)
        if text.endswith("FOUND"):
            return ScanResult(False, text.split(":", 1)[-1].replace("FOUND", "").strip())
        raise ScannerUnavailable(f"unexpected clamd reply: {text}")
