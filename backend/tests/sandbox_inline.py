"""A sandbox that runs the program's own code in the test process, for the many tests that
need pages and do not test the boundary. No process, no limits: the boundary is tested in
test_sandbox.py with the real child process. The host checks still run on its output."""

from pathlib import Path

from app.sandbox.client import Limits, ProcessSandbox
from sandbox import main as program


class InlineSandbox(ProcessSandbox):
    def __init__(self, limits: Limits | None = None) -> None:
        super().__init__(limits or Limits())

    def _execute(self, mode: str, in_dir: Path, out_dir: Path, extra: list[str]) -> None:
        argv = self._command(mode, in_dir, out_dir, extra)[3:]  # drop python -m sandbox.main
        program.execute(program.parse(argv))
