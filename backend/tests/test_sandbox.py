"""M19: the sandbox boundary. P-24, P-25, P-28 and the limits of P-27 that a child process can
show (the container's own isolation is in test_sandbox_container.py).

A hostile file must come out as page images and JSON, or as a refusal with a reason; it must
never crash the caller, run anything, or return anything but what the checks allow."""

import io
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from PIL import Image

from app.sandbox.client import (
    Limits,
    ProcessSandbox,
    SandboxRejected,
    check_output,
    split_on_blank_pages,
)
from tests.intake_data import samples as S

FAST = Limits(timeout_s=60, max_pages=5, max_pixels=4_000_000, max_total_pixels=20_000_000)
PNG = b"\x89PNG\r\n\x1a\n"


def box(**kw: int) -> ProcessSandbox:
    return ProcessSandbox(Limits(**{**FAST.__dict__, **kw}))


def small_png(w: int = 40, h: int = 30) -> bytes:
    img = Image.new("L", (w, h), 255)
    for x in range(5, 35):
        img.putpixel((x, 15), 0)
    return S.png(img)


def png_header_only(width: int, height: int) -> bytes:
    """A valid signature and IHDR claiming a huge image, with no pixel data at all."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return PNG + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def pdf(objects: str, media_box: str = "0 0 200 200", extra_root: str = "") -> bytes:
    body = (
        "%PDF-1.7\n"
        f"1 0 obj<</Type/Catalog/Pages 2 0 R {extra_root}>>endobj\n"
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        f"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[{media_box}]>>endobj\n"
        f"{objects}\n"
        "trailer<</Root 1 0 R/Size 9>>\n%%EOF\n"
    )
    return body.encode()


def assert_clean_outcome(sb: ProcessSandbox, data: bytes, media: str) -> None:
    """Either page images (and only PNG) or a refusal. Anything else fails the test."""
    try:
        r = sb.render(data, media, 100)
    except SandboxRejected as e:
        assert e.reason  # a reason a person can read
        return
    assert r.pages and all(p.startswith(PNG) for p in r.pages)
    assert len(r.blank) == len(r.pages)


# ---- P-24 hostile PDF -----------------------------------------------------------------------
def test_pdf_with_javascript_launch_and_embedded_file_is_only_rendered(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    hostile = pdf(
        f"4 0 obj<</S/JavaScript/JS(app.launchURL('file://{marker}'))>>endobj\n"
        f"5 0 obj<</S/Launch/F(/bin/touch {marker})>>endobj\n"
        "6 0 obj<</Type/EmbeddedFile/Length 5>>stream\nEVIL!\nendstream endobj",
        extra_root="/OpenAction 4 0 R /AA<</O 5 0 R>> /Names<</EmbeddedFiles<</Names[(a.exe) 6 0 R]>>>>",
    )
    r = box().render(hostile, "application/pdf", 100)
    assert len(r.pages) == 1 and r.pages[0].startswith(PNG)
    assert not marker.exists()  # nothing was executed


def test_pdf_with_broken_cross_reference_and_garbage_never_escapes_as_an_exception() -> None:
    sb = box()
    broken = b"%PDF-1.4\n1 0 obj<<>>endobj\nxref\n0 99999\n" + b"0000000000 65535 f \n" * 50
    for data in (broken, b"%PDF-1.4\n" + os.urandom(2000), b"%PDF-1.5\n%%EOF", b"%PDF-"):
        assert_clean_outcome(sb, data, "application/pdf")


def test_pdf_page_of_absurd_size_is_refused_before_it_is_rendered() -> None:
    huge = pdf("", media_box="0 0 200000 200000")
    with pytest.raises(SandboxRejected) as e:
        box().render(huge, "application/pdf", 200)
    assert e.value.reason == "too_many_pixels"


# ---- P-25 hostile image ---------------------------------------------------------------------
def test_decompression_bomb_is_refused_from_the_header() -> None:
    bomb = png_header_only(60_000, 60_000)  # 3.6 billion pixels in 50 bytes
    assert len(bomb) < 100
    with pytest.raises(SandboxRejected) as e:
        box().render(bomb, "image/png", 200)
    assert e.value.reason in ("too_many_pixels", "unreadable")


def test_tiff_with_too_many_pages_is_refused_not_truncated() -> None:
    frames = [Image.new("L", (10, 10), 255) for _ in range(7)]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])
    with pytest.raises(SandboxRejected) as e:
        box(max_pages=5).render(buf.getvalue(), "image/tiff", 100)
    assert e.value.reason == "too_many_pages"
    assert len(box(max_pages=7).render(buf.getvalue(), "image/tiff", 100).pages) == 7


def test_image_that_is_also_an_archive_gives_back_only_the_picture() -> None:
    trailer = b"PK\x03\x04" + b"<script>alert(1)</script>" * 20
    r = box().render(small_png() + trailer, "image/png", 100)
    assert len(r.pages) == 1 and r.pages[0].startswith(PNG)
    assert b"script" not in r.pages[0] and b"PK\x03\x04" not in r.pages[0]  # re-encoded


def test_a_file_that_is_not_what_it_says_is_refused() -> None:
    with pytest.raises(SandboxRejected):
        box().render(b"not an image at all", "image/png", 100)


# ---- the program's own limits and the host's -----------------------------------------------
class Slow(ProcessSandbox):
    def _command(self, mode: str, in_dir: Path, out_dir: Path, extra: list[str]) -> list[str]:
        return [sys.executable, "-c", "import time; time.sleep(30)"]


class Crashing(ProcessSandbox):
    def _command(self, mode: str, in_dir: Path, out_dir: Path, extra: list[str]) -> list[str]:
        return [sys.executable, "-c", "import os; os.abort()"]


class Spy(ProcessSandbox):
    """Writes what the program can see into its result, to show what it is given."""

    def _command(self, mode: str, in_dir: Path, out_dir: Path, extra: list[str]) -> list[str]:
        code = (
            "import json,os,sys;"
            f"open({str(out_dir / 'result.json')!r},'w').write(json.dumps("
            "{'env':sorted(os.environ),'cwd':os.getcwd(),'in':sorted(os.listdir(sys.argv[1]))}))"
        )
        return [sys.executable, "-c", code, str(in_dir)]


def test_a_run_past_the_time_limit_is_killed_and_refused() -> None:
    sb = Slow(Limits(timeout_s=1))
    with pytest.raises(SandboxRejected) as e:
        sb.render(small_png(), "image/png", 100)
    assert e.value.reason == "timeout"


def test_a_crashing_program_is_a_refusal_not_an_exception_in_the_worker() -> None:
    with pytest.raises(SandboxRejected) as e:
        Crashing(FAST).render(small_png(), "image/png", 100)
    assert e.value.reason == "sandbox_failed"


def test_the_program_sees_no_secrets_and_only_its_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ANS_DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    in_dir, out = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    out.mkdir()
    (in_dir / "input.bin").write_bytes(b"x")
    Spy(FAST)._execute("render", in_dir, out, [])
    seen = json.loads((out / "result.json").read_text())
    assert set(seen["env"]) <= {"PATH", "PYTHONPATH", "OMP_THREAD_LIMIT", "LC_CTYPE"}
    assert seen["in"] == ["input.bin"]


def test_memory_limit_stops_a_runaway_allocation() -> None:
    code = "from sandbox import limits; limits.apply(256, 20, 10);x = bytearray(1024*1024*1024)"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode != 0 and b"MemoryError" in proc.stderr


def test_output_size_file_limit_applies_to_the_program() -> None:
    code = (
        "from sandbox import limits; limits.apply(512, 20, 1);"
        "open('/tmp/claude-1002/limit-test.bin','wb').write(b'x'*(3*1024*1024))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode != 0  # RLIMIT_FSIZE: the write is cut off (SIGXFSZ)


# ---- P-28 what comes back is checked ---------------------------------------------------------
def out_dir(tmp_path: Path, pages: int = 1, body: object | None = None) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    for i in range(1, pages + 1):
        (d / f"page-{i:04d}.png").write_bytes(small_png())
    (d / "result.json").write_text(json.dumps(body if body is not None else {"pages": pages}))
    return d


def test_the_expected_output_is_accepted(tmp_path: Path) -> None:
    body, pages = check_output(out_dir(tmp_path, 2), FAST, want_pages=True)
    assert body["pages"] == 2 and len(pages) == 2


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda d: (d / "evil.sh").write_text("#!/bin/sh"), "bad_output"),
        (lambda d: (d / "page-0001.png").write_bytes(b"GIF89a"), "bad_output"),
        (lambda d: (d / "page-0004.png").write_bytes(small_png()), "bad_output"),  # a gap
        (lambda d: (d / "page-1.png").write_bytes(small_png()), "bad_output"),  # not in the scheme
        (lambda d: (d / "result.json").write_text("[1,2]"), "bad_output"),
        (lambda d: (d / "result.json").write_text("{not json"), "bad_output"),
        (lambda d: (d / "result.json").unlink(), "no_result"),
        (lambda d: (d / "result.json").write_text('{"error":"too_many_pages"}'), "too_many_pages"),
        (
            lambda d: (d / "result.json").write_bytes(b"{" + b" " * (9 * 1024 * 1024) + b"}"),
            "output_too_large",
        ),
        (lambda d: (d / "page-0001.png").write_bytes(PNG + b"\x00" * 40), "bad_output"),
    ],
)
def test_unexpected_output_is_rejected(tmp_path: Path, mutate, reason: str) -> None:  # type: ignore[no-untyped-def]
    d = out_dir(tmp_path, 2)
    mutate(d)
    with pytest.raises(SandboxRejected) as e:
        check_output(d, FAST, want_pages=True)
    assert e.value.reason == reason


def test_a_symlink_or_a_second_hard_link_in_the_output_is_rejected(tmp_path: Path) -> None:
    d = out_dir(tmp_path, 1)
    secret = tmp_path / "secret.png"
    secret.write_bytes(small_png())
    (d / "page-0001.png").unlink()
    (d / "page-0001.png").symlink_to(secret)  # would leak a file from the host
    with pytest.raises(SandboxRejected):
        check_output(d, FAST, want_pages=True)
    (d / "page-0001.png").unlink()
    os.link(secret, d / "page-0001.png")
    with pytest.raises(SandboxRejected):
        check_output(d, FAST, want_pages=True)


def test_an_oversized_page_or_total_is_rejected(tmp_path: Path) -> None:
    d = out_dir(tmp_path, 2)
    with pytest.raises(SandboxRejected) as e:
        check_output(d, Limits(**{**FAST.__dict__, "max_page_bytes": 10}), want_pages=True)
    assert e.value.reason == "output_too_large"
    with pytest.raises(SandboxRejected) as e2:
        check_output(d, Limits(**{**FAST.__dict__, "max_output_bytes": 100}), want_pages=True)
    assert e2.value.reason == "output_too_large"
    big = out_dir(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, 0)
    (big / "page-0001.png").write_bytes(png_header_only(5000, 5000))
    with pytest.raises(SandboxRejected) as e3:
        check_output(big, FAST, want_pages=True)  # 25 million pixels > the 4 million limit
    assert e3.value.reason == "too_many_pixels"


# ---- a real round trip -----------------------------------------------------------------------
def test_render_and_blank_flags_for_a_multi_page_pdf() -> None:
    pages = [S.render(S.id_card("123456782")), S.blank(), S.render(S.invoice("000000026"))]
    r = box(max_pixels=40_000_000, max_total_pixels=400_000_000, max_pages=10).render(
        S.pdf(pages), "application/pdf", 100
    )
    assert r.blank == [False, True, False]
    assert [(g.first, g.last) for g in split_on_blank_pages(r.blank)] == [(1, 1), (3, 3)]
