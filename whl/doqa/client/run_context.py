"""Resolved run to report into, honouring ``adapter_mode``.

* ``MODE_NEW_RUN`` - create a fresh test run; report ALL results.
* ``MODE_SELECTIVE`` - existing ``test_run_id`` + its SELECTIVE autotest list; only those
  external ids are in scope (the adapter deselects the rest).
* ``MODE_EXISTING_RUN`` - existing ``test_run_id``; report ALL results (no selection).
"""

import logging
import sys
import uuid
from typing import Dict, List, Optional, Set

from .api import ApiClient, ApiError
from .config import DoqaConfig

LOG = logging.getLogger(__name__)


class RunContext:

    def __init__(self, run_id: str, configuration_id: Optional[str], mode: int,
                 selected_external_ids: Optional[Set[str]],
                 selected_order: Optional[List[str]] = None):
        self.run_id = run_id
        self.configuration_id = configuration_id
        self.mode = mode
        #: None => everything allowed; a set => only these ids are in scope (mode 0).
        self.selected_external_ids = selected_external_ids
        #: Raw SERVER order of the selective list (mode 0), or None when there is no plan.
        self.selected_order = None if selected_order is None else list(selected_order)
        self._order_index: Dict[str, int] = {}
        if self.selected_order is not None:
            for i, external_id in enumerate(self.selected_order):
                self._order_index.setdefault(external_id, i)

    def allows(self, external_id: str) -> bool:
        """Membership is decided by the set alone; ``selected_order`` is not an authority."""
        return self.selected_external_ids is None or external_id in self.selected_external_ids

    def order_index(self, external_id: str) -> int:
        """Plan position (0-based, first occurrence wins). Not in the plan (or no plan) ->
        ``sys.maxsize``: stable sorts keep such items at the tail in their original relative
        order - order never changes the SET of tests, only the sequence."""
        return self._order_index.get(external_id, sys.maxsize)

    @staticmethod
    def establish(client: ApiClient, config: DoqaConfig) -> "RunContext":
        mode = _effective_mode(config)
        conf_id = config.configuration_id

        if mode == DoqaConfig.MODE_NEW_RUN:
            # A per-process external_key makes the create idempotent server-side, so the
            # client may safely retry it - a lost response must not leave two runs behind.
            run_id = client.create_test_run(config.test_run_name, conf_id,
                                            "doqa-client-" + str(uuid.uuid4()))
            if not run_id:
                # 2xx without a runId means contract drift (or a proxy ate the body);
                # fail fast so the session disables cleanly instead of uploading results
                # without a test_run_id.
                raise ApiError("test-run create returned no runId - check the DoQA "
                               "url/response")
            return RunContext(run_id, conf_id, DoqaConfig.MODE_NEW_RUN, None)
        if mode == DoqaConfig.MODE_SELECTIVE:
            run_id = _require_run_id(config, DoqaConfig.MODE_SELECTIVE)
            selected = client.get_run_autotests(run_id, conf_id)
            return RunContext(run_id, conf_id, DoqaConfig.MODE_SELECTIVE,
                              set(selected), selected)
        return RunContext(_require_run_id(config, DoqaConfig.MODE_EXISTING_RUN), conf_id,
                          DoqaConfig.MODE_EXISTING_RUN, None)


def _effective_mode(config: DoqaConfig) -> int:
    # A configured testRunId with no explicit adapterMode means "report into that run" -
    # otherwise the default mode would silently ignore the id and send results into a
    # freshly created run.
    has_run_id = bool(config.test_run_id and config.test_run_id.strip())
    if has_run_id and not config.adapter_mode_explicit:
        return DoqaConfig.MODE_EXISTING_RUN
    if has_run_id and config.adapter_mode == DoqaConfig.MODE_NEW_RUN:
        LOG.warning("DoQA: adapterMode=2 creates a NEW run - the configured testRunId %s is "
                    "ignored (use adapterMode=1 to report into it)", config.test_run_id)
    return config.adapter_mode


def _require_run_id(config: DoqaConfig, mode: int) -> str:
    # modes 0/1 report into a pre-existing run - a missing testRunId means every upload
    # would 4xx; fail fast here so the session disables cleanly instead.
    run_id = config.test_run_id
    if not run_id or not run_id.strip():
        raise ApiError("adapterMode " + str(mode)
                       + " requires testRunId, but none was configured")
    return run_id
