"""Framework-agnostic reporting core shared by DoQA adapters."""

from .contexts import AttachmentRef, DoqaContexts, Phase, RuntimeContext, StepNode
from .meta import Meta
from .result_builder import Built
from .runtime import AdapterRuntime
from .session import DoqaSession
from .test_ref import TestRef

__all__ = [
    "AdapterRuntime",
    "AttachmentRef",
    "Built",
    "DoqaContexts",
    "DoqaSession",
    "Meta",
    "Phase",
    "RuntimeContext",
    "StepNode",
    "TestRef",
]
