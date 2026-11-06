"""Render pages from PDF, TIFF and images, clean them up, split multi-page faxes."""

import io
from dataclasses import dataclass

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageSequence

MAX_PAGES = 200


def render_pages(data: bytes, media_type: str, dpi: int = 200) -> list[Image.Image]:
    if media_type == "application/pdf":
        pdf = pdfium.PdfDocument(data)
        try:
            n = min(len(pdf), MAX_PAGES)
            return [pdf[i].render(scale=dpi / 72).to_pil().convert("L") for i in range(n)]
        finally:
            pdf.close()
    if media_type in ("image/png", "image/jpeg", "image/tiff"):
        img = Image.open(io.BytesIO(data))
        return [
            f.convert("L")
            for _, f in zip(range(MAX_PAGES), ImageSequence.Iterator(img), strict=False)
        ]
    return []


def deskew_angle(gray: np.ndarray) -> float:
    """Skew of the text lines in degrees, from the minimum-area rectangle of the ink."""
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) < 200:
        return 0.0
    angle = cv2.minAreaRect(coords[:, ::-1].astype(np.float32))[-1]
    angle = -(90 + angle) if angle < -45 else -angle if angle > 45 else -angle
    angle = ((angle + 45) % 90) - 45  # fold into [-45, 45)
    return float(angle) if abs(angle) <= 15 else 0.0


def crop_to_content(gray: np.ndarray, margin: int = 40) -> np.ndarray:
    """Cut the empty margins: faster OCR and a steadier layout analysis."""
    ys, xs = np.where(gray < 160)
    if len(ys) < 50:
        return gray
    h, w = gray.shape
    return gray[
        max(ys.min() - margin, 0) : min(ys.max() + margin, h),
        max(xs.min() - margin, 0) : min(xs.max() + margin, w),
    ]


def clean(img: Image.Image) -> Image.Image:
    """Crop to the content, level the text, then de-noise (the costly step runs last and
    only on the small cropped area)."""
    cv2.setNumThreads(2)
    gray = crop_to_content(np.array(img.convert("L")))
    angle = deskew_angle(gray)
    if abs(angle) >= 0.3:
        h, w = gray.shape
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        gray = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)
    gray = cv2.fastNlMeansDenoising(gray, None, 12, 5, 11)
    return Image.fromarray(gray)


def is_blank(img: Image.Image, ink_ratio: float = 0.002) -> bool:
    gray = np.array(img.convert("L").resize((400, max(1, int(400 * img.height / img.width)))))
    return float((gray < 128).mean()) < ink_ratio


@dataclass(frozen=True)
class PageGroup:
    first: int  # 1-based, inclusive
    last: int


def split_on_blank_pages(pages: list[Image.Image]) -> list[PageGroup]:
    """Faxes often hold several documents separated by blank sheets."""
    groups: list[PageGroup] = []
    start: int | None = None
    for i, p in enumerate(pages, start=1):
        if is_blank(p):
            if start is not None:
                groups.append(PageGroup(start, i - 1))
                start = None
        elif start is None:
            start = i
    if start is not None:
        groups.append(PageGroup(start, len(pages)))
    return groups or [PageGroup(1, len(pages))]
