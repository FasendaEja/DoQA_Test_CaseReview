"""Registry of shared (class/module-scoped) fixture nodes, keyed by the exact declaring
container (``pkg.module`` / ``pkg.module::TestClass``); resolution walks the nesting chain
so an inner class's fixtures reach only its own tests. Nodes recorded under
:data:`GLOBAL_KEY` (session-wide fixtures with no declaring container) reach every test."""

import threading
from typing import Callable, Dict, List, Optional

from .contexts import StepNode

#: Key for fixtures scoped wider than any single container (session/package scope).
GLOBAL_KEY = ""

_lock = threading.Lock()
_before: Dict[str, List[StepNode]] = {}
_after: Dict[str, List[StepNode]] = {}


def record_before(class_key: str, node: StepNode) -> None:
    with _lock:
        _before.setdefault(class_key, []).append(node)


def record_after(class_key: str, node: StepNode) -> None:
    with _lock:
        _after.setdefault(class_key, []).append(node)


def chain_keys(fqcn: Optional[str]) -> List[str]:
    """Nesting chain, outermost first: ``m::A::B`` -> ``[m, m::A, m::A::B]``."""
    if not fqcn:
        return []
    keys = []
    index = fqcn.find("::")
    while index >= 0:
        keys.append(fqcn[:index])
        index = fqcn.find("::", index + 2)
    keys.append(fqcn)
    return keys


def before_results(test_class_fqcn: Optional[str],
                   to_result_step: Callable[[StepNode], object]) -> List[object]:
    """Before nodes of the whole chain, outside-in (setup execution order); global
    (session-wide) nodes come first."""
    out = []
    for key in _resolution_chain(test_class_fqcn):
        with _lock:
            nodes = list(_before.get(key, ()))
        out.extend(to_result_step(node) for node in nodes)
    return out


def after_results(test_class_fqcn: Optional[str],
                  to_result_step: Callable[[StepNode], object]) -> List[object]:
    """After nodes of the whole chain, inside-out (teardown execution order); global
    (session-wide) nodes come last."""
    out = []
    for key in reversed(_resolution_chain(test_class_fqcn)):
        with _lock:
            nodes = list(_after.get(key, ()))
        out.extend(to_result_step(node) for node in nodes)
    return out


def _resolution_chain(fqcn: Optional[str]) -> List[str]:
    keys = chain_keys(fqcn)
    return keys if keys and keys[0] == GLOBAL_KEY else [GLOBAL_KEY] + keys


def reset() -> None:
    """Plan boundary: a rerun in the same process must not accumulate fixture duplicates."""
    with _lock:
        _before.clear()
        _after.clear()
