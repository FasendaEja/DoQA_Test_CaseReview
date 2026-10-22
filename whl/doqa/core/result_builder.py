"""Final projection: runtime context + outcome -> AutotestDef (upsert) + AutotestResult."""

import re
from typing import Any, Callable, Dict, List, Optional

from doqa.client.models import (Attachment, AutotestDef, AutotestResult, Link, Parameter,
                                Step, StepResult, run_properties)
from doqa.client.outcomes import StepKind
from . import limits, meta as meta_reader, attribution, placeholders
from .contexts import AttachmentRef, RuntimeContext, StepNode
from .runtime import AdapterRuntime

#: Parses a framework-generated invocation display name like ``[1] a=b``.
PARAM_DISPLAY = re.compile(r"^\[(\d+)\]\s*(.*)$")

AttachmentUploader = Callable[[AttachmentRef], Optional[str]]
IdGate = Callable[[str], bool]


class Built:
    def __init__(self, definition: AutotestDef, result: AutotestResult,
                 full_name: Optional[str], allure_id: Optional[str],
                 class_key: Optional[str], method_key: Optional[str]):
        self.definition = definition
        self.result = result
        self.full_name = full_name
        self.allure_id = allure_id
        self.class_key = class_key
        self.method_key = method_key


def build(ctx: RuntimeContext, outcome: str, message: Optional[str],
          traces: Optional[str], uploader: Optional[AttachmentUploader],
          gate: Optional[IdGate], config) -> Optional[Built]:
    ref = ctx.test_ref
    meta = meta_reader.read(ref)
    attr = attribution.resolve(ref)
    params = placeholders.params_of(ctx)

    # the runtime override (doqa.add_external_id) beats any declarative id; placeholders
    # substitute BEFORE the id is used for identity or selection
    external_id = placeholders.resolve(
        ctx.external_id if ctx.external_id is not None else attr.external_id, params)
    if gate is not None and not gate(external_id):
        # an out-of-scope test builds nothing and uploads nothing
        return None

    def resolve(value: Optional[str]) -> Optional[str]:
        return placeholders.resolve(value, params)

    markup = getattr(ref, "markup", None)
    explicit_name = _first_non_blank(ctx.display_name, meta.display_name)
    fallback_name = ref.display_name if ref else None
    full_name = ref.full_name() if ref else None
    name = resolve(_first_non_blank(explicit_name, markup.name if markup else None,
                                    fallback_name, full_name))
    definition_name = resolve(_first_non_blank(
        explicit_name, markup.definition_name if markup else None, fallback_name, full_name))
    title = resolve(_first_non_blank(ctx.title, meta.title))
    description = resolve(_first_non_blank(ctx.description, meta.description))
    namespace = resolve(_first_non_blank(meta.namespace,
                                         ref.package_name() if ref else None))
    classname = resolve(_first_non_blank(meta.classname,
                                         ref.simple_class_name() if ref else None))
    labels = placeholders.resolve_all(_dedup(meta.labels + ctx.labels), params)
    tags = placeholders.resolve_all(_dedup(meta.tags + ctx.tags), params)
    # declarative links get placeholder substitution; runtime links travel verbatim
    links: List[Link] = [Link(resolve(l.url), l.type, resolve(l.title),
                              resolve(l.description)) for l in meta.links]
    links.extend(ctx.links)
    case_ids = _dedup(list(ctx.case_ids) + list(attr.case_ids or []))

    max_message = config.max_message_length if config else limits.max_message_length()
    max_trace = config.max_trace_length if config else limits.max_trace_length()
    max_parameter = config.max_parameter_length if config else limits.max_parameter_length()

    definition = AutotestDef(external_id, limits.clip(definition_name, limits.MAX_NAME_LENGTH))
    definition.title = title
    definition.description = description
    definition.namespace = namespace
    definition.classname = classname
    # the raw method name, never the display name: DoQA derives its native selective-launch
    # filter from it, and a custom display name must not hide the real token
    definition.runner_method = limits.clip(ref.method_name if ref else None,
                                           limits.MAX_NAME_LENGTH)
    definition.labels = labels
    definition.tags = tags
    definition.links = list(links)
    definition.steps = ([_to_def_step(n, StepKind.BEFORE) for n in ctx.setup_steps]
                        + [_to_def_step(n, StepKind.STEP) for n in ctx.call_steps]
                        + [_to_def_step(n, StepKind.AFTER) for n in ctx.teardown_steps])
    definition.case_ids = case_ids
    definition.allure_id = attr.allure_id

    final_message = message
    if final_message is None and ctx.messages:
        final_message = "\n".join(ctx.messages)

    result = AutotestResult(external_id, outcome)
    result.name = limits.clip(name, limits.MAX_NAME_LENGTH)
    if ctx.t_start > 0:
        result.started_on = ctx.t_start
    if ctx.t_end > 0:
        result.completed_on = ctx.t_end
    if ctx.t_start > 0 and ctx.t_end > 0:
        result.duration_ms = ctx.t_end - ctx.t_start
    result.message = limits.truncate(final_message, max_message)
    result.traces = limits.truncate(traces, max_trace)
    result.parameters = _build_parameters(ctx, max_parameter)
    result.step_results = [to_result_step(n, uploader) for n in ctx.call_steps]
    result.setup_results = [to_result_step(n, uploader) for n in ctx.setup_steps]
    result.teardown_results = [to_result_step(n, uploader) for n in ctx.teardown_steps]
    result.attachments = _upload_all(ctx.attachments, uploader)
    result.links = list(links)
    result.properties = run_properties(AdapterRuntime.framework_label(), namespace, classname)
    # additive opt-in; travels only in the result, never in the def
    result.create_manual_case = meta.create_manual_case or ctx.create_manual_case

    return Built(definition, result,
                 ref.full_name() if ref else None, attr.allure_id,
                 ref.fqcn if ref else None, ref.method_key() if ref else None)


def to_result_step(node: StepNode, uploader: Optional[AttachmentUploader]) -> StepResult:
    step = StepResult(limits.clip(node.title, limits.MAX_STEP_TITLE_LENGTH),
                      node.outcome if node.outcome else "passed",
                      node.duration_ms,
                      limits.truncate(node.message, limits.max_message_length()),
                      _upload_all(node.attachments, uploader),
                      [to_result_step(child, uploader) for child in node.children])
    if node.start_millis > 0:
        step.started_on = node.start_millis
        if node.duration_ms is not None:
            step.completed_on = node.start_millis + node.duration_ms
    return step


def _to_def_step(node: StepNode, kind: Optional[str]) -> Step:
    # kind is meaningful only on the top level; children carry None
    title = node.definition_title if node.definition_title is not None else node.title
    return Step(limits.clip(title, limits.MAX_STEP_TITLE_LENGTH), node.description, kind,
                [_to_def_step(child, None) for child in node.children])


def _build_parameters(ctx: RuntimeContext, max_parameter: int) -> List[Parameter]:
    out: List[Parameter] = []
    if ctx.invocation_parameters:
        for name, value in ctx.invocation_parameters:
            out.append(Parameter(str(name), limits.truncate(str(value), max_parameter)))
    elif ctx.test_ref is not None and ctx.test_ref.parameterized:
        # no invocation capture: fall back to the framework display name "[1] a=b"
        match = PARAM_DISPLAY.match(ctx.test_ref.display_name or "")
        if match and match.group(2).strip():
            out.append(Parameter("arguments",
                                 limits.truncate(match.group(2).strip(), max_parameter)))
    for name, value in ctx.parameters:
        # a string value is truncated; a non-string one travels in the payload typed, as is
        if isinstance(value, str):
            out.append(Parameter(str(name), limits.truncate(value, max_parameter)))
        else:
            out.append(Parameter(str(name), value))
    return out


def _upload_all(refs: List[AttachmentRef],
                uploader: Optional[AttachmentUploader]) -> List[Attachment]:
    out: List[Attachment] = []
    for ref in refs:
        media_file_id = _upload(ref, uploader)
        if media_file_id is not None:
            out.append(Attachment(media_file_id))
    return out


def _upload(ref: AttachmentRef, uploader: Optional[AttachmentUploader]) -> Optional[str]:
    # memoized: a class-fixture attachment merged into every test of the class is
    # uploaded exactly once
    if ref.uploaded_id is not None:
        return ref.uploaded_id
    if uploader is None:
        return None
    media_file_id = uploader(ref)
    if media_file_id is not None:
        ref.uploaded_id = media_file_id
    return media_file_id


def _first_non_blank(*candidates: Optional[str]) -> Optional[str]:
    for candidate in candidates:
        if candidate is not None and candidate.strip():
            return candidate
    return None


def _dedup(values: List[Any]) -> List[Any]:
    seen: Dict[Any, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)
