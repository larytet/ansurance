from dataclasses import dataclass

from app.adapters.antivirus import Antivirus
from app.blob.base import BlobStore
from app.config import Settings
from app.intake.ocr import OcrEngine


@dataclass
class IntakeContext:
    settings: Settings
    blobs: BlobStore
    antivirus: Antivirus
    ocr: OcrEngine | None  # only the worker needs OCR


def make_ocr(settings: Settings) -> OcrEngine:
    from app.intake.ocr import FakeOcr, TesseractOcr

    if settings.ocr_engine == "fake":
        return FakeOcr()
    return TesseractOcr(settings.ocr_langs)


def make_context(settings: Settings, with_ocr: bool = True) -> IntakeContext:
    from app.main import make_antivirus, make_blob_store

    return IntakeContext(
        settings,
        make_blob_store(settings),
        make_antivirus(settings),
        make_ocr(settings) if with_ocr else None,
    )
