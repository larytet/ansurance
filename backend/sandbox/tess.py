"""Tesseract, run over page images. Inside the sandbox only."""

import csv
import io
import os
import subprocess  # noqa: S404  (the local tesseract binary, fixed arguments)
from pathlib import Path


def installed_languages() -> frozenset[str]:
    out = subprocess.run(  # noqa: S603, S607
        ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20, check=True
    ).stdout
    return frozenset(line.strip() for line in out.splitlines()[1:] if line.strip())


def recognize(pages: list[Path], langs: str, psm: int = 6) -> dict[str, object]:
    """Words with a confidence (0..1) and page number, and the text line by line.
    Languages that are not installed are dropped; none left is an error."""
    have = installed_languages()
    chosen = [lang for lang in langs.split("+") if lang in have]
    if not chosen:
        raise RuntimeError(f"none of {langs} is installed")
    words: list[dict[str, object]] = []
    lines: list[str] = []
    for n, src in enumerate(pages, start=1):
        proc = subprocess.run(  # noqa: S603, S607
            ["tesseract", str(src), "-", "-l", "+".join(chosen), "--psm", str(psm), "tsv"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            env={"PATH": os.environ.get("PATH", ""), "OMP_THREAD_LIMIT": "1"},
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip()[:300])
        cur_line: list[str] = []
        cur_key: tuple[str, str, str] | None = None
        for row in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
            text = (row.get("text") or "").strip()
            if row.get("level") != "5" or not text:
                continue
            words.append({"t": text, "c": max(float(row["conf"]), 0.0) / 100.0, "p": n})
            key = (row["block_num"], row["par_num"], row["line_num"])
            if cur_key not in (None, key):
                lines.append(" ".join(cur_line))
                cur_line = []
            cur_key = key
            cur_line.append(text)
        if cur_line:
            lines.append(" ".join(cur_line))
    return {"text": "\n".join(lines), "words": words, "pages": len(pages), "langs": chosen}
