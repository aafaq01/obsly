"""Build stack frames from a traceback.

Frames are emitted oldest-first, matching how Python prints a traceback, so the last frame is
where the exception was raised.
"""

import os
import sys
import sysconfig
import traceback
from types import TracebackType
from typing import Any

_STDLIB_PATHS = tuple(
    os.path.normcase(path)
    for path in {
        sysconfig.get_paths().get("stdlib", ""),
        sysconfig.get_paths().get("platstdlib", ""),
    }
    if path
)


_SDK_ROOT = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))


def _is_in_app(filename: str) -> bool:
    """Is this the user's code, or somebody else's?

    Everything readable in an issue depends on this: with system frames folded away a trace is
    five lines, and without it a Starlette exception is forty frames of middleware.
    """
    normalised = os.path.normcase(os.path.abspath(filename))

    # Our own frames are never the user's code. A site-packages install would exclude them by
    # accident; an editable install or a vendored copy would not, and the SDK appearing at the
    # top of somebody's traceback points them at the wrong file.
    if normalised.startswith(_SDK_ROOT):
        return False
    if "site-packages" in normalised or "dist-packages" in normalised:
        return False
    return not any(normalised.startswith(path) for path in _STDLIB_PATHS)


def _frame_to_dict(frame: Any, lineno: int) -> dict[str, Any]:
    code = frame.f_code
    filename = code.co_filename

    return {
        "filename": filename,
        "abs_path": os.path.abspath(filename),
        "function": code.co_name,
        "module": frame.f_globals.get("__name__", ""),
        "lineno": lineno,
        "in_app": _is_in_app(filename),
    }


def frames_from_traceback(tb: TracebackType | None, *, limit: int = 50) -> list[dict[str, Any]]:
    frames = [_frame_to_dict(frame, lineno) for frame, lineno in traceback.walk_tb(tb)]
    # Keep the deepest frames: the top of a runaway recursion is noise, the bottom is the bug.
    return frames[-limit:]


def exception_chain(exc: BaseException, *, limit: int = 10) -> list[dict[str, Any]]:
    """Flatten `raise X from Y` and implicit chaining, oldest cause first.

    That ordering matches a printed traceback, where the thing actually raised is last — and
    that is the one an engineer reads first and searches for.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc

    while current is not None and len(chain) < limit and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__

    return [
        {
            "type": type(item).__name__,
            "value": _safe_str(item),
            "module": type(item).__module__,
            "stacktrace": {"frames": frames_from_traceback(item.__traceback__)},
        }
        for item in reversed(chain)
    ]


def _safe_str(exc: BaseException) -> str:
    """__str__ on a user exception is arbitrary code and may itself raise."""
    try:
        return str(exc)[:4000]
    except Exception:  # noqa: BLE001 - reporting must never fail on a hostile __str__
        return "<unprintable exception>"


def current_exception_chain() -> list[dict[str, Any]] | None:
    exc = sys.exc_info()[1]
    return exception_chain(exc) if exc is not None else None
