"""OCR types, the engine contract, a sandboxed engine and a deterministic fake.

Tesseract itself runs only inside the sandbox (`backend/sandbox/tess.py`): it reads image
files, so it is a parser of untrusted input like any other.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.sandbox.client import Sandbox


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
    def recognize(self, pages: list[bytes]) -> OcrResult: ...


class OcrUnavailable(Exception):
    pass


class SandboxOcr:
    """Sends page images (PNG) to the sandbox and reads back words and confidences."""

    def __init__(self, sandbox: "Sandbox", langs: str = "heb+ara+rus+fra+eng") -> None:
        self.sandbox, self.langs = sandbox, langs

    def recognize(self, pages: list[bytes]) -> OcrResult:
        res = self.sandbox.ocr(pages, self.langs)
        if res is None:
            raise OcrUnavailable("sandbox returned no text")
        return res

    def recognize_latin(self, pages: list[bytes]) -> OcrResult | None:
        """Second opinion with the English model only: it reads Latin tokens inside Hebrew
        sentences (policy numbers, IDs) that the mixed model can mangle."""
        return self.sandbox.ocr(pages, "eng")


class FakeOcr:
    """Returns a prepared result per call, in order, or one fixed result. For tests."""

    def __init__(self, *results: OcrResult) -> None:
        self._results, self.calls = list(results), 0

    def recognize(self, pages: list[bytes]) -> OcrResult:
        self.calls += 1
        res = self._results[min(self.calls - 1, len(self._results) - 1)]
        return res


def result_from_text(text: str, conf: float = 0.97, pages: int = 1) -> OcrResult:
    return OcrResult(text, [Word(w, conf, 1) for w in text.split()], pages)
