"""Wire model of the Direct Autotest API.

All payloads are insertion-ordered dicts with snake_case keys. :func:`compact` drops
``None``, empty strings and empty collections but keeps falsy scalars (``0``, ``False``) -
that is the wire form the API contract expects. :class:`Parameter` and :class:`Attachment`
payloads are deliberately NOT compacted: a parameter value of ``""``, ``0`` or ``None`` is
legitimate data.
"""

from typing import Any, Dict, Iterable, List, Optional


def compact(payload: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, (str, list, tuple, dict)) and len(value) == 0:
            continue
        out[key] = value
    return out


def put_if_present(body: Dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        body[key] = value


class Link:
    def __init__(self, url: str, type: Optional[str] = None,  # noqa: A002 - wire name
                 title: Optional[str] = None, description: Optional[str] = None):
        self.url = url
        self.type = type
        self.title = title
        self.description = description

    def to_payload(self) -> Dict[str, Any]:
        return compact({
            "url": self.url,
            "type": self.type,
            "title": self.title,
            "description": self.description,
        })


class Attachment:
    def __init__(self, media_file_id: str):
        self.media_file_id = media_file_id

    def to_payload(self) -> Dict[str, Any]:
        return {"media_file_id": self.media_file_id}


class Parameter:
    def __init__(self, name: str, value: Any):
        self.name = name
        self.value = value

    def to_payload(self) -> Dict[str, Any]:
        return {"name": self.name, "value": self.value}


class Step:
    """Definition step (upsert payload), recursive."""

    def __init__(self, title: str, description: Optional[str] = None,
                 kind: Optional[str] = None, steps: Optional[List["Step"]] = None):
        self.title = title
        self.description = description
        self.kind = kind
        self.steps = list(steps) if steps else []

    @staticmethod
    def of(title: str) -> "Step":
        return Step(title)

    def to_payload(self) -> Dict[str, Any]:
        return compact({
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "steps": [s.to_payload() for s in self.steps],
        })


class StepResult:
    """Executed step (results payload), recursive."""

    def __init__(self, title: Optional[str], outcome: Optional[str],
                 duration_ms: Optional[int] = None, message: Optional[str] = None,
                 attachments: Optional[List[Attachment]] = None,
                 steps: Optional[List["StepResult"]] = None):
        self.title = title
        self.outcome = outcome
        self.duration_ms = duration_ms
        self.message = message
        self.attachments = list(attachments) if attachments else []
        self.steps = list(steps) if steps else []
        self.started_on: Optional[int] = None
        self.completed_on: Optional[int] = None

    def timing(self, started_on: Optional[int],
               completed_on: Optional[int]) -> "StepResult":
        self.started_on = started_on
        self.completed_on = completed_on
        return self

    def to_payload(self) -> Dict[str, Any]:
        # started_on/completed_on feed the file sink (Allure start/stop); the API sink
        # tolerates the extra keys (the backend reads only the ones it knows).
        return compact({
            "title": self.title,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "started_on": self.started_on,
            "completed_on": self.completed_on,
            "message": self.message,
            "attachments": [a.to_payload() for a in self.attachments],
            "steps": [s.to_payload() for s in self.steps],
        })


class AutotestDef:
    """Autotest definition (element of the upsert body)."""

    def __init__(self, external_id: str, name: Optional[str] = None):
        self.external_id = external_id
        self.name = name
        self.title: Optional[str] = None
        self.description: Optional[str] = None
        self.namespace: Optional[str] = None
        self.classname: Optional[str] = None
        self.runner_method: Optional[str] = None
        # native allure id: DoQA links the manual case with this id 1:1 (the AS_ID label in file mode)
        self.allure_id: Optional[str] = None
        self.labels: List[str] = []
        self.tags: List[str] = []
        self.links: List[Link] = []
        self.steps: List[Step] = []
        self.case_ids: List[int] = []

    def to_payload(self) -> Dict[str, Any]:
        return compact({
            "external_id": self.external_id,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "namespace": self.namespace,
            "classname": self.classname,
            "runner_method": self.runner_method,
            "allure_id": self.allure_id,
            "labels": list(self.labels),
            "tags": list(self.tags),
            "links": [l.to_payload() for l in self.links],
            "steps": [s.to_payload() for s in self.steps],
            "case_ids": list(self.case_ids),
        })


def run_properties(framework_label: Optional[str], namespace: Optional[str],
                   classname: Optional[str]) -> List[Parameter]:
    """Run labels shared by the file sink and Direct API; blank values are skipped."""
    pairs = (("framework", framework_label), ("language", "python"), ("package", namespace),
             ("testClass", classname), ("suite", classname))
    return [Parameter(name, value) for name, value in pairs if value]


class AutotestResult:
    """Executed autotest (element of the results body)."""

    def __init__(self, external_id: str, outcome: str):
        self.external_id = external_id
        self.outcome = outcome
        self.name: Optional[str] = None
        self.started_on: Optional[int] = None
        self.completed_on: Optional[int] = None
        self.duration_ms: Optional[int] = None
        self.message: Optional[str] = None
        self.traces: Optional[str] = None
        self.parameters: List[Parameter] = []
        self.step_results: List[StepResult] = []
        self.setup_results: List[StepResult] = []
        self.teardown_results: List[StepResult] = []
        self.attachments: List[Attachment] = []
        self.links: List[Link] = []
        self.properties: List[Parameter] = []
        self.create_manual_case = False

    def prepend_setup_results(self, results: Iterable[StepResult]) -> None:
        """Insert at the FRONT: class-level fixtures go before per-test ones."""
        self.setup_results[:0] = list(results)

    def extend_teardown_results(self, results: Iterable[StepResult]) -> None:
        self.teardown_results.extend(results)

    def to_payload(self) -> Dict[str, Any]:
        payload = compact({
            "external_id": self.external_id,
            "outcome": self.outcome,
            "name": self.name,
            "started_on": self.started_on,
            "completed_on": self.completed_on,
            "duration_ms": self.duration_ms,
            "message": self.message,
            "traces": self.traces,
            # parameters must be a LIST of {name, value} pairs, never a map: the server
            # camelCases object keys on intake and would distort parameter names.
            "parameters": [p.to_payload() for p in self.parameters],
            "step_results": [s.to_payload() for s in self.step_results],
            "setup_results": [s.to_payload() for s in self.setup_results],
            "teardown_results": [s.to_payload() for s in self.teardown_results],
            "attachments": [a.to_payload() for a in self.attachments],
            "links": [l.to_payload() for l in self.links],
            "properties": [p.to_payload() for p in self.properties],
        })
        if self.create_manual_case:
            payload["create_manual_case"] = True
        return payload
