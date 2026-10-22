"""Process-global reporting session: sink selection, buffering, flushes.

Reporting never fails the user's test run, and a failing DoQA never costs the results: when
the run cannot be established (the server is unreachable, the token is rejected, the run
cannot be created) the session degrades to the file sink for the whole run; when a results
chunk is rejected once the run is established, that chunk alone is written as files and later
chunks keep using the API. Either way the results dir carries ``doqa-reporting.properties``,
so the upload step of a pipeline can tell "nothing to upload" from "nothing was reported".
"""

import atexit
import logging
import re
import threading
from typing import Callable, Dict, List, Optional

from doqa.client.api import ApiClient, ApiError
from doqa.client.config import DoqaConfig
from doqa.client.file_writer import REPORTING_INFO_FILE, AllureFileWriter
from doqa.client.resolver import ConfigResolver
from doqa.client.run_context import RunContext
from . import class_fixtures, result_builder
from .contexts import AttachmentRef
from .result_builder import Built
from .runtime import AdapterRuntime

LOG = logging.getLogger(__name__)

CONFIG_HINT = ("Configure them via the --doqa-url / --doqa-token / --doqa-space-id "
               "pytest options, the DOQA_URL / DOQA_TOKEN / DOQA_SPACE_ID environment "
               "variables, or a doqa.properties file.")
UPLOAD_HINT = (" Upload them in a later CI step (doqactl upload / "
               "POST /api/autotests/report).")


class _Reported:
    def __init__(self, built: Built):
        self.built = built


class DoqaSession:

    _instance: Optional["DoqaSession"] = None
    _lock = threading.Lock()
    _config_override: Optional[DoqaConfig] = None
    _env_override: Optional[Dict[str, str]] = None
    _cli_layer: Optional[Dict[str, str]] = None
    _client_factory: Optional[Callable[[DoqaConfig], ApiClient]] = None
    _discovery_verdict: Optional[bool] = None
    _shutdown_registered = False

    def __init__(self, enabled: bool, config: DoqaConfig,
                 client: Optional[ApiClient], run_context: Optional[RunContext],
                 file_writer: Optional[AllureFileWriter],
                 fallback_writer: Optional[AllureFileWriter] = None):
        self.enabled = enabled
        self.config = config
        self.client = client
        self.run_context = run_context
        self.file_writer = file_writer
        # api sink: where a chunk the server rejected lands; None when the dir is unusable
        self._fallback_writer = fallback_writer
        self.realtime = config.import_realtime
        # api sink: results the server accepted / results written as files instead
        self.delivered = 0
        self.fallback_results = 0
        self._buffer: List[_Reported] = []
        self._realtime_by_class: Dict[str, List[_Reported]] = {}
        self._file_uuids_by_class: Dict[str, List[str]] = {}
        self._id_owners: Dict[str, str] = {}
        self._warned_duplicate_ids: set = set()
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def get_or_init(cls) -> "DoqaSession":
        instance = cls._instance
        if instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls._create()
                instance = cls._instance
        return instance

    @classmethod
    def _create(cls) -> "DoqaSession":
        config = cls._resolve_config()
        reporting = config.effective_reporting()
        if reporting == DoqaConfig.REPORTING_OFF:
            LOG.debug("DoQA: reporting=off, reporter disabled.")
            return cls._disabled(config)
        if reporting == DoqaConfig.REPORTING_FILES:
            cls._warn_if_unconfigured(config)
            return cls._file_session(config, None)
        # api
        if not config.enabled():
            LOG.warning("DoQA: reporting=api, but the configuration is incomplete "
                        "(missing %s) - reporting is disabled. %s",
                        ", ".join(config.missing_api_settings()), CONFIG_HINT)
            return cls._disabled(config)
        try:
            client = (cls._client_factory(config) if cls._client_factory
                      else ApiClient(config))
            run_context = RunContext.establish(client, config)
        except Exception as e:
            LOG.warning("%s", _establish_failure_message(config, e))
            LOG.debug("DoQA: establish failure", exc_info=True)
            return cls._file_session(config, e)
        session = cls(True, config, client, run_context, None,
                      cls._open_fallback_writer(config))
        session._write_reporting_info()
        cls._register_shutdown_hook()
        return session

    @classmethod
    def _file_session(cls, config: DoqaConfig,
                      cause: Optional[Exception]) -> "DoqaSession":
        """The file sink - chosen deliberately (``reporting=files``, an unconfigured ``auto``)
        or as the landing of an API session that could not be established (``cause``)."""
        try:
            writer = AllureFileWriter(config.results_dir, AdapterRuntime.framework_label())
            writer.write_environment(config.environment)
            info: Dict[str, Optional[str]] = _ci_reporting_info(config)
            info["sink"] = "files"
            if cause is not None:
                info["degradedFrom"] = "api"
                info["reason"] = (_redact_token(str(cause), config.token)
                                  if config.token else str(cause))
            writer.write_reporting_info(info)
        except OSError as e:
            LOG.warning("DoQA: cannot open results dir '%s', disabling%s: %s",
                        config.results_dir,
                        " - the results of this run are LOST" if cause is not None else "",
                        e)
            return cls._disabled(config)
        if cause is None:
            LOG.debug("DoQA: reporting=files, results are written to %s",
                      config.results_dir)
        session = cls(True, config, None, None, writer)
        cls._register_shutdown_hook()
        return session

    @classmethod
    def _open_fallback_writer(cls, config: DoqaConfig) -> Optional[AllureFileWriter]:
        """The api session's landing for rejected chunks; None (such chunks are lost) when
        the results dir is unusable."""
        try:
            return AllureFileWriter(config.results_dir, AdapterRuntime.framework_label())
        except OSError as e:
            LOG.warning("DoQA: cannot open results dir '%s' - a results chunk the server "
                        "rejects will be lost: %s", config.results_dir, e)
            return None

    @classmethod
    def _disabled(cls, config: DoqaConfig) -> "DoqaSession":
        return cls(False, config, None, None, None)

    @classmethod
    def _resolve_config(cls) -> DoqaConfig:
        if cls._config_override is not None:
            return cls._config_override
        return ConfigResolver.resolve(cls._cli_layer, cls._env_override, None)

    @classmethod
    def _warn_if_unconfigured(cls, config: DoqaConfig) -> None:
        raw = (config.reporting or "auto").strip().lower()
        if raw in (DoqaConfig.REPORTING_API, DoqaConfig.REPORTING_FILES,
                   DoqaConfig.REPORTING_OFF):
            # explicit files is a deliberate choice - stay quiet (_create logs the
            # destination once for every files-mode path)
            return
        LOG.warning("DoQA: no reporting configuration found (missing %s) - results are "
                    "written as Allure files to '%s' and are NOT sent to DoQA. %s Set "
                    "doqa.reporting=files to make the file output explicit and silence "
                    "this warning.",
                    ", ".join(config.missing_api_settings()), config.results_dir,
                    CONFIG_HINT)

    @classmethod
    def _register_shutdown_hook(cls) -> None:
        # best-effort flush on process exit: rescues buffered results when a test calls
        # os-level exit or CI kills the process between the last test and the plan end
        if cls._shutdown_registered:
            return
        cls._shutdown_registered = True
        atexit.register(cls._shutdown_flush)

    @classmethod
    def _shutdown_flush(cls) -> None:
        instance = cls._instance
        if instance is None:
            return
        try:
            instance.flush()
        except Exception:
            LOG.warning("DoQA: shutdown flush failed")

    # ------------------------------------------------------------------ test seams
    @classmethod
    def set_config_override(cls, config: Optional[DoqaConfig]) -> None:
        cls._config_override = config
        cls._discovery_verdict = None

    @classmethod
    def set_env_override(cls, env: Optional[Dict[str, str]]) -> None:
        cls._env_override = env

    @classmethod
    def set_cli_layer(cls, cli: Optional[Dict[str, str]]) -> None:
        """CLI-priority layer injected by the framework bridge (``doqa.<field>`` keys)."""
        cls._cli_layer = cli

    @classmethod
    def set_client_factory(cls,
                           factory: Optional[Callable[[DoqaConfig], ApiClient]]) -> None:
        cls._client_factory = factory

    @classmethod
    def reset(cls) -> None:
        cls._instance = None
        cls._discovery_verdict = None
        class_fixtures.reset()

    @classmethod
    def current_config(cls) -> Optional[DoqaConfig]:
        instance = cls._instance
        return instance.config if instance is not None else None

    @classmethod
    def peek(cls) -> Optional["DoqaSession"]:
        """The existing singleton, or None - never initializes one."""
        return cls._instance

    @classmethod
    def resolve_config(cls) -> DoqaConfig:
        """Resolve the effective config from the registered layers without creating a
        session (no run creation, no directory writes)."""
        return cls._resolve_config()

    # ------------------------------------------------------------------ selection
    @classmethod
    def discovery_selection_active(cls) -> bool:
        """True only for a mode-0 API run. When the singleton is not created yet, the
        verdict is cached from the config alone WITHOUT initializing the session - a
        mode-2 discovery from an IDE must not create a run on the server."""
        instance = cls._instance
        if instance is not None:
            return (instance.enabled and instance.run_context is not None
                    and instance.run_context.selected_external_ids is not None)
        if cls._discovery_verdict is None:
            try:
                config = cls._resolve_config()
                cls._discovery_verdict = (
                    config.adapter_mode == DoqaConfig.MODE_SELECTIVE
                    and config.effective_reporting() == DoqaConfig.REPORTING_API)
            except Exception:
                cls._discovery_verdict = False
        return cls._discovery_verdict

    @classmethod
    def top_level_class(cls, class_key: Optional[str]) -> str:
        """Realtime buffer key: the container up to the first nesting marker (the module
        for ``pkg.module::Class``); the bridge must detect "class finished" by the same cut."""
        if not class_key:
            return ""
        marker = class_key.find("::")
        return class_key[:marker] if marker >= 0 else class_key

    def file_sink(self) -> bool:
        return self.file_writer is not None

    def allows_id(self, external_id: str) -> bool:
        """Gate for the result builder: filters only in mode-0 api."""
        return (not self.enabled or self.run_context is None
                or self.run_context.allows(external_id))

    # ------------------------------------------------------------------ reporting
    def report(self, built: Optional[Built]) -> None:
        if not self.enabled or built is None:
            return
        self._warn_on_duplicate_id(built)
        if self.file_sink():
            # the file sink writes immediately; @BeforeAll-analog fixtures have already run
            # and are prepended here, @AfterAll-analogs arrive later via the shared container
            built.result.prepend_setup_results(class_fixtures.before_results(
                built.class_key, lambda node: result_builder.to_result_step(
                    node, self.uploader())))
            result_uuid = self.file_writer.write(built.definition, built.result,
                                                 built.full_name, built.allure_id)
            if built.class_key:
                with self._state_lock:
                    self._file_uuids_by_class.setdefault(built.class_key,
                                                         []).append(result_uuid)
            return
        if (self.run_context is not None
                and not self.run_context.allows(built.definition.external_id)):
            # mode-0 gate, re-checked after build (per-invocation substituted ids)
            return
        reported = _Reported(built)
        with self._state_lock:
            if self.realtime:
                key = self.top_level_class(built.class_key)
                self._realtime_by_class.setdefault(key, []).append(reported)
            else:
                self._buffer.append(reported)

    def flush_class(self, class_fqcn: str) -> None:
        """Realtime only: upload the batch of a finished top-level container (called after
        its teardown fixtures, so class teardown is never lost)."""
        if not self.enabled or self.file_sink():
            return
        with self._state_lock:
            snapshot = self._realtime_by_class.pop(self.top_level_class(class_fqcn), None)
        if snapshot:
            self._upload(snapshot)

    def flush(self) -> None:
        if not self.enabled:
            return
        if self.file_sink():
            with self._state_lock:
                per_class = dict(self._file_uuids_by_class)
                self._file_uuids_by_class.clear()
            for class_key, uuids in per_class.items():
                # before-fixtures are already inside each result; only the after-fixtures
                # (known only now) travel via the shared container
                afters = class_fixtures.after_results(
                    class_key, lambda node: result_builder.to_result_step(
                        node, self.uploader()))
                self.file_writer.write_container(uuids, [], afters)
            self.file_writer.clear_attachment_meta()
            return
        with self._state_lock:
            snapshot = list(self._buffer)
            self._buffer.clear()
            for leftovers in self._realtime_by_class.values():
                # defensive: classes whose container event never arrived
                snapshot.extend(leftovers)
            self._realtime_by_class.clear()
        if snapshot:
            self._upload(snapshot)
        self._write_reporting_info()

    def _upload(self, snapshot: List[_Reported]) -> None:
        # the snapshot is already detached from the buffers: a repeated flush can neither
        # duplicate fixtures nor resend old results; a chunk the server rejects is written
        # as files instead
        for reported in snapshot:
            built = reported.built
            built.result.prepend_setup_results(class_fixtures.before_results(
                built.class_key, lambda node: result_builder.to_result_step(
                    node, self.uploader())))
            built.result.extend_teardown_results(class_fixtures.after_results(
                built.class_key, lambda node: result_builder.to_result_step(
                    node, self.uploader())))
        defs = {}
        for reported in snapshot:
            defs.setdefault(reported.built.definition.external_id, reported.built.definition)
        batch = self.config.batch_size
        unique_defs = list(defs.values())
        for i in range(0, len(unique_defs), batch):
            chunk = unique_defs[i:i + batch]
            try:
                self.client.upsert_autotests(chunk)
            except Exception as e:
                LOG.warning("DoQA: upsert chunk failed (%d defs dropped): %s",
                            len(chunk), e)
        for i in range(0, len(snapshot), batch):
            chunk = snapshot[i:i + batch]
            try:
                self.client.upload_results(self.run_context.run_id,
                                           self.run_context.configuration_id,
                                           [reported.built.result for reported in chunk])
                self.delivered += len(chunk)
            except Exception as e:
                self._spill(chunk, e)

    def _spill(self, chunk: List[_Reported], cause: Exception) -> None:
        """A chunk the server did not accept lands on disk in the file sink's format, so the
        upload step of the pipeline delivers it later. Nothing is re-sent here: a dead link
        would cost every remaining chunk its retries, and a chunk the server may have
        processed after all would arrive twice."""
        if self._fallback_writer is None:
            LOG.warning("DoQA: results chunk failed (%d results LOST, results dir "
                        "unavailable): %s", len(chunk), cause)
            return
        written = 0
        for reported in chunk:
            built = reported.built
            try:
                self._fallback_writer.write(built.definition, built.result,
                                            built.full_name, built.allure_id)
                written += 1
            except Exception as e:
                LOG.warning("DoQA: result %s LOST - cannot write it: %s",
                            built.definition.external_id, e)
        self.fallback_results += written
        LOG.warning("DoQA: results chunk failed (%s) - %d of %d result(s) written as Allure "
                    "files to '%s' instead.%s%s", cause, written, len(chunk),
                    self._fallback_writer.results_dir, UPLOAD_HINT,
                    _failure_hint(cause, self.config))

    def _write_reporting_info(self) -> None:
        """api sink: refresh ``doqa-reporting.properties`` with the current counts."""
        if self._fallback_writer is None or self.run_context is None:
            return
        try:
            info = _ci_reporting_info(self.config)
            info.update({
                "sink": "api",
                "runId": self.run_context.run_id,
                "adapterMode": str(self.run_context.mode),
                "delivered": str(self.delivered),
                "fallbackResults": str(self.fallback_results),
            })
            self._fallback_writer.write_reporting_info(info)
        except OSError as e:
            LOG.warning("DoQA: cannot write %s: %s", REPORTING_INFO_FILE, e)

    def uploader(self) -> Callable[[AttachmentRef], Optional[str]]:
        def upload(ref: Optional[AttachmentRef]) -> Optional[str]:
            if not self.enabled or ref is None:
                return None
            try:
                if self.file_sink():
                    if ref.path is not None:
                        return self.file_writer.store_attachment(ref.path)
                    return self.file_writer.store_attachment_bytes(ref.name, ref.content,
                                                                   ref.content_type)
                if ref.path is not None:
                    return self.client.upload_attachment(ref.path)
                return self.client.upload_attachment_bytes(ref.name or "attachment",
                                                           ref.content, ref.content_type)
            except Exception:
                # the attachment is lost, the test is not
                LOG.warning("DoQA: attachment handling failed for %s", ref)
                return None
        return upload

    def _warn_on_duplicate_id(self, built: Built) -> None:
        external_id = built.definition.external_id
        method_key = built.method_key or ""
        with self._state_lock:
            owner = self._id_owners.setdefault(external_id, method_key)
            if owner == method_key or external_id in self._warned_duplicate_ids:
                # invocations of one method share the id by design and stay silent
                return
            self._warned_duplicate_ids.add(external_id)
        LOG.warning('DoQA: externalId "%s" is reported by both %s and %s - their results '
                    "merge into one autotest (check class-level doqa.id / duplicated ids)",
                    external_id, owner, method_key)


def _ci_reporting_info(config: DoqaConfig) -> Dict[str, Optional[str]]:
    return {
        "pipelineId": config.pipeline_id,
        "ciRunId": config.ci_run_id,
        "sourceKey": config.source_key,
        "correlationId": config.correlation_id,
        "branch": config.branch,
    }


_SHORT_JSON_ESCAPES = {'"': '\\"', "\\": "\\\\", "/": "\\/", "\n": "\\n",
                       "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def _redact_token(text: str, token: str) -> str:
    """Redacts the token literally and in any JSON-escaped spelling."""
    parts = []
    for ch in token:
        code = ord(ch)
        units = (code,) if code < 0x10000 else (
            0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF))
        unicode_escape = "".join(
            "\\\\u" + "".join("[%s%s]" % (d, d.upper()) if d.isalpha() else d
                              for d in "%04x" % unit)
            for unit in units)
        alternatives = [unicode_escape]
        short = _SHORT_JSON_ESCAPES.get(ch)
        if short is not None:
            alternatives.append(re.escape(short))
        alternatives.append(re.escape(ch))
        parts.append("(?:%s)" % "|".join(alternatives))
    return re.sub("".join(parts), "***", text)


def _establish_failure_message(config: DoqaConfig, error: Exception) -> str:
    message = ("DoQA: could not establish the test run (%s) - results are written as Allure "
               "files to '%s' instead and are NOT sent to DoQA directly.%s%s"
               % (error, config.results_dir, UPLOAD_HINT, _failure_hint(error, config)))
    if config.adapter_mode == DoqaConfig.MODE_SELECTIVE:
        message += " The run's selection could not be fetched, so every discovered test runs."
    return message


def _failure_hint(error: Exception, config: DoqaConfig) -> str:
    """What the status says about the cause - the part of the warning the reader acts on."""
    if not isinstance(error, ApiError):
        return ""
    status = error.status
    if status == 0:
        return " DoQA did not answer at %s." % config.url
    if status == 401:
        return " DoQA rejected the token (401): check doqa.token / DOQA_TOKEN."
    if status == 403:
        return (" The token is not allowed for this space or CI binding (403): re-issue the "
                "CI variables from the DoQA CI/CD settings.")
    if status >= 500:
        return " DoQA answered %d - the server is unhealthy." % status
    return ""
