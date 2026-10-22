"""Per-test runtime contexts: registry by unique id plus a thread-local pointer."""

import threading
import time
from typing import Any, Dict, List, Optional


class AttachmentRef:
    """A pending attachment: a path or in-memory bytes; ``uploaded_id`` memoizes the upload
    so a class-fixture attachment merged into N results is uploaded once."""

    def __init__(self, path: Optional[str] = None, name: Optional[str] = None,
                 content: Optional[bytes] = None, content_type: Optional[str] = None):
        self.path = path
        self.name = name
        self.content = content
        self.content_type = content_type
        self.uploaded_id: Optional[str] = None

    @staticmethod
    def of_path(path: str) -> "AttachmentRef":
        return AttachmentRef(path=path)

    @staticmethod
    def of_bytes(name: Optional[str], content: Optional[bytes],
                 content_type: Optional[str]) -> "AttachmentRef":
        return AttachmentRef(name=name, content=content if content is not None else b"",
                             content_type=content_type)

    def __str__(self) -> str:
        return self.path if self.path is not None else str(self.name)


class StepNode:
    """One executed step; ``children`` nest, ``outcome`` is a wire status or None."""

    def __init__(self, title: Optional[str], description: Optional[str] = None):
        self.title = title
        self.description = description
        #: definition title when it differs from the executed one (Outline keeps ``<param>``)
        self.definition_title: Optional[str] = None
        self.outcome: Optional[str] = None
        self.message: Optional[str] = None
        self.duration_ms: Optional[int] = None
        self.attachments: List[AttachmentRef] = []
        self.children: List["StepNode"] = []
        self.start_millis = now_millis()


class Phase:
    SETUP = "setup"
    CALL = "call"
    TEARDOWN = "teardown"


class RuntimeContext:
    """Everything collected for one test."""

    def __init__(self, unique_id: Optional[str]):
        self.unique_id = unique_id
        self.test_ref = None
        self.closed = False
        self.external_id: Optional[str] = None
        self.title: Optional[str] = None
        self.display_name: Optional[str] = None
        self.description: Optional[str] = None
        self.create_manual_case = False
        self.labels: List[str] = []
        self.tags: List[str] = []
        self.links: List[Any] = []
        self.case_ids: List[int] = []
        #: runtime add_parameter pairs [name, value]; values kept AS IS (not stringified)
        self.parameters: List[List[Any]] = []
        #: [name, stringified value] pairs captured from a parameterized invocation
        self.invocation_parameters: List[List[str]] = []
        self.attachments: List[AttachmentRef] = []
        self.messages: List[str] = []
        self.setup_steps: List[StepNode] = []
        self.call_steps: List[StepNode] = []
        self.teardown_steps: List[StepNode] = []
        self.step_stack: List[StepNode] = []
        # default CALL: without phase-driving glue, body steps still land in the call bucket
        self.phase = Phase.CALL
        self.t_start = now_millis()
        self.t_end = 0

    def phase_bucket(self) -> List[StepNode]:
        if self.phase == Phase.TEARDOWN:
            return self.teardown_steps
        if self.phase == Phase.SETUP:
            return self.setup_steps
        return self.call_steps


class DoqaContexts:
    """``unique_id -> RuntimeContext`` registry + the thread-local current pointer read by
    the ``doqa.*`` runtime facade."""

    _by_uid: Dict[str, RuntimeContext] = {}
    _lock = threading.Lock()
    _local = threading.local()

    @classmethod
    def open(cls, unique_id: str) -> RuntimeContext:
        ctx = RuntimeContext(unique_id)
        with cls._lock:
            cls._by_uid[unique_id] = ctx
        cls._local.current = ctx
        return ctx

    @classmethod
    def bind(cls, unique_id: str) -> Optional[RuntimeContext]:
        """Re-point the thread-local on the executing thread (which may differ from the one
        that opened the context). No fallback to the current thread-local - in a parallel
        run that would attribute steps to a foreign test."""
        with cls._lock:
            ctx = cls._by_uid.get(unique_id)
        if ctx is None or ctx.closed:
            return None
        cls._local.current = ctx
        return ctx

    @classmethod
    def current(cls) -> Optional[RuntimeContext]:
        ctx = getattr(cls._local, "current", None)
        if ctx is not None and ctx.closed:
            # a stale pool-thread pointer must not write into an already reported context
            cls._local.current = None
            return None
        return ctx

    @classmethod
    def remove(cls, unique_id: str) -> Optional[RuntimeContext]:
        with cls._lock:
            ctx = cls._by_uid.pop(unique_id, None)
        if ctx is not None:
            ctx.closed = True
        if getattr(cls._local, "current", None) is ctx:
            cls._local.current = None
        return ctx

    @classmethod
    def push(cls, ctx: Optional[RuntimeContext]) -> Optional[RuntimeContext]:
        """Temporarily bind a transient (unregistered) context: class fixtures and
        ``doqa.run_with``. Returns the previous binding."""
        previous = getattr(cls._local, "current", None)
        cls._local.current = ctx
        return previous

    @classmethod
    def restore(cls, previous: Optional[RuntimeContext]) -> None:
        cls._local.current = previous

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._by_uid.clear()
        cls._local.current = None


def now_millis() -> int:
    return int(time.time() * 1000)
