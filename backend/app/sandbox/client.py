"""The host side of the sandbox: hand a file in, take page images and JSON out.

This module never opens the file. It writes the bytes to a temporary directory, starts the
sandbox, and then treats everything the sandbox produced as untrusted: only the expected
file names, only PNG, only within the size limits, only JSON of the expected shape.
"""

import json
import os
import re
import struct
import subprocess  # noqa: S404  (starts the sandbox program with fixed arguments)
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings
from app.intake.ocr import OcrResult, Word

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PAGE_NAME = re.compile(r"page-(\d{4})\.png")
SANDBOX_DIR = Path(__file__).resolve().parents[2]  # the directory that holds `sandbox/`


class SandboxRejected(Exception):
    """The sandbox refused the file or it broke the rules: send it to a person, with the reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SandboxUnavailable(Exception):
    """The sandbox itself could not run (not the file's fault): the job is retried."""


@dataclass(frozen=True)
class Limits:
    timeout_s: int = 180
    memory_mb: int = 2048
    max_pages: int = 200
    max_pixels: int = 40_000_000
    max_total_pixels: int = 400_000_000
    max_page_bytes: int = 30 * 1024 * 1024
    max_output_bytes: int = 400 * 1024 * 1024
    max_json_bytes: int = 8 * 1024 * 1024

    @classmethod
    def from_settings(cls, s: Settings) -> "Limits":
        return cls(
            timeout_s=s.sandbox_timeout_s,
            memory_mb=s.sandbox_memory_mb,
            max_pages=s.sandbox_max_pages,
            max_pixels=s.sandbox_max_pixels,
        )


@dataclass
class Rendered:
    pages: list[bytes]  # cleaned page images, PNG
    blank: list[bool]
    tool: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Sandbox(Protocol):
    def render(self, data: bytes, media_type: str, dpi: int) -> Rendered: ...

    def ocr(self, pages: list[bytes], langs: str) -> OcrResult | None: ...


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height from the header, without decoding anything."""
    if len(data) < 24 or not data.startswith(PNG_MAGIC) or data[12:16] != b"IHDR":
        raise SandboxRejected("bad_output")
    w, h = struct.unpack(">II", data[16:24])
    return int(w), int(h)


def check_output(
    out: Path, limits: Limits, *, want_pages: bool
) -> tuple[dict[str, Any], list[bytes]]:
    """Accept exactly `result.json` and numbered PNG pages, nothing else (P-28)."""
    total = 0
    pages: list[bytes] = []
    names = []
    for entry in sorted(out.iterdir(), key=lambda p: p.name):
        st = entry.lstat()  # lstat: a symlink is refused, not followed
        if not entry.is_file() or entry.is_symlink() or st.st_nlink != 1:
            raise SandboxRejected("bad_output")
        total += st.st_size
        if total > limits.max_output_bytes:
            raise SandboxRejected("output_too_large")
        if entry.name == "result.json":
            if st.st_size > limits.max_json_bytes:
                raise SandboxRejected("output_too_large")
        elif PAGE_NAME.fullmatch(entry.name) and want_pages:
            if st.st_size > limits.max_page_bytes:
                raise SandboxRejected("output_too_large")
        else:
            raise SandboxRejected("bad_output")
        names.append(entry.name)
    if "result.json" not in names:
        raise SandboxRejected("no_result")
    try:
        body = json.loads((out / "result.json").read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise SandboxRejected("bad_output") from e
    if not isinstance(body, dict):
        raise SandboxRejected("bad_output")
    if "error" in body:
        raise SandboxRejected(str(body["error"])[:60])
    page_names = [n for n in names if n != "result.json"]
    if page_names != [f"page-{i:04d}.png" for i in range(1, len(page_names) + 1)]:
        raise SandboxRejected("bad_output")  # gaps, or a name that is not in the sequence
    for n in page_names:
        data = (out / n).read_bytes()
        w, h = png_size(data)
        if w * h > limits.max_pixels:
            raise SandboxRejected("too_many_pixels")
        pages.append(data)
    return body, pages


class ProcessSandbox:
    """Runs the program as a child process with its own limits and an empty environment.

    This is the development and test runner. It has no network or filesystem isolation of its
    own, so production uses `ContainerSandbox`; both go through `check_output`.
    """

    def __init__(self, limits: Limits, ocr_psm: int = 6) -> None:
        self.limits, self.psm = limits, ocr_psm

    def _command(self, mode: str, in_dir: Path, out_dir: Path, extra: list[str]) -> list[str]:
        lim = self.limits
        return [
            sys.executable,
            "-m",
            "sandbox.main",
            mode,
            str(in_dir),
            str(out_dir),
            "--address-space-mb",
            str(lim.memory_mb * 4),
            "--cpu-seconds",
            str(lim.timeout_s),
            "--max-pages",
            str(lim.max_pages),
            "--max-pixels",
            str(lim.max_pixels),
            "--max-total-pixels",
            str(lim.max_total_pixels),
            "--psm",
            str(self.psm),
            *extra,
        ]

    def _execute(self, mode: str, in_dir: Path, out_dir: Path, extra: list[str]) -> None:
        """Run the program over `in_dir`, leaving whatever it produced in `out_dir`."""
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(SANDBOX_DIR),
            "OMP_THREAD_LIMIT": "1",
        }
        try:
            proc = subprocess.run(  # noqa: S603
                self._command(mode, in_dir, out_dir, extra),
                env=env,
                cwd=in_dir.parent,
                capture_output=True,
                timeout=self.limits.timeout_s,
                check=False,
                start_new_session=True,
            )
        except subprocess.TimeoutExpired as e:
            raise SandboxRejected("timeout") from e
        except OSError as e:
            raise SandboxUnavailable(str(e)) from e
        if proc.returncode != 0:  # crashed or was killed at a limit
            raise SandboxRejected("sandbox_failed")

    def _run(
        self, mode: str, inputs: dict[str, bytes], extra: list[str], *, want_pages: bool
    ) -> tuple[dict[str, Any], list[bytes]]:
        with tempfile.TemporaryDirectory(prefix="sandbox-") as root:
            in_dir, out_dir = Path(root, "in"), Path(root, "out")
            in_dir.mkdir()
            out_dir.mkdir()
            for name, data in inputs.items():
                (in_dir / name).write_bytes(data)
            self._execute(mode, in_dir, out_dir, extra)
            return check_output(out_dir, self.limits, want_pages=want_pages)

    def render(self, data: bytes, media_type: str, dpi: int) -> Rendered:
        body, pages = self._run(
            "render",
            {"input.bin": data},
            ["--media-type", media_type, "--dpi", str(dpi)],
            want_pages=True,
        )
        blank = body.get("blank")
        if (
            not pages
            or not isinstance(blank, list)
            or len(blank) != len(pages)
            or not all(isinstance(b, bool) for b in blank)
        ):
            raise SandboxRejected("bad_output")
        return Rendered(pages, blank, str(body.get("tool", ""))[:60])

    def ocr(self, pages: list[bytes], langs: str) -> OcrResult | None:
        for blob in pages:
            png_size(blob)  # only images made by the sandbox itself go back in
        body, _ = self._run(
            "ocr",
            {f"page-{i:04d}.png": p for i, p in enumerate(pages, start=1)},
            ["--langs", langs],
            want_pages=False,
        )
        words = body.get("words")
        text = body.get("text")
        if not isinstance(words, list) or not isinstance(text, str):
            raise SandboxRejected("bad_output")
        out: list[Word] = []
        for w in words:
            if not (
                isinstance(w, dict)
                and isinstance(w.get("t"), str)
                and isinstance(w.get("c"), int | float)
                and isinstance(w.get("p"), int)
            ):
                raise SandboxRejected("bad_output")
            out.append(Word(w["t"][:200], min(max(float(w["c"]), 0.0), 1.0), w["p"]))
        return OcrResult(text, out, len(pages))


@dataclass(frozen=True)
class PageGroup:
    first: int  # 1-based, inclusive
    last: int


def split_on_blank_pages(blank: list[bool]) -> list[PageGroup]:
    """Faxes often hold several documents separated by blank sheets."""
    groups: list[PageGroup] = []
    start: int | None = None
    for i, is_blank in enumerate(blank, start=1):
        if is_blank:
            if start is not None:
                groups.append(PageGroup(start, i - 1))
                start = None
        elif start is None:
            start = i
    if start is not None:
        groups.append(PageGroup(start, len(blank)))
    return groups or [PageGroup(1, len(blank))]
