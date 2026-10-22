"""Selective mode-0 plan: membership and ordering in TestRef terms, always fail-open -
a broken adapter has no right to throw a test out of the run."""

import logging
import sys
import threading
from typing import Dict, Optional

from . import attribution, placeholders
from .runtime import AdapterRuntime
from .session import DoqaSession
from .test_ref import TestRef

LOG = logging.getLogger(__name__)

_index_lock = threading.Lock()
_plan_index_cache: Dict[str, int] = {}


def active() -> bool:
    """True when the run selected a subset - deselection is meaningful."""
    return _plan() is not None


def has_plan() -> bool:
    """Selection is active AND the server provided an order."""
    plan = _plan()
    return plan is not None and bool(plan.selected_order)


def allows(ref: TestRef, params: Optional[Dict[str, str]] = None) -> bool:
    plan = _plan()
    if plan is None:
        return True
    try:
        external_id = attribution.resolve(ref).external_id
        if placeholders.has_placeholder(external_id):
            substituted = placeholders.resolve(external_id, params or {})
            if not placeholders.has_placeholder(substituted):
                return plan.allows(substituted)
            # nothing to substitute with: the test is included when ANY selected id matches
            # the template's wildcard form, and the exact id is re-checked at report time
            pattern = placeholders.template_to_regex(external_id)
            return any(pattern.fullmatch(selected) is not None
                       for selected in plan.selected_external_ids)
        return plan.allows(external_id)
    except Exception:
        LOG.warning("DoQA %s: selection failed to resolve, keeping the test",
                    AdapterRuntime.framework())
        return True


def plan_index(ref: TestRef) -> int:
    """0-based plan position for stable sorting; no plan / not in the plan / any error ->
    ``sys.maxsize`` (a stable sort keeps such tests at the tail in original order)."""
    plan = _plan()
    if plan is None or not plan.selected_order:
        return sys.maxsize
    try:
        cache_key = None
        if ref.test_method is not None and ref.fqcn:
            # keyed by the EXECUTING container, not the function: an inherited test is one
            # function executed by several classes, each with its own plan position
            cache_key = (ref.fqcn + "#" + (ref.method_name or "")
                         + "(" + (ref.method_param_types or "") + ")")
            with _index_lock:
                cached = _plan_index_cache.get(cache_key)
            if cached is not None:
                return cached
        index = _resolve_index(plan, ref)
        if cache_key is not None:
            with _index_lock:
                _plan_index_cache[cache_key] = index
        return index
    except Exception:
        return sys.maxsize


def reset() -> None:
    """Run boundary: a long-lived process may execute a different plan next."""
    with _index_lock:
        _plan_index_cache.clear()


def _resolve_index(plan, ref: TestRef) -> int:
    external_id = attribution.resolve(ref).external_id
    if placeholders.has_placeholder(external_id):
        pattern = placeholders.template_to_regex(external_id)
        for i, selected in enumerate(plan.selected_order):
            if pattern.fullmatch(selected) is not None:
                return i
        return sys.maxsize
    return plan.order_index(external_id)


def _plan():
    if not DoqaSession.discovery_selection_active():
        return None
    try:
        session = DoqaSession.get_or_init()
    except Exception:
        LOG.warning("DoQA %s: init failed, running without selection",
                    AdapterRuntime.framework())
        return None
    if (not session.enabled or session.run_context is None
            or session.run_context.selected_external_ids is None):
        return None
    return session.run_context
