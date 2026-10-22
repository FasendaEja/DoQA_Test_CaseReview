"""Small JSON codec wrapper with the client-core wire rules.

Writer: dicts are emitted in insertion order, non-ASCII stays literal (no ``\\u`` escapes),
no pretty-print, and non-finite floats become ``null`` - NaN/Infinity are not valid JSON
literals and one such value must not invalidate the whole payload. Unknown objects are
serialized as their string representation.

Reader: :func:`parse_object` returns the parsed dict, or an empty dict on any parse error
or a non-object document - enough for the small response envelopes (``map``, ``runId``,
``mediaFileId``, ``autotests[]``).
"""

import json
import math
from typing import Any, Dict


def write(value: Any) -> str:
    return json.dumps(_sanitize(value), ensure_ascii=False, separators=(",", ":"),
                      default=str)


def parse(text: str) -> Any:
    return json.loads(text)


def parse_object(text: Any) -> Dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
