"""Synthetic scans for the intake tests: Hebrew and English documents rendered with a real
font, optionally degraded like a fax (skew, noise, low resolution, 1-bit)."""

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
W, H = 1654, 2339  # A4 at 200 dpi


def _has_hebrew(s: str) -> bool:
    return any("֐" <= ch <= "׿" for ch in s)


def render(
    lines: list[str],
    *,
    size: int = 34,
    skew: float = 0.0,
    noise: float = 0.0,
    fax: bool = False,
    seed: int = 0,
) -> Image.Image:
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    y = 140
    for i, line in enumerate(lines):
        font = ImageFont.truetype(str(BOLD if i == 0 else FONT), size + (10 if i == 0 else 0))
        text = line  # Pillow (raqm) applies the bidirectional algorithm itself
        w = d.textlength(text, font=font)
        x = W - 140 - w if _has_hebrew(line) else 140  # Hebrew right-aligned
        d.text((x, y), text, font=font, fill=0)
        y += size * 2
    if skew:
        img = img.rotate(skew, resample=Image.Resampling.BICUBIC, fillcolor=255)
    if fax:  # low resolution, soft, 1-bit
        img = img.resize((W // 2, H // 2), Image.Resampling.BILINEAR)
        img = img.filter(ImageFilter.GaussianBlur(0.8)).point(lambda v: 255 if v > 150 else 0)
        img = img.resize((W, H), Image.Resampling.NEAREST).convert("L")
    if noise:
        arr = np.array(img, dtype=np.float32)
        arr += np.random.default_rng(seed).normal(0, noise, arr.shape)
        img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    return img


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def pdf(pages: list[Image.Image]) -> bytes:
    buf = io.BytesIO()
    first, rest = pages[0].convert("RGB"), [p.convert("RGB") for p in pages[1:]]
    first.save(buf, "PDF", save_all=True, append_images=rest, resolution=200)
    return buf.getvalue()


def tiff(pages: list[Image.Image]) -> bytes:
    buf = io.BytesIO()
    pages[0].save(buf, "TIFF", save_all=True, append_images=pages[1:], compression="tiff_lzw")
    return buf.getvalue()


def blank() -> Image.Image:
    return Image.new("L", (W, H), 255)


# ---- document templates -----------------------------------------------------------------
def id_card(id_number: str, name: str = "דוד כהן") -> list[str]:
    return [
        "תעודת זהות",
        "מדינת ישראל",
        f"שם: {name}",
        f"מספר זהות {id_number}",
        "Israel Identity Card",
        f"ID Number {id_number}",
        "תאריך לידה 01/02/1980",
    ]


def addendum(head: str, kids: list[str]) -> list[str]:
    return ["ספח תעודת זהות", f"מספר זהות {head}", "ילדים:"] + [f"{k}  ילד" for k in kids]


def policy(number: str, id_number: str) -> list[str]:
    return [
        "פוליסה",
        f"מספר פוליסה {number}",
        f"Policy Number {number}",
        f"ת.ז. המבוטח {id_number}",
        "פרמיה חודשית 245",
    ]


def claim(number: str, id_number: str) -> list[str]:
    return [
        "טופס תביעה",
        "Claim form",
        f"מספר פוליסה {number}",
        f"ת.ז {id_number}",
        "מועד האירוע 12/03/2026",
    ]


def medical(id_number: str) -> list[str]:
    return ["דוח רפואי", "Medical report", f"ת.ז {id_number}", "אבחנה: בדיקה שגרתית"]


def invoice(id_number: str) -> list[str]:
    return ["חשבונית מס", "Tax invoice", f"לקוח ת.ז {id_number}", "סכום כולל מע מ 1180"]


def insurer_letter(number: str) -> list[str]:
    return ["לכבוד", "Dear Sir", f"בהמשך לפנייתך בנושא פוליסה {number}", "אנו מאשרים את הבקשה"]


def bank_form(id_number: str) -> list[str]:
    return ["הוראת קבע", "Direct debit mandate", f"ת.ז {id_number}", "IBAN IL620108000000099999999"]


def letter_no_ids() -> list[str]:
    return ["לכבוד", "Dear Sir", "מכתב כללי ללא מספרים מזהים", "בברכה"]


def name_only(name: str) -> list[str]:
    return ["חשבונית מס", "Tax invoice", f"לכבוד {name}", "סכום 500"]
