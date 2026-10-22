"""DoQA markup carried by Gherkin tags: @doqa.id, @DOQA-<n>, @allure.id, @doqa.case."""

import logging
import re
import threading
from typing import Iterable, List, Optional, Sequence, Set

from .attribution import extract_id_in_title
from .test_ref import ExternalMarkup

LOG = logging.getLogger(__name__)

EXPLICIT_ID = "doqa.id"
DOQA_ID = "DOQA"
ALLURE_ID = "allure.id"
CASE = "doqa.case"

_PREFIXED = re.compile(r"^(doqa\.id|doqa\.case|allure\.id)[:=](.*)$", re.IGNORECASE | re.DOTALL)
_DOQA_N = re.compile(r"^DOQA[-:=](\d+)$")


class ServiceTag:
    """One parsed DoQA tag; ``value`` is None when malformed (still a service tag)."""

    __slots__ = ("kind", "value", "raw")

    def __init__(self, kind: str, value: Optional[str], raw: str):
        self.kind = kind
        self.value = value
        self.raw = raw


def parse(tag: str) -> Optional[ServiceTag]:
    text = tag[1:] if tag.startswith("@") else tag
    match = _PREFIXED.match(text)
    if match is not None:
        kind = match.group(1).lower()
        value = match.group(2).strip()
        if kind == CASE:
            return ServiceTag(CASE, value if value.isdigit() else None, text)
        return ServiceTag(kind, value or None, text)
    match = _DOQA_N.match(text)
    if match is not None:
        return ServiceTag(DOQA_ID, "DOQA-" + match.group(1), text)
    return None


def is_service_tag(tag: str) -> bool:
    return parse(tag) is not None


class Level:
    def __init__(self, kind: str, name: Optional[str], tags: Iterable[str],
                 id_in_name: bool = False):
        self.kind = kind
        self.name = name
        self.tags = list(tags)
        self.id_in_name = id_in_name


def resolve(levels: Sequence[Level]) -> ExternalMarkup:
    """``levels`` go from the narrowest (scenario) to the widest (feature)."""
    markup = ExternalMarkup()
    parsed = [(level, _parse_level(level)) for level in levels]
    markup.explicit_id = _narrowest(parsed, EXPLICIT_ID)
    markup.doqa_id = _narrowest(parsed, DOQA_ID)
    markup.allure_id = _narrowest(parsed, ALLURE_ID)
    for level, tags in reversed(parsed):
        for tag in tags:
            if tag.kind == CASE and tag.value is not None:
                case_id = int(tag.value)
                if case_id not in markup.case_ids:
                    markup.case_ids.append(case_id)
    return markup


def _parse_level(level: Level) -> List[ServiceTag]:
    out = []
    for raw in level.tags:
        tag = parse(raw)
        if tag is None:
            continue
        if tag.value is None:
            _warn_once('DoQA: tag "@%s" on %s "%s" is ignored: %s', tag.raw, level.kind,
                       level.name, "the case id must be a number" if tag.kind == CASE
                       else "the value is empty")
            continue
        out.append(tag)
    if level.id_in_name:
        in_name = extract_id_in_title(level.name)
        if in_name is not None:
            out.append(ServiceTag(DOQA_ID, in_name, "[" + in_name + "]"))
    return out


def _narrowest(parsed, kind: str) -> Optional[str]:
    for level, tags in parsed:
        values = [tag for tag in tags if tag.kind == kind]
        if not values:
            continue
        if len({tag.value for tag in values}) > 1:
            _warn_once('DoQA: %s "%s" has %d %s tags (%s) - the first one is used',
                       level.kind, level.name, len(values), _label(kind),
                       ", ".join(tag.raw if tag.raw.startswith("[") else "@" + tag.raw
                                 for tag in values))
        return values[0].value
    return None


def _label(kind: str) -> str:
    return "DOQA-<n>" if kind == DOQA_ID else "@" + kind


_warned: Set[str] = set()
_warned_lock = threading.Lock()


def reset_warnings() -> None:
    with _warned_lock:
        _warned.clear()


def _warn_once(message: str, *args) -> None:
    # a feature-level tag is resolved once per scenario: say it once
    text = message % args
    with _warned_lock:
        if text in _warned:
            return
        _warned.add(text)
    LOG.warning(text)
