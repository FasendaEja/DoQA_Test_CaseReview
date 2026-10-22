"""``{param}`` placeholder substitution and wildcard matching of templated external ids."""

import re
from typing import Any, Dict, List, Optional, Pattern

_PLACEHOLDER = re.compile(r"\{[^{}]*}")
_TEMPLATE_CACHE: Dict[str, Pattern[str]] = {}


def params_of(ctx) -> Dict[str, str]:
    """Ordered name -> string map; invocation parameters win over runtime ones on a name
    clash (first inserted wins)."""
    params: Dict[str, str] = {}
    for name, value in ctx.invocation_parameters:
        params.setdefault(str(name), str(value))
    for name, value in ctx.parameters:
        params.setdefault(str(name), str(value))
    return params


def resolve(value: Optional[str], params: Dict[str, str]) -> Optional[str]:
    """Literal replacement of every ``{key}`` occurrence; unknown placeholders stay in the
    text verbatim. Plain string replace, not regex - values may contain anything."""
    if value is None or not params or "{" not in value:
        return value
    for key, replacement in params.items():
        value = value.replace("{" + key + "}", replacement)
    return value


def resolve_all(values: List[str], params: Dict[str, str]) -> List[str]:
    return [resolve(v, params) for v in values]


def has_placeholder(value: Optional[str]) -> bool:
    return value is not None and _PLACEHOLDER.search(value) is not None


def template_to_regex(template: str) -> Pattern[str]:
    """Wildcard form of a templated external id (``login_{browser}`` matches any
    substituted id, full match); compiled patterns are cached per template."""
    cached = _TEMPLATE_CACHE.get(template)
    if cached is not None:
        return cached
    literals = _PLACEHOLDER.split(template)
    pattern = re.compile(".*".join(re.escape(part) for part in literals))
    _TEMPLATE_CACHE[template] = pattern
    return pattern


def stringify(arg: Any) -> str:
    """``None`` -> ``"null"``; lists/tuples print as ``[a, b]``; everything else via str."""
    if arg is None:
        return "null"
    if isinstance(arg, (list, tuple)):
        return "[" + ", ".join(stringify(el) if isinstance(el, (list, tuple)) else
                               ("null" if el is None else str(el)) for el in arg) + "]"
    return str(arg)
