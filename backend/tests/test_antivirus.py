"""T-M2-4 with the real ClamAV (clamd INSTREAM). Slow: the signature database loads first."""

import time
from collections.abc import Iterator

import pytest

from app.adapters.antivirus import EICAR, ClamdAntivirus, ScannerUnavailable


@pytest.fixture(scope="module")
def clamd() -> Iterator[ClamdAntivirus]:
    from testcontainers.core.container import DockerContainer

    c = DockerContainer("clamav/clamav:stable").with_exposed_ports(3310)
    with c:
        av = ClamdAntivirus(c.get_container_host_ip(), int(c.get_exposed_port(3310)))
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                av.scan(b"warm up")
                break
            except ScannerUnavailable:
                time.sleep(5)
        else:
            pytest.skip("clamd did not become ready in 300 s")
        yield av


def test_clean_file_passes(clamd: ClamdAntivirus) -> None:
    assert clamd.scan(b"%PDF-1.4 harmless").clean


def test_eicar_is_detected(clamd: ClamdAntivirus) -> None:
    res = clamd.scan(EICAR)  # ClamAV matches the exact test file only
    assert not res.clean and "Eicar" in (res.signature or "")


def test_large_clean_file_streams(clamd: ClamdAntivirus) -> None:
    assert clamd.scan(b"\x00" * (5 * 1024 * 1024)).clean


def test_unreachable_scanner_raises() -> None:
    with pytest.raises(ScannerUnavailable):
        ClamdAntivirus("127.0.0.1", 1, timeout=1).scan(b"x")
