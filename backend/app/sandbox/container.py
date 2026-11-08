"""Runs the sandbox as a separate container reached over the internal network.

The sandbox service has no secrets, no route out, a read-only root and a memory-backed work
directory (compose file). The worker sends the input as a tar and gets a tar back; the
reply is unpacked member by member into a fresh directory and then checked like any output.
"""

import http.client
import io
import tarfile
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

from app.sandbox.client import (
    Limits,
    ProcessSandbox,
    SandboxRejected,
    SandboxUnavailable,
)


class ContainerSandbox(ProcessSandbox):
    def __init__(self, limits: Limits, url: str = "http://sandbox:8080", ocr_psm: int = 6) -> None:
        super().__init__(limits, ocr_psm)
        u = urlparse(url)
        self.host, self.port = u.hostname or "sandbox", u.port or 8080

    def _execute(self, mode: str, in_dir: Path, out_dir: Path, extra: list[str]) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for f in sorted(in_dir.iterdir()):
                tar.add(f, arcname=f.name)
        params: list[tuple[str, str]] = [("mode", mode)]
        lim = self.limits
        params += [
            ("address-space-mb", str(lim.memory_mb * 4)),
            ("cpu-seconds", str(lim.timeout_s)),
            ("max-pages", str(lim.max_pages)),
            ("max-pixels", str(lim.max_pixels)),
            ("max-total-pixels", str(lim.max_total_pixels)),
            ("psm", str(self.psm)),
        ]
        it = iter(extra)
        params += [(flag.lstrip("-"), value) for flag, value in zip(it, it, strict=True)]
        status, body = self._post("/run?" + urlencode(params), buf.getvalue())
        if status in (408, 422):
            raise SandboxRejected("timeout" if status == 408 else "sandbox_failed")
        if status != 200:
            raise SandboxUnavailable(f"sandbox answered {status}")
        self._unpack(body, out_dir)

    def _post(self, path: str, body: bytes) -> tuple[int, bytes]:
        """One retry: the service restarts itself after a batch, so a refusal can be a restart."""
        last: Exception | None = None
        for attempt in range(4):
            try:
                conn = http.client.HTTPConnection(
                    self.host, self.port, timeout=self.limits.timeout_s + 30
                )
                conn.request("POST", path, body=body)
                resp = conn.getresponse()
                return resp.status, resp.read()
            except (OSError, http.client.HTTPException) as e:
                last = e
                time.sleep(1 + attempt)
        raise SandboxUnavailable(str(last))

    def _unpack(self, body: bytes, out_dir: Path) -> None:
        total = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as tar:
                for m in tar.getmembers():
                    if not m.isfile() or "/" in m.name or m.name.startswith("."):
                        raise SandboxRejected("bad_output")
                    total += m.size
                    if total > self.limits.max_output_bytes:
                        raise SandboxRejected("output_too_large")
                    src = tar.extractfile(m)
                    if src is None:
                        raise SandboxRejected("bad_output")
                    (out_dir / m.name).write_bytes(src.read())
        except tarfile.TarError as e:
            raise SandboxRejected("bad_output") from e
