"""Migration bridge for common Allure markup, read by MARK NAMES - no allure import.

Recognized surface: ``pytest.mark.allure_label`` (label_type epic/feature/story/severity/
owner and the ``as_id`` id), ``pytest.mark.allure_link`` (link_type link/issue/tms) and
``pytest.mark.allure_description``. Everything is additive: ``doqa.*`` values always win
(description is only filled when still empty).
"""

from typing import Any, List, Optional

from doqa.client.models import Link
from doqa.client.outcomes import LinkType

_LABEL_KEYS = {"epic": "epic", "feature": "feature", "story": "story",
               "owner": "owner", "severity": "severity"}


def fill(meta, element: Any) -> None:
    if element is None:
        return
    for mark in _marks(element):
        if mark.name == "allure_label":
            label_type = mark.kwargs.get("label_type")
            key = _LABEL_KEYS.get(str(label_type)) if label_type is not None else None
            if key is None:
                continue
            for value in mark.args:
                text = _text(value)
                if text:
                    label = key + ":" + (text.lower() if key == "severity" else text)
                    meta.labels.append(label)
        elif mark.name == "allure_link":
            link = _to_link(mark)
            if link is not None:
                meta.links.append(link)
        elif mark.name == "allure_description":
            if meta.description is None and mark.args:
                text = _text(mark.args[0])
                if text:
                    meta.description = text


def allure_id(element: Any) -> Optional[str]:
    """Raw ``allure.id`` value (an ``as_id`` label mark), or None."""
    if element is None:
        return None
    for mark in _marks(element):
        if (mark.name == "allure_label" and mark.kwargs.get("label_type") == "as_id"
                and mark.args):
            text = _text(mark.args[0])
            if text:
                return text
    return None


def _to_link(mark) -> Optional[Link]:
    url = _text(mark.args[0]) if mark.args else None
    if not url:
        return None
    link_type = mark.kwargs.get("link_type")
    title = _text(mark.kwargs.get("name")) or None
    if link_type == "issue":
        # an issue mark carries an id, not necessarily a URL - only a literal URL maps
        if not url.startswith(("http://", "https://")):
            return None
        return Link(url, LinkType.DEFECT, title, None)
    if link_type == "tms":
        if not url.startswith(("http://", "https://")):
            return None
        return Link(url, LinkType.REQUIREMENT, title, None)
    return Link(url, LinkType.parse(str(link_type)) if link_type is not None else None,
                title, None)


def _marks(element: Any) -> List[Any]:
    out = []
    marks = getattr(element, "pytestmark", None)
    if isinstance(marks, (list, tuple)):
        for mark in marks:
            if hasattr(mark, "name") and hasattr(mark, "args") and hasattr(mark, "kwargs"):
                out.append(mark)
    return out


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    # severity may arrive as an enum - its value is the wire string
    text = getattr(value, "value", value)
    return str(text)
