"""mixtaper — split DJ mixes into tagged tracks."""

from pathlib import Path

__version__ = "0.1.0"


def build_info() -> str:
    """'0.1.0 · 2026-10-28 13:05 · 27035e8' for packaged builds (stamped by packaging/build.py)."""
    try:
        return f"{__version__} · {(Path(__file__).parent / 'build.txt').read_text('utf-8').strip()}"
    except OSError:
        return f"{__version__} · dev"
