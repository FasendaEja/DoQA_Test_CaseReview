"""External-id attribution cascade: explicit id, id-in-title, native Allure id, signature
hash. The fallback id is a deterministic hash - any deviation in the input string breaks
the history of every test."""

import hashlib
import logging
import re
import threading
from typing import List, Optional, Set

from . import allure_compat, limits, meta
from .runtime import AdapterRuntime

LOG = logging.getLogger(__name__)

ID_IN_TITLE = re.compile(r"(?:\[|@)DOQA[-:](\d+)\]?")


class Source:
    EXPLICIT_EXTERNAL_ID = "explicit_external_id"
    ID_IN_TITLE = "id_in_title"
    NATIVE_ALLURE = "native_allure"
    SIGNATURE_HASH = "signature_hash"


class Result:
    def __init__(self, external_id: str, source: str,
                 case_ids: Optional[List[int]], allure_id: Optional[str]):
        self.external_id = external_id
        self.source = source
        self.case_ids = case_ids
        self.allure_id = allure_id


def resolve(ref) -> Result:
    markup = getattr(ref, "markup", None)
    case_ids = _read_case_ids(ref)
    if markup is not None and markup.case_ids:
        case_ids = _merge(case_ids, markup.case_ids)
    raw_allure_id = _read_allure_id(ref)
    if (raw_allure_id is None or not raw_allure_id.strip()) and markup is not None:
        raw_allure_id = markup.allure_id

    # code-level markup first, then the same branch declared outside the code
    explicit = _usable(_read_explicit_id(ref))
    if explicit is None and markup is not None:
        explicit = _usable(markup.explicit_id)
    if explicit is not None:
        return Result(explicit, Source.EXPLICIT_EXTERNAL_ID, case_ids, raw_allure_id)

    in_title = _usable(extract_id_in_title(ref.display_name))
    if in_title is None and markup is not None:
        in_title = _usable(markup.doqa_id)
    if in_title is not None:
        return Result(in_title, Source.ID_IN_TITLE, case_ids, raw_allure_id)

    if raw_allure_id is not None and raw_allure_id.strip():
        allure_external_id = _usable("ALLURE-" + raw_allure_id.strip())
        if allure_external_id is not None:
            return Result(allure_external_id, Source.NATIVE_ALLURE, case_ids, raw_allure_id)

    return Result(fallback_external_id(hash_identity(ref)), Source.SIGNATURE_HASH,
                  case_ids, raw_allure_id)


_warned_long_ids: Set[str] = set()
_warned_lock = threading.Lock()


def _usable(external_id: Optional[str]) -> Optional[str]:
    """The stripped id, or None when blank or over the server limit."""
    if external_id is None or not external_id.strip():
        return None
    external_id = external_id.strip()
    if len(external_id) <= limits.MAX_EXTERNAL_ID_LENGTH:
        return external_id
    with _warned_lock:
        first = external_id not in _warned_long_ids
        _warned_long_ids.add(external_id)
    if first:
        LOG.warning("DoQA: external id is longer than %d characters and is ignored, the next "
                    "id source applies: %s...", limits.MAX_EXTERNAL_ID_LENGTH,
                    external_id[:80])
    return None


def _merge(first: Optional[List[int]], second: List[int]) -> List[int]:
    merged = list(first or [])
    for value in second:
        if value not in merged:
            merged.append(value)
    return merged


def extract_id_in_title(display_name: Optional[str]) -> Optional[str]:
    if display_name is None:
        return None
    match = ID_IN_TITLE.search(display_name)
    return "DOQA-" + match.group(1) if match else None


def hash_identity(ref) -> str:
    """Frozen input of the fallback hash: the allure-style full name without any
    invocation suffix (``tests.test_login.TestLogin#test_ok``). Every invocation of a
    parameterized test collapses into one method-level id - the arguments travel in the
    result ``parameters`` instead. Other DoQA components derive the same id from the
    same full name, so the construction can never change."""
    return ref.full_name() or ""


def fallback_external_id(signature: Optional[str]) -> str:
    digest = hashlib.sha1((signature or "").encode("utf-8")).hexdigest()
    return AdapterRuntime.framework() + ":" + digest


def _read_explicit_id(ref) -> Optional[str]:
    value = meta.meta_value(ref.test_method, "id")
    if value is None:
        value = meta.meta_value(ref.test_class, "id")
    return value


def _read_case_ids(ref) -> Optional[List[int]]:
    # method first, class only when the method has none - a choice, not a merge
    for element in (ref.test_method, ref.test_class):
        ids = meta.meta_value(element, "case_ids")
        if ids:
            return list(ids)
    return None


def _read_allure_id(ref) -> Optional[str]:
    value = allure_compat.allure_id(ref.test_method)
    if value is None:
        value = allure_compat.allure_id(ref.test_class)
    return value
