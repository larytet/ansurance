from dataclasses import dataclass

from app.adapters.antivirus import Antivirus
from app.blob.base import BlobStore
from app.config import Settings
from app.intake.ocr import OcrEngine
from app.sandbox.client import Sandbox


@dataclass
class IntakeContext:
    settings: Settings
    blobs: BlobStore
    antivirus: Antivirus
    ocr: OcrEngine | None  # only the worker needs OCR
    sandbox: Sandbox  # the only place an untrusted file is opened


def make_sandbox(settings: Settings) -> Sandbox:
    from app.sandbox.client import Limits, ProcessSandbox

    if settings.sandbox_runner == "process":
        return ProcessSandbox(Limits.from_settings(settings))
    from app.sandbox.container import ContainerSandbox

    return ContainerSandbox(Limits.from_settings(settings), settings.sandbox_url)


def make_ocr(settings: Settings, sandbox: Sandbox) -> OcrEngine:
    from app.intake.ocr import FakeOcr, SandboxOcr

    if settings.ocr_engine == "fake":
        return FakeOcr()
    return SandboxOcr(sandbox, settings.ocr_langs)


def make_context(settings: Settings, with_ocr: bool = True) -> IntakeContext:
    from app.main import make_antivirus, make_blob_store

    sandbox = make_sandbox(settings)
    return IntakeContext(
        settings,
        make_blob_store(settings),
        make_antivirus(settings),
        make_ocr(settings, sandbox) if with_ocr else None,
        sandbox,
    )
