"""OCR adapter. Tesseract (local, no data leaves the machine) and a deterministic fake."""

import csv
import io
import os
import subprocess  # noqa: S404  (runs the local tesseract binary with fixed arguments)
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class Word:
    text: str
    conf: float  # 0..1
    page: int


@dataclass
class OcrResult:
    text: str
    words: list[Word] = field(default_factory=list)
    pages: int = 1

    @property
    def mean_conf(self) -> float:
        return sum(w.conf for w in self.words) / len(self.words) if self.words else 0.0


class OcrEngine(Protocol):
    def recognize(self, pages: list[Image.Image]) -> OcrResult: ...


class OcrUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def installed_languages() -> frozenset[str]:
    try:
        out = subprocess.run(  # noqa: S603, S607
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise OcrUnavailable(str(e)) from e
    return frozenset(line.strip() for line in out.splitlines()[1:] if line.strip())


class TesseractOcr:
    """Runs `tesseract` per page and reads word confidences from its TSV output."""

    def __init__(self, langs: str = "heb+ara+rus+fra+eng", psm: int = 6) -> None:
        have = installed_languages()
        chosen = [lang for lang in langs.split("+") if lang in have]
        if not chosen:
            raise OcrUnavailable(f"none of {langs} is installed (have {sorted(have)})")
        self.langs, self.psm = "+".join(chosen), psm
        self.missing = [lang for lang in langs.split("+") if lang not in have]

    def recognize(self, pages: list[Image.Image]) -> OcrResult:
        return self._run(pages, self.langs)

    def recognize_latin(self, pages: list[Image.Image]) -> OcrResult | None:
        """Second opinion with the English model only: it reads Latin tokens inside Hebrew
        sentences (policy numbers, IDs) that the mixed model can mangle."""
        return self._run(pages, "eng") if "eng" in installed_languages() else None

    def _run(self, pages: list[Image.Image], langs: str) -> OcrResult:
        words: list[Word] = []
        lines: list[str] = []
        for n, img in enumerate(pages, start=1):
            with tempfile.TemporaryDirectory() as d:
                src = Path(d) / "page.png"
                img.save(src)
                proc = subprocess.run(  # noqa: S603, S607
                    ["tesseract", str(src), "-", "-l", langs, "--psm", str(self.psm), "tsv"],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                    env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                )
            if proc.returncode != 0:
                raise OcrUnavailable(proc.stderr.strip()[:300])
            cur_line, cur_key = [], None
            for row in csv.DictReader(
                io.StringIO(proc.stdout), delimiter="\t", quoting=csv.QUOTE_NONE
            ):
                text = (row.get("text") or "").strip()
                if row.get("level") != "5" or not text:
                    continue
                conf = max(float(row["conf"]), 0.0) / 100.0
                words.append(Word(text, conf, n))
                key = (row["block_num"], row["par_num"], row["line_num"])
                if cur_key not in (None, key):
                    lines.append(" ".join(cur_line))
                    cur_line = []
                cur_key = key
                cur_line.append(text)
            if cur_line:
                lines.append(" ".join(cur_line))
        return OcrResult("\n".join(lines), words, len(pages))


class FakeOcr:
    """Returns a prepared result per call, in order, or one fixed result. For tests."""

    def __init__(self, *results: OcrResult) -> None:
        self._results, self.calls = list(results), 0

    def recognize(self, pages: list[Image.Image]) -> OcrResult:
        self.calls += 1
        res = self._results[min(self.calls - 1, len(self._results) - 1)]
        return res


def result_from_text(text: str, conf: float = 0.97, pages: int = 1) -> OcrResult:
    return OcrResult(text, [Word(w, conf, 1) for w in text.split()], pages)
