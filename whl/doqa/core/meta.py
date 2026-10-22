"""Metadata collected from ``doqa.*`` decorators, with the Allure migration bridge on top.

Scalars follow "method overrides class"; labels/tags/links accumulate (class first, then
method). Class-level markers are visible to subclasses: the lookup walks the MRO per key,
the closest declaration wins.
"""

import logging
from typing import Any, Dict, List, Optional, Set

from doqa.client.models import Link
from doqa.client.outcomes import LinkType
from . import allure_compat

LOG = logging.getLogger(__name__)

META_ATTRIBUTE = "__doqa_meta__"

_SCALARS = ("display_name", "title", "description", "namespace", "classname")
_WARNED_LINK_TYPES: Set[str] = set()


class Meta:
    def __init__(self) -> None:
        self.display_name: Optional[str] = None
        self.title: Optional[str] = None
        self.description: Optional[str] = None
        self.namespace: Optional[str] = None
        self.classname: Optional[str] = None
        self.create_manual_case = False
        self.labels: List[str] = []
        self.tags: List[str] = []
        self.links: List[Link] = []


def read(ref) -> Meta:
    meta = Meta()
    _collect(meta, ref.test_class if ref is not None else None)
    _collect(meta, ref.test_method if ref is not None else None)
    allure_compat.fill(meta, ref.test_class if ref is not None else None)
    allure_compat.fill(meta, ref.test_method if ref is not None else None)
    return meta


def own_meta(element: Any) -> Optional[Dict[str, Any]]:
    """The ``__doqa_meta__`` dict declared directly on ``element`` (no inheritance)."""
    if element is None:
        return None
    if isinstance(element, type):
        return vars(element).get(META_ATTRIBUTE)
    return getattr(element, META_ATTRIBUTE, None)


def meta_value(element: Any, key: str) -> Any:
    """Per-key lookup; for classes the MRO is walked so base-class markers reach
    subclasses, the closest declaration winning."""
    if element is None:
        return None
    if isinstance(element, type):
        for klass in element.__mro__:
            declared = vars(klass).get(META_ATTRIBUTE)
            if declared is not None and key in declared:
                return declared[key]
        return None
    declared = getattr(element, META_ATTRIBUTE, None)
    return declared.get(key) if declared is not None else None


def _collect(meta: Meta, element: Any) -> None:
    if element is None:
        return
    for scalar in _SCALARS:
        value = meta_value(element, scalar)
        if value is not None:
            setattr(meta, scalar, value)
    if meta_value(element, "create_manual_case"):
        meta.create_manual_case = True
    labels = meta_value(element, "labels")
    if labels:
        meta.labels.extend(labels)
    tags = meta_value(element, "tags")
    if tags:
        meta.tags.extend(tags)
    # links are not inherited from base classes (mirrors the reference behavior)
    declared = own_meta(element)
    for raw in (declared.get("links") if declared else None) or []:
        meta.links.append(_to_link(raw))


def _to_link(raw: Dict[str, Any]) -> Link:
    raw_type = raw.get("type") or None
    parsed = LinkType.parse(raw_type)
    if raw_type is not None and parsed is None and raw_type not in _WARNED_LINK_TYPES:
        # the typo'd type must not travel to the wire; the link itself is kept
        _WARNED_LINK_TYPES.add(raw_type)
        LOG.warning('DoQA: unknown link type "%s" - link kept without a type '
                    "(valid: related|defect|requirement|blocked_by|repository)", raw_type)
    return Link(raw.get("url"), parsed, raw.get("title") or None,
                raw.get("description") or None)
