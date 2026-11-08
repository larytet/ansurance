"""Limits the sandbox program puts on itself before it opens the file.

The host and the container add their own (time, memory, processes, network); these are the
ones only the program can apply, and the first thing it does is call `apply()`.
"""

import resource
import sys

# Defaults; the host passes its settings on the command line.
MAX_PAGES = 200
MAX_PIXELS = 40_000_000  # one page: A4 at 400 dpi is 15.5 million
MAX_TOTAL_PIXELS = 400_000_000  # all pages of one file


class LimitExceeded(Exception):
    """The file is within what we accept to read, but over a limit. Not a crash."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def apply(address_space_mb: int, cpu_seconds: int, max_file_mb: int) -> None:
    """Hard limits for this process and everything it starts. Cannot be raised again.

    The address-space limit is a backstop against a runaway allocation, not the memory cap:
    pdfium and OpenCV reserve far more address space than they use, so it must be generous.
    The real memory cap is the container's (cgroup) limit."""
    _cap(resource.RLIMIT_CORE, 0)
    _cap(resource.RLIMIT_CPU, cpu_seconds)
    _cap(resource.RLIMIT_AS, address_space_mb * 1024 * 1024)
    _cap(resource.RLIMIT_FSIZE, max_file_mb * 1024 * 1024)
    _cap(resource.RLIMIT_NOFILE, 64)
    sys.setrecursionlimit(2000)


def _cap(which: int, value: int) -> None:
    _, hard = resource.getrlimit(which)
    if hard != resource.RLIM_INFINITY:
        value = min(value, hard)
    resource.setrlimit(which, (value, value))
