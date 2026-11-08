"""The sandbox program: reads one untrusted input and writes page images and one JSON result.

    python -m sandbox.main render IN_DIR OUT_DIR   (IN_DIR/input.bin  ->  page-0001.png ...)
    python -m sandbox.main ocr    IN_DIR OUT_DIR   (IN_DIR/page-0001.png ...  ->  result.json)

It imports nothing from the application and reads nothing but IN_DIR. Exit 0: a result
(or a refusal, `{"error": code}`) is in OUT_DIR/result.json. Any other exit: it crashed.
"""

import argparse
import io
import json
import sys
from pathlib import Path

from sandbox import limits

TOOL = "ansurance-sandbox/1"


def _render(args: argparse.Namespace, in_dir: Path, out_dir: Path) -> dict[str, object]:
    from sandbox import pages

    data = (in_dir / "input.bin").read_bytes()
    rendered = pages.render_pages(
        data,
        args.media_type,
        args.dpi,
        args.max_pages,
        args.max_pixels,
        args.max_total_pixels,
    )
    if not rendered:
        raise limits.LimitExceeded("no_pages")
    blank: list[bool] = []
    for n, img in enumerate(rendered, start=1):
        blank.append(pages.is_blank(img))
        buf = io.BytesIO()
        pages.clean(img).save(buf, "PNG")
        (out_dir / f"page-{n:04d}.png").write_bytes(buf.getvalue())
    return {"pages": len(rendered), "blank": blank}


def _ocr(args: argparse.Namespace, in_dir: Path, out_dir: Path) -> dict[str, object]:
    from sandbox import tess

    files = sorted(in_dir.glob("page-*.png"))
    if not files:
        raise limits.LimitExceeded("no_pages")
    return tess.recognize(files, args.langs, args.psm)


def parse(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="sandbox")
    ap.add_argument("mode", choices=["render", "ocr"])
    ap.add_argument("in_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--media-type", default="")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--langs", default="eng")
    ap.add_argument("--psm", type=int, default=6)
    ap.add_argument("--max-pages", type=int, default=limits.MAX_PAGES)
    ap.add_argument("--max-pixels", type=int, default=limits.MAX_PIXELS)
    ap.add_argument("--max-total-pixels", type=int, default=limits.MAX_TOTAL_PIXELS)
    ap.add_argument("--address-space-mb", type=int, default=8192)
    ap.add_argument("--cpu-seconds", type=int, default=120)
    ap.add_argument("--max-file-mb", type=int, default=50)
    return ap.parse_args(argv)


def execute(args: argparse.Namespace) -> None:
    """Do the work and write result.json. Never raises for a bad file: it refuses."""
    try:
        body = (
            _render(args, args.in_dir, args.out_dir)
            if args.mode == "render"
            else _ocr(args, args.in_dir, args.out_dir)
        )
        body["tool"] = TOOL
    except limits.LimitExceeded as e:
        body = {"error": e.code, "tool": TOOL}
    except MemoryError:
        body = {"error": "out_of_memory", "tool": TOOL}
    except Exception as e:  # noqa: BLE001  (a parser failed on a bad file: refuse, do not crash)
        body = {"error": "unreadable", "detail": type(e).__name__, "tool": TOOL}
    (args.out_dir / "result.json").write_text(json.dumps(body))


def main(argv: list[str]) -> int:
    args = parse(argv)
    limits.apply(args.address_space_mb, args.cpu_seconds, args.max_file_mb)
    execute(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
