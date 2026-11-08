"""The sandbox service: one request at a time, one child process per request.

POST /run?mode=render|ocr&arg=...  body: a tar of the input files  ->  a tar of the output files.

It has no secrets and no network beyond the internal one, a read-only root and a work
directory in memory. It exits after `--max-requests` files and the container runtime starts
a fresh one, so no state outlives a small batch (DESIGN 8.1).
"""

import argparse
import http.server
import io
import os
import re
import subprocess  # noqa: S404
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

NAME = re.compile(r"(input\.bin|page-\d{4}\.png)")
MAX_BODY = 600 * 1024 * 1024
ALLOWED = {
    "--media-type",
    "--dpi",
    "--langs",
    "--psm",
    "--max-pages",
    "--max-pixels",
    "--max-total-pixels",
    "--address-space-mb",
    "--cpu-seconds",
    "--max-file-mb",
}
VALUE = re.compile(r"[A-Za-z0-9_+./-]{0,64}")


def unpack(body: bytes, into: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as tar:
        for m in tar.getmembers():
            if not m.isfile() or not NAME.fullmatch(m.name):
                raise ValueError("bad member")
            src = tar.extractfile(m)
            if src is None:
                raise ValueError("bad member")
            (into / m.name).write_bytes(src.read())


def pack(out_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in sorted(out_dir.iterdir()):
            if f.is_file() and not f.is_symlink():
                tar.add(f, arcname=f.name)
    return buf.getvalue()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "sandbox"
    timeout_s = 200

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802  (health check)
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        q = parse_qs(url.query)
        mode = (q.get("mode") or [""])[0]
        length = int(self.headers.get("content-length") or 0)
        if url.path != "/run" or mode not in ("render", "ocr") or not 0 < length <= MAX_BODY:
            self.send_response(400)
            self.end_headers()
            return
        args: list[str] = []
        for key, values in q.items():
            if key == "mode":
                continue
            flag = f"--{key}"
            if flag not in ALLOWED or not VALUE.fullmatch(values[0]):
                self.send_response(400)
                self.end_headers()
                return
            args += [flag, values[0]]
        body = self.rfile.read(length)
        with tempfile.TemporaryDirectory(
            prefix="job-", dir=os.environ.get("WORK", "/work")
        ) as root:
            in_dir, out_dir = Path(root, "in"), Path(root, "out")
            in_dir.mkdir()
            out_dir.mkdir()
            try:
                unpack(body, in_dir)
                proc = subprocess.run(  # noqa: S603
                    [sys.executable, "-m", "sandbox.main", mode, str(in_dir), str(out_dir), *args],
                    env={
                        "PATH": os.environ.get("PATH", ""),
                        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                        "OMP_THREAD_LIMIT": "1",
                    },
                    cwd=root,
                    capture_output=True,
                    timeout=self.timeout_s,
                    check=False,
                    start_new_session=True,
                )
                code = 200 if proc.returncode == 0 else 422
                out = pack(out_dir) if code == 200 else b""
            except subprocess.TimeoutExpired:
                code, out = 408, b""
            except (ValueError, tarfile.TarError):
                code, out = 400, b""
        self.send_response(code)
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
        self.server.served = getattr(self.server, "served", 0) + 1  # type: ignore[attr-defined]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--max-requests", type=int, default=10)
    args = ap.parse_args(argv)
    srv = http.server.HTTPServer(("0.0.0.0", args.port), Handler)  # noqa: S104  (internal network)
    while getattr(srv, "served", 0) < args.max_requests:
        srv.handle_request()
    return 0  # the container is restarted: a fresh process for the next files


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
