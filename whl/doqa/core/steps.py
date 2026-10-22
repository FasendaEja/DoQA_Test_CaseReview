"""Step-stack mechanics and outcome taxonomy."""

import traceback
from typing import Optional

from .contexts import DoqaContexts, StepNode, now_millis


def push(title: Optional[str], description: Optional[str] = None) -> Optional[StepNode]:
    ctx = DoqaContexts.current()
    if ctx is None:
        return None
    node = StepNode(title, description)
    if ctx.step_stack:
        ctx.step_stack[-1].children.append(node)
    else:
        ctx.phase_bucket().append(node)
    ctx.step_stack.append(node)
    return node


def pop(outcome: Optional[str], message: Optional[str]) -> None:
    ctx = DoqaContexts.current()
    if ctx is None or not ctx.step_stack:
        return
    node = ctx.step_stack.pop()
    node.duration_ms = now_millis() - node.start_millis
    node.outcome = outcome
    if message is not None:
        # the failure cause is never lost - appended AFTER any user add_message notes
        node.message = message if node.message is None else node.message + "\n" + message


#: Exception types beyond AssertionError that count as assertion failures. The framework
#: bridge registers its native types (e.g. ``pytest.fail.Exception``) so the test outcome
#: and its step outcomes never disagree inside one result.
_assertion_types: tuple = ()
_assertion_type_names: frozenset = frozenset()


def register_assertion_types(types=(), type_names=()) -> None:
    """Called once by the framework bridge; ``type_names`` covers types that are costly
    or unsafe to import eagerly."""
    global _assertion_types, _assertion_type_names
    _assertion_types = tuple(types)
    _assertion_type_names = frozenset(type_names)


def failure_outcome(error: Optional[BaseException]) -> str:
    """``failed`` for assertion failures (a product defect signal), ``broken`` for anything
    else (an infrastructure / test defect signal) - DoQA error clustering builds on this."""
    return "failed" if is_assertion(error) else "broken"


def is_assertion(error: Optional[BaseException]) -> bool:
    if error is None:
        return False
    return (isinstance(error, (AssertionError,) + _assertion_types)
            or type(error).__name__ in _assertion_type_names)


def message_of(error: Optional[BaseException]) -> Optional[str]:
    if error is None:
        return None
    try:
        message = str(error)
    except Exception:
        # a broken __str__ in a user exception must not replace the real failure
        message = ""
    return message if message else type(error).__name__


def stack_trace(error: Optional[BaseException]) -> Optional[str]:
    if error is None:
        return None
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))
