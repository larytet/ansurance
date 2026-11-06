"""Machine translation adapter (REQUIREMENTS section 5). A real provider (Azure, Google, DeepL
and others) needs a contract and a data-residency check, so only the contract and a
deterministic fake exist."""

from typing import Protocol


class Translator(Protocol):
    def translate(self, text: str, source: str, target: str) -> str: ...


class FakeTranslator:
    """Returns `[target] text` so tests can see what was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail = False

    def translate(self, text: str, source: str, target: str) -> str:
        if self.fail:
            raise ConnectionError("translation provider unavailable")
        self.calls.append((text, source, target))
        return f"[{target}] {text}"
