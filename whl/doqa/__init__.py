"""DoQA user facade: declarative markup decorators, steps and the runtime ``add_*`` API.

Decorators attach metadata to the test function (or class) via the ``__doqa_meta__``
attribute; the adapter reads it when building the autotest definition. Every runtime call
outside an active test context is a safe no-op and never raises.
"""

import functools
import inspect
from typing import Any, Callable, Iterable, Optional, TypeVar, Union

from doqa.client.models import Link as _Link
from doqa.client.outcomes import LinkType as _LinkType
from doqa.core import limits as _limits
from doqa.core import placeholders as _placeholders
from doqa.core import steps as _steps
from doqa.core.contexts import AttachmentRef as _AttachmentRef
from doqa.core.contexts import DoqaContexts as _DoqaContexts
from doqa.core.meta import META_ATTRIBUTE as _META_ATTRIBUTE

_T = TypeVar("_T")
_Decoratable = TypeVar("_Decoratable", bound=Union[Callable[..., Any], type])

__all__ = [
    "id", "display_name", "title", "description", "label", "tag", "link", "case_ids",
    "namespace", "class_name", "create_manual_case", "step",
    "add_external_id", "add_display_name", "add_title", "add_description", "add_message",
    "add_parameter", "add_labels", "add_label", "add_tags", "add_links", "add_link",
    "add_case_ids", "add_create_manual_case", "attach", "attach_file",
    "Context", "capture_context", "run_with",
]


# --------------------------------------------------------------------- markup decorators
def _writable_meta(obj: Any) -> dict:
    if isinstance(obj, type):
        if _META_ATTRIBUTE not in vars(obj):
            setattr(obj, _META_ATTRIBUTE, {})
        return vars(obj)[_META_ATTRIBUTE]
    declared = getattr(obj, _META_ATTRIBUTE, None)
    if declared is None:
        declared = {}
        setattr(obj, _META_ATTRIBUTE, declared)
    return declared


def _scalar(key: str, value: str) -> Callable[[_Decoratable], _Decoratable]:
    def apply(obj: _Decoratable) -> _Decoratable:
        _writable_meta(obj)[key] = value
        return obj
    return apply


def id(value: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:  # noqa: A001
    """Explicit stable external id (e.g. ``doqa.id("DOQA-42")``); the highest attribution
    priority. Test functions only: on a class every test would share the id and collapse
    into a single autotest, so that is rejected with a ``TypeError``."""
    def apply(obj: Callable[..., Any]) -> Callable[..., Any]:
        if isinstance(obj, type):
            raise TypeError(
                "doqa.id decorates test functions only: an id on class "
                + obj.__name__ + " would collapse all of its tests into one autotest")
        _writable_meta(obj)["id"] = value
        return obj
    return apply


def display_name(value: str) -> Callable[[_Decoratable], _Decoratable]:
    return _scalar("display_name", value)


def title(value: str) -> Callable[[_Decoratable], _Decoratable]:
    return _scalar("title", value)


def description(value: str) -> Callable[[_Decoratable], _Decoratable]:
    return _scalar("description", value)


def namespace(value: str) -> Callable[[_Decoratable], _Decoratable]:
    return _scalar("namespace", value)


def class_name(value: str) -> Callable[[_Decoratable], _Decoratable]:
    return _scalar("classname", value)


def label(name: str, value: Optional[str] = None) -> Callable[[_Decoratable], _Decoratable]:
    """A flat label, or a ``name:value`` facet when ``value`` is given."""
    text = name if value is None else name + ":" + value

    def apply(obj: _Decoratable) -> _Decoratable:
        _writable_meta(obj).setdefault("labels", []).append(text)
        return obj
    return apply


def tag(*values: str) -> Callable[[_Decoratable], _Decoratable]:
    def apply(obj: _Decoratable) -> _Decoratable:
        _writable_meta(obj).setdefault("tags", []).extend(values)
        return obj
    return apply


def case_ids(*ids: int) -> Callable[[_Decoratable], _Decoratable]:
    """Manual case ids bound to this autotest (one autotest - N cases)."""
    def apply(obj: _Decoratable) -> _Decoratable:
        _writable_meta(obj)["case_ids"] = [int(i) for i in ids]
        return obj
    return apply


def create_manual_case(obj: Optional[_Decoratable] = None):
    """Marker: ask DoQA to create a linked manual case on report. Usable bare or called."""
    def apply(target: _Decoratable) -> _Decoratable:
        _writable_meta(target)["create_manual_case"] = True
        return target
    return apply(obj) if obj is not None else apply


class _LinkFactory:
    """``doqa.link(url, ...)`` decorator plus typed shortcuts
    (``doqa.link.defect(url, ...)`` etc.)."""

    def __call__(self, url: str, type: Optional[str] = None,  # noqa: A002
                 title: Optional[str] = None,
                 description: Optional[str] = None) -> Callable[[_Decoratable], _Decoratable]:
        def apply(obj: _Decoratable) -> _Decoratable:
            _writable_meta(obj).setdefault("links", []).append({
                "url": url, "type": type, "title": title, "description": description,
            })
            return obj
        return apply

    def related(self, url: str, **kwargs: Any):
        return self(url, type=_LinkType.RELATED, **kwargs)

    def defect(self, url: str, **kwargs: Any):
        return self(url, type=_LinkType.DEFECT, **kwargs)

    def requirement(self, url: str, **kwargs: Any):
        return self(url, type=_LinkType.REQUIREMENT, **kwargs)

    def blocked_by(self, url: str, **kwargs: Any):
        return self(url, type=_LinkType.BLOCKED_BY, **kwargs)

    def repository(self, url: str, **kwargs: Any):
        return self(url, type=_LinkType.REPOSITORY, **kwargs)


link = _LinkFactory()


# --------------------------------------------------------------------- steps
class _StepContext:
    def __init__(self, title: Optional[str], description: Optional[str] = None):
        self._title = title
        self._description = description

    def __enter__(self) -> "_StepContext":
        _steps.push(self._title, self._description)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            _steps.pop("passed", None)
        else:
            _steps.pop(_steps.failure_outcome(exc), _steps.message_of(exc))
        return False

    def __call__(self, func: Callable[..., _T]) -> Callable[..., _T]:
        template = self._title if self._title else func.__name__

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> _T:
            resolved = _resolve_step_title(template, func, args, kwargs)
            _steps.push(resolved, self._description)
            try:
                value = func(*args, **kwargs)
            except BaseException as error:
                _steps.pop(_steps.failure_outcome(error), _steps.message_of(error))
                raise
            _steps.pop("passed", None)
            return value
        return wrapper


def step(title: Union[str, Callable[..., Any], None] = None,
         description: Optional[str] = None):
    """A step: context manager (``with doqa.step("..."):``) and decorator
    (``@doqa.step("open {page} page")`` - ``{param}`` placeholders resolve from the call
    arguments). Bare ``@doqa.step`` titles the step with the function name."""
    if callable(title):
        return _StepContext(None)(title)
    return _StepContext(title, description)


def _resolve_step_title(template: str, func: Callable[..., Any],
                        args: Any, kwargs: Any) -> str:
    if "{" not in template:
        return template
    params: dict = {}
    try:
        bound = inspect.signature(func).bind(*args, **kwargs)
        arguments = list(bound.arguments.items())
    except TypeError:
        arguments = []
    max_parameter = _limits.max_parameter_length()
    for name, value in arguments:
        params.setdefault(name, _limits.truncate(_placeholders.stringify(value),
                                                 max_parameter))
    for index, (_, value) in enumerate(arguments):
        params.setdefault(str(index), _limits.truncate(_placeholders.stringify(value),
                                                       max_parameter))
    return _placeholders.resolve(template, params)


# --------------------------------------------------------------------- runtime API
def add_external_id(external_id: str) -> None:
    """Pin the external id of the CURRENT invocation; wins over any declarative id. The
    only way to give a dynamic test a stable id."""
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.external_id = external_id


def add_display_name(name: str) -> None:
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.display_name = name


def add_title(value: str) -> None:
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.title = value


def add_description(value: str) -> None:
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.description = value


def add_message(message: Optional[str]) -> None:
    """A note on the innermost open step, or on the test when no step is open."""
    ctx = _DoqaContexts.current()
    if ctx is None or message is None:
        return
    if ctx.step_stack:
        node = ctx.step_stack[-1]
        node.message = message if node.message is None else node.message + "\n" + message
    else:
        ctx.messages.append(message)


def add_parameter(name: str, value: Any) -> None:
    """The value is kept as is (not stringified): typed values travel to the wire intact."""
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.parameters.append([name, value])


def add_labels(*labels: str) -> None:
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.labels.extend(labels)


def add_label(name: Optional[str], value: Optional[str]) -> None:
    ctx = _DoqaContexts.current()
    if ctx is not None and name is not None and value is not None:
        ctx.labels.append(name + ":" + value)


def add_tags(*tags: str) -> None:
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.tags.extend(tags)


def add_link(url: str, type: Optional[str] = None,  # noqa: A002 - facade name
             title: Optional[str] = None, description: Optional[str] = None) -> None:
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.links.append(_Link(url, _LinkType.parse(type), title, description))


def add_links(*links: Optional[_Link]) -> None:
    ctx = _DoqaContexts.current()
    if ctx is None:
        return
    for item in links:
        if item is not None:
            ctx.links.append(item)


def add_case_ids(*ids: int) -> None:
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.case_ids.extend(int(i) for i in ids)


def add_create_manual_case() -> None:
    """Additive with the decorator: any source turns the flag on, nothing turns it off."""
    ctx = _DoqaContexts.current()
    if ctx is not None:
        ctx.create_manual_case = True


def attach(body: Union[bytes, str, None], name: Optional[str] = None,
           content_type: Optional[str] = None, kind: Any = None) -> None:
    """Attach in-memory content to the open step (or the test when no step is open)."""
    ctx = _DoqaContexts.current()
    if ctx is None:
        return
    if isinstance(body, str):
        data: Optional[bytes] = body.encode("utf-8")
        if content_type is None and kind is None:
            content_type = "text/plain"
    else:
        data = body
    _attachment_target(ctx).append(
        _AttachmentRef.of_bytes(name, data, _effective_content_type(content_type, kind)))


def attach_file(path: str, name: Optional[str] = None,
                content_type: Optional[str] = None, kind: Any = None) -> None:
    """Attach a file by path; with an explicit name/content type the content is captured
    at call time."""
    ctx = _DoqaContexts.current()
    if ctx is None or path is None:
        return
    effective_type = _effective_content_type(content_type, kind)
    if name is None and effective_type is None:
        _attachment_target(ctx).append(_AttachmentRef.of_path(path))
        return
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return
    _attachment_target(ctx).append(_AttachmentRef.of_bytes(name, data, effective_type))


def _attachment_target(ctx):
    return ctx.step_stack[-1].attachments if ctx.step_stack else ctx.attachments


def _effective_content_type(content_type: Optional[str], kind: Any) -> Optional[str]:
    if content_type is not None:
        return content_type
    if kind is None:
        return None
    mime = getattr(kind, "mime_type", None)
    if mime is not None:
        return str(mime)
    return str(kind)


# --------------------------------------------------------------------- context transfer
class Context:
    """Opaque handle for carrying the test context into a spawned thread."""

    def __init__(self, ctx) -> None:
        self._ctx = ctx


def capture_context() -> Context:
    return Context(_DoqaContexts.current())


def run_with(context: Optional[Context], body: Callable[[], Any]) -> Any:
    """Run ``body`` under the captured context; steps and attachments from foreign threads
    are silently lost without this transfer (thread-local scoping).

    One thread at a time per context: the step stack behind the handle is shared, so
    concurrent bodies under the same context interleave their steps into one tree."""
    previous = _DoqaContexts.push(context._ctx if context is not None else None)
    try:
        return body()
    finally:
        _DoqaContexts.restore(previous)
