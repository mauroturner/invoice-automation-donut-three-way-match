"""The error of a leak and the one way the code asks who is calling (plan, section 10; decisions D10, D23).

``LeakageError`` is raised when a part of the flow reaches for something that belongs to the evaluation: the synthetic truth (``truth.read_truth``,
U06) and CORD's test split (``cord.read_split``, U04). The guards look at the calling module and function, which stops a mistake (a task of the flow
that opens what it must not) and is not a defence against code that forges a module name; the static tests that read the imports are the other half.
"""

from __future__ import annotations

import sys
from collections.abc import Collection


class LeakageError(RuntimeError):
    """Something the flow may not read was asked for by a caller that is not allowed to."""


def caller(skip_modules: Collection[str]) -> tuple[str, str]:
    """The module and the function name of the nearest caller of whoever called this, going up past the frames whose module is in `skip_modules`
    (the module that guards and this one). ``("", "")`` when there is none."""
    frame = sys._getframe(2)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module not in skip_modules and module != __name__:
            return module, frame.f_code.co_name
        frame = frame.f_back
    return "", ""


def called_by(allowed: Collection[tuple[str, str]], skip_modules: Collection[str]) -> bool:
    """Whether the nearest outside caller of whoever called this is one of the (module, function) pairs in `allowed`."""
    frame = sys._getframe(2)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module not in skip_modules and module != __name__:
            return (module, frame.f_code.co_name) in allowed
        frame = frame.f_back
    return False
