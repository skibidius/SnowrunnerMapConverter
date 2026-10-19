"""Small terminal UI helpers: spinners and progress bars.

No external dependencies. Falls back to plain text when stdout is not a TTY.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time


def _enable_windows_ansi() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        for handle in (-11, -12):  # stdout, stderr
            h = kernel32.GetStdHandle(handle)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


_enable_windows_ansi()

ENABLED = bool(sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb")
_IS_WINDOWS = sys.platform.startswith("win")
FRAMES = "|/-\\" if _IS_WINDOWS else "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
OK_MARK = "+" if _IS_WINDOWS else "✓"
FAIL_MARK = "x" if _IS_WINDOWS else "✗"
BAR_FULL = "#" if _IS_WINDOWS else "█"
BAR_EMPTY = "-" if _IS_WINDOWS else "░"


def configure(enabled: bool | None = None) -> None:
    global ENABLED
    if enabled is None:
        ENABLED = bool(sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb")
    else:
        ENABLED = bool(enabled and sys.stdout.isatty())


def _width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 20)).columns
    except Exception:
        return default


def _write(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.write(text.encode("ascii", "replace").decode("ascii"))
        sys.stdout.flush()
    except BrokenPipeError:
        pass


def _print(text: str, error: bool = False) -> None:
    try:
        print(text, file=sys.stderr if error else sys.stdout, flush=True)
    except BrokenPipeError:
        pass


def _clear() -> None:
    if ENABLED:
        _write("\r\033[K")


def status(text: str) -> None:
    if ENABLED:
        _write(f"{OK_MARK} {text}\n")
    else:
        _print(f"[ok] {text}")


def info(text: str) -> None:
    print(text)


def warn(text: str) -> None:
    print(f"! {text}", file=sys.stderr)


class Spinner:
    def __init__(self, text: str):
        self.text = text
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def __enter__(self) -> "Spinner":
        self._started = time.monotonic()
        if ENABLED:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            _print(f"{self.text} ...")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        elapsed = time.monotonic() - self._started
        if ENABLED:
            _clear()
            if exc_type is None:
                _write(f"{OK_MARK} {self.text} ({elapsed:.1f} s)\n")
            else:
                _write(f"{FAIL_MARK} {self.text}\n")
        elif exc_type is None:
            _print(f"[ok] {self.text}")
        else:
            _print(f"[failed] {self.text}", error=True)

    def _run(self) -> None:
        frames = FRAMES
        i = 0
        while not self._stop.wait(0.09):
            frame = frames[i % len(frames)]
            _write(f"\r{frame} {self.text}")
            i += 1


class Progress:
    def __init__(self, text: str, total: int, unit: str = ""):
        self.text = text
        self.total = max(0, int(total))
        self.unit = unit
        self.current = 0
        self._last = 0.0
        self._started = time.monotonic()
        self._closed = False
        if not ENABLED:
            self._next_report = 0.0

    def update(self, current: int, suffix: str = "") -> None:
        current = max(0, min(int(current), self.total))
        self.current = current
        if not ENABLED:
            if self.total <= 0:
                return
            now = time.monotonic()
            if now >= getattr(self, "_next_report", 0.0):
                pct = current * 100.0 / self.total
                _print(f"{self.text}: {pct:.0f}% ({current:,}/{self.total:,})")
                self._next_report = now + 5.0
            return
        now = time.monotonic()
        if current < self.total and now - self._last < 0.05:
            return
        self._last = now
        width = max(10, min(32, _width() - 48))
        frac = 0.0 if self.total == 0 else current / self.total
        filled = int(width * frac)
        bar = BAR_FULL * filled + BAR_EMPTY * (width - filled)
        extra = f" {suffix}" if suffix else ""
        _write(f"\r{self.text} [{bar}] {frac*100:3.0f}% "
               f"{current:,}/{self.total:,}{extra}\033[K")

    def advance(self, amount: int = 1, suffix: str = "") -> None:
        self.update(self.current + amount, suffix)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if ENABLED:
            if self.total and self.current < self.total:
                self.update(self.total)
            _write("\n")
        else:
            if self.total:
                _print(f"{self.text}: 100% ({self.total:,}/{self.total:,})")
        elapsed = time.monotonic() - self._started
        if elapsed > 3.0 and ENABLED:
            status(f"{self.text} — {elapsed:.1f} s")

    def __enter__(self) -> "Progress":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
