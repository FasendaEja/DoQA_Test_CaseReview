"""Resolved DoQA client-core configuration.

See :mod:`doqa.client.resolver` for the full catalog of keys, aliases and environment
variables - that module is the single source of truth for configuration names; this class
only documents semantics.

Run-selection semantics (``adapter_mode``): ``MODE_NEW_RUN`` (default) creates a fresh test
run; ``MODE_EXISTING_RUN`` reports everything into the configured ``test_run_id``;
``MODE_SELECTIVE`` reports into ``test_run_id`` restricted to the run's selective autotest
list. When ``test_run_id`` is configured but ``adapter_mode`` is not,
:meth:`doqa.client.run_context.RunContext.establish` treats it as ``MODE_EXISTING_RUN`` -
an explicitly targeted run must never be silently ignored.
"""

from typing import List, Optional


class DoqaConfig:
    """Immutable resolved configuration; build via :class:`DoqaConfig.Builder`."""

    MODE_SELECTIVE = 0
    MODE_EXISTING_RUN = 1
    MODE_NEW_RUN = 2
    DEFAULT_ADAPTER_MODE = MODE_NEW_RUN

    REPORTING_AUTO = "auto"
    REPORTING_API = "api"
    REPORTING_FILES = "files"
    REPORTING_OFF = "off"
    DEFAULT_RESULTS_DIR = "results"

    DEFAULT_BATCH_SIZE = 100
    DEFAULT_REQUEST_TIMEOUT_MS = 30_000
    DEFAULT_RETRIES = 3
    DEFAULT_RETRY_BACKOFF_MS = 500
    DEFAULT_MAX_TRACE_LENGTH = 100_000
    DEFAULT_MAX_MESSAGE_LENGTH = 10_000
    DEFAULT_MAX_PARAMETER_LENGTH = 2_000

    def __init__(self, builder: "DoqaConfig.Builder"):
        self.url = _blank_to_none(builder._url)
        self.token = _blank_to_none(builder._token)
        self.space_id = _blank_to_none(builder._space_id)
        self.configuration_id = _blank_to_none(builder._configuration_id)
        self.test_run_id = _blank_to_none(builder._test_run_id)
        self.test_run_name = _blank_to_none(builder._test_run_name)
        self.adapter_mode = builder._adapter_mode
        self.adapter_mode_explicit = builder._adapter_mode_explicit
        self.import_realtime = builder._import_realtime
        self.cert_validation = builder._cert_validation
        self.proxy = _blank_to_none(builder._proxy)
        self.reporting = builder._reporting
        self.results_dir = builder._results_dir
        self.environment = _blank_to_none(builder._environment)
        self.pipeline_id = _blank_to_none(builder._pipeline_id)
        self.ci_run_id = _blank_to_none(builder._ci_run_id)
        self.source_key = _blank_to_none(builder._source_key)
        self.correlation_id = _blank_to_none(builder._correlation_id)
        self.branch = _blank_to_none(builder._branch)
        self.batch_size = builder._batch_size
        self.request_timeout_ms = builder._request_timeout_ms
        self.retries = builder._retries
        self.retry_backoff_ms = builder._retry_backoff_ms
        self.max_trace_length = builder._max_trace_length
        self.max_message_length = builder._max_message_length
        self.max_parameter_length = builder._max_parameter_length

    def enabled(self) -> bool:
        """True when url, token and space_id are all present - the minimum to talk to the API."""
        return bool(self.url and self.token and self.space_id)

    def missing_api_settings(self) -> List[str]:
        """Canonical names of absent API settings, in configuration order."""
        missing = []
        if not self.url:
            missing.append("url")
        if not self.token:
            missing.append("token")
        if not self.space_id:
            missing.append("spaceId")
        return missing

    def effective_reporting(self) -> str:
        """Normalized sink: explicit ``api``/``files``/``off`` as is; anything else follows
        the ``auto`` rules (``api`` when :meth:`enabled`, ``files`` otherwise)."""
        raw = self.reporting
        r = "auto" if raw is None else raw.strip().lower()
        if r in (self.REPORTING_API, self.REPORTING_FILES, self.REPORTING_OFF):
            return r
        return self.REPORTING_API if self.enabled() else self.REPORTING_FILES

    @staticmethod
    def resolve() -> "DoqaConfig":
        from .resolver import ConfigResolver
        return ConfigResolver.resolve()

    class Builder:
        def __init__(self) -> None:
            self._url: Optional[str] = None
            self._token: Optional[str] = None
            self._space_id: Optional[str] = None
            self._configuration_id: Optional[str] = None
            self._test_run_id: Optional[str] = None
            self._test_run_name: Optional[str] = None
            self._adapter_mode = DoqaConfig.DEFAULT_ADAPTER_MODE
            self._adapter_mode_explicit = False
            self._import_realtime = False
            self._cert_validation = True
            self._proxy: Optional[str] = None
            self._reporting = DoqaConfig.REPORTING_AUTO
            self._results_dir = DoqaConfig.DEFAULT_RESULTS_DIR
            self._environment: Optional[str] = None
            self._pipeline_id: Optional[str] = None
            self._ci_run_id: Optional[str] = None
            self._source_key: Optional[str] = None
            self._correlation_id: Optional[str] = None
            self._branch: Optional[str] = None
            self._batch_size = DoqaConfig.DEFAULT_BATCH_SIZE
            self._request_timeout_ms = DoqaConfig.DEFAULT_REQUEST_TIMEOUT_MS
            self._retries = DoqaConfig.DEFAULT_RETRIES
            self._retry_backoff_ms = DoqaConfig.DEFAULT_RETRY_BACKOFF_MS
            self._max_trace_length = DoqaConfig.DEFAULT_MAX_TRACE_LENGTH
            self._max_message_length = DoqaConfig.DEFAULT_MAX_MESSAGE_LENGTH
            self._max_parameter_length = DoqaConfig.DEFAULT_MAX_PARAMETER_LENGTH

        def url(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._url = v
            return self

        def token(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._token = v
            return self

        def space_id(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._space_id = v
            return self

        def configuration_id(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._configuration_id = v
            return self

        def test_run_id(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._test_run_id = v
            return self

        def test_run_name(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._test_run_name = v
            return self

        def adapter_mode(self, v: int) -> "DoqaConfig.Builder":
            self._adapter_mode = int(v)
            self._adapter_mode_explicit = True
            return self

        def import_realtime(self, v: bool) -> "DoqaConfig.Builder":
            self._import_realtime = bool(v)
            return self

        def cert_validation(self, v: bool) -> "DoqaConfig.Builder":
            self._cert_validation = bool(v)
            return self

        def proxy(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._proxy = v
            return self

        def reporting(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._reporting = v
            return self

        def results_dir(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._results_dir = v
            return self

        def environment(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._environment = v
            return self

        def pipeline_id(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._pipeline_id = v
            return self

        def ci_run_id(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._ci_run_id = v
            return self

        def source_key(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._source_key = v
            return self

        def correlation_id(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._correlation_id = v
            return self

        def branch(self, v: Optional[str]) -> "DoqaConfig.Builder":
            if v is not None:
                self._branch = v
            return self

        def batch_size(self, v: int) -> "DoqaConfig.Builder":
            if v > 0:
                self._batch_size = v
            return self

        def request_timeout_ms(self, v: int) -> "DoqaConfig.Builder":
            if v > 0:
                self._request_timeout_ms = v
            return self

        def retries(self, v: int) -> "DoqaConfig.Builder":
            if v > 0:
                self._retries = v
            return self

        def retry_backoff_ms(self, v: int) -> "DoqaConfig.Builder":
            # zero is valid: instant retries (used by tests)
            if v >= 0:
                self._retry_backoff_ms = v
            return self

        def max_trace_length(self, v: int) -> "DoqaConfig.Builder":
            if v > 0:
                self._max_trace_length = v
            return self

        def max_message_length(self, v: int) -> "DoqaConfig.Builder":
            if v > 0:
                self._max_message_length = v
            return self

        def max_parameter_length(self, v: int) -> "DoqaConfig.Builder":
            if v > 0:
                self._max_parameter_length = v
            return self

        def build(self) -> "DoqaConfig":
            return DoqaConfig(self)


def _blank_to_none(v: Optional[str]) -> Optional[str]:
    # An explicitly EMPTY value ("clear the inherited setting") must behave as absent -
    # it must never travel to the wire as "".
    if v is None or not v.strip():
        return None
    return v
