"""Config resolver - merge file / env / CLI layers with priority CLI > env > file.

Sources:

* **file** - ``doqa.properties`` (UTF-8) in the working directory, or the path given by the
  ``doqa.config`` CLI key / ``DOQA_CONFIG`` env. Keys are canonical field names or
  compatibility aliases (e.g. ``privateToken``, ``projectId``). An explicitly configured
  path that cannot be read is reported with a WARNING (a broken config must not silently
  degrade to defaults).
* **env** - ``DOQA_<FIELD>`` (snake case, e.g. ``DOQA_TEST_RUN_ID``, ``DOQA_ENVIRONMENT``,
  ``DOQA_BATCH_SIZE``) plus aliases ``DOQA_PRIVATE_TOKEN`` / ``DOQA_PROJECT_ID``; CI
  correlation auto-picked from ``CI_PIPELINE_ID`` / ``GITHUB_RUN_ID`` / the Jenkins job
  path + ``BUILD_NUMBER`` / the TeamCity internal build ID. Empty env values are ignored
  (CI systems export empty variables for unset settings).
* **CLI** (highest) - injectable mapping of ``doqa.<field>`` keys (adapters feed their
  command-line/ini options here). An explicitly EMPTY CLI value clears the field inherited
  from lower layers (e.g. ``doqa.pipelineId=`` drops an auto-picked ``CI_PIPELINE_ID``).

Canonical fields: ``url``, ``token``, ``spaceId``, ``configurationId``, ``testRunId``,
``testRunName``, ``adapterMode`` (``0|1|2`` or ``selective|existing|new``),
``importRealtime``, ``certValidation``, ``proxy``, ``reporting`` (``auto|api|files|off``),
``resultsDir``, ``environment``, ``pipelineId``, ``ciRunId``, ``sourceKey``,
``correlationId``, ``branch``, ``batchSize``,
``requestTimeoutMs``, ``retries``, ``retryBackoffMs``, ``maxTraceLength``,
``maxMessageLength``, ``maxParameterLength``.
"""

import logging
import os
import re
from typing import Dict, Mapping, Optional, Union

from .config import DoqaConfig
from .properties import read_properties

LOG = logging.getLogger(__name__)

# A value that is nothing but a variable reference ($DOQA_TOKEN, ${DOQA_TOKEN}) is an
# unexpanded CI placeholder rather than a setting - the literal text reaches the process
# whenever the referenced variable does not exist.
_UNEXPANDED_REF = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*}?")

URL = "url"
TOKEN = "token"
SPACE_ID = "spaceId"
CONFIGURATION_ID = "configurationId"
TEST_RUN_ID = "testRunId"
TEST_RUN_NAME = "testRunName"
ADAPTER_MODE = "adapterMode"
IMPORT_REALTIME = "importRealtime"
CERT_VALIDATION = "certValidation"
PROXY = "proxy"
REPORTING = "reporting"
RESULTS_DIR = "resultsDir"
ENVIRONMENT = "environment"
PIPELINE_ID = "pipelineId"
CI_RUN_ID = "ciRunId"
SOURCE_KEY = "sourceKey"
CORRELATION_ID = "correlationId"
BRANCH = "branch"
BATCH_SIZE = "batchSize"
REQUEST_TIMEOUT_MS = "requestTimeoutMs"
RETRIES = "retries"
RETRY_BACKOFF_MS = "retryBackoffMs"
MAX_TRACE_LENGTH = "maxTraceLength"
MAX_MESSAGE_LENGTH = "maxMessageLength"
MAX_PARAMETER_LENGTH = "maxParameterLength"

_CANONICAL = {
    "url": URL,
    "token": TOKEN,
    "privatetoken": TOKEN,
    "spaceid": SPACE_ID,
    "projectid": SPACE_ID,
    "configurationid": CONFIGURATION_ID,
    "testrunid": TEST_RUN_ID,
    "testrunname": TEST_RUN_NAME,
    "adaptermode": ADAPTER_MODE,
    "importrealtime": IMPORT_REALTIME,
    "certvalidation": CERT_VALIDATION,
    "proxy": PROXY,
    "reporting": REPORTING,
    "resultsdir": RESULTS_DIR,
    "environment": ENVIRONMENT,
    "pipelineid": PIPELINE_ID,
    "cirunid": CI_RUN_ID,
    "sourcekey": SOURCE_KEY,
    "correlationid": CORRELATION_ID,
    "branch": BRANCH,
    "batchsize": BATCH_SIZE,
    "requesttimeoutms": REQUEST_TIMEOUT_MS,
    "retries": RETRIES,
    "retrybackoffms": RETRY_BACKOFF_MS,
    "maxtracelength": MAX_TRACE_LENGTH,
    "maxmessagelength": MAX_MESSAGE_LENGTH,
    "maxparameterlength": MAX_PARAMETER_LENGTH,
}

# (env variable, canonical field) in check order; the first source wins - never overwrite.
_ENV_ORDER = (
    ("DOQA_URL", URL),
    ("DOQA_TOKEN", TOKEN),
    ("DOQA_PRIVATE_TOKEN", TOKEN),
    ("DOQA_SPACE_ID", SPACE_ID),
    ("DOQA_PROJECT_ID", SPACE_ID),
    ("DOQA_CONFIGURATION_ID", CONFIGURATION_ID),
    ("DOQA_TEST_RUN_ID", TEST_RUN_ID),
    ("DOQA_TEST_RUN_NAME", TEST_RUN_NAME),
    ("DOQA_ADAPTER_MODE", ADAPTER_MODE),
    ("DOQA_IMPORT_REALTIME", IMPORT_REALTIME),
    ("DOQA_CERT_VALIDATION", CERT_VALIDATION),
    ("DOQA_PROXY", PROXY),
    ("DOQA_REPORTING", REPORTING),
    ("DOQA_RESULTS_DIR", RESULTS_DIR),
    ("DOQA_ENVIRONMENT", ENVIRONMENT),
    # CI pipeline linkage: an explicit DOQA_PIPELINE_ID wins (first source in order);
    # otherwise auto-picked from the standard CI variables (GitLab / GitHub Actions).
    ("DOQA_PIPELINE_ID", PIPELINE_ID),
    ("CI_PIPELINE_ID", PIPELINE_ID),
    ("GITHUB_RUN_ID", PIPELINE_ID),
    # DoQA-initiated launches pass their ci_run id (DOQA_CI_RUN_ID) - echoed back with
    # results so the backend links them to the exact pipeline.
    ("DOQA_CI_RUN_ID", CI_RUN_ID),
    ("DOQA_SOURCE_KEY", SOURCE_KEY),
    ("DOQA_CORRELATION_ID", CORRELATION_ID),
    ("DOQA_BRANCH", BRANCH),
    ("CI_COMMIT_REF_NAME", BRANCH),
    ("GITHUB_REF_NAME", BRANCH),
    ("DOQA_BATCH_SIZE", BATCH_SIZE),
    ("DOQA_REQUEST_TIMEOUT_MS", REQUEST_TIMEOUT_MS),
    ("DOQA_RETRIES", RETRIES),
    ("DOQA_RETRY_BACKOFF_MS", RETRY_BACKOFF_MS),
    ("DOQA_MAX_TRACE_LENGTH", MAX_TRACE_LENGTH),
    ("DOQA_MAX_MESSAGE_LENGTH", MAX_MESSAGE_LENGTH),
    ("DOQA_MAX_PARAMETER_LENGTH", MAX_PARAMETER_LENGTH),
)


class ConfigResolver:

    @staticmethod
    def resolve(cli: Optional[Mapping[str, str]] = None,
                env: Optional[Mapping[str, str]] = None,
                config_file: Union[str, "os.PathLike[str]", None] = None) -> DoqaConfig:
        """Resolve the effective config; ``env`` defaults to ``os.environ``."""
        if env is None:
            env = os.environ
        file_layer = _normalize(_load_file(cli, env, config_file), keep_empty=False)
        env_layer = _normalize(_collect_env(env), keep_empty=False)
        # CLI keeps empty values: an explicit doqa.<field>= clears the inherited setting.
        cli_layer = _normalize(_collect_cli(cli), keep_empty=True)

        merged: Dict[str, str] = {}
        merged.update(file_layer)
        merged.update(env_layer)
        merged.update(cli_layer)

        b = (DoqaConfig.Builder()
             .url(merged.get(URL))
             .token(merged.get(TOKEN))
             .space_id(merged.get(SPACE_ID))
             .configuration_id(merged.get(CONFIGURATION_ID))
             .test_run_id(merged.get(TEST_RUN_ID))
             .test_run_name(merged.get(TEST_RUN_NAME))
             .proxy(merged.get(PROXY))
             .reporting(merged.get(REPORTING))
             .environment(merged.get(ENVIRONMENT))
             .pipeline_id(merged.get(PIPELINE_ID))
             .ci_run_id(merged.get(CI_RUN_ID))
             .source_key(merged.get(SOURCE_KEY))
             .correlation_id(merged.get(CORRELATION_ID))
             .branch(merged.get(BRANCH)))
        if merged.get(RESULTS_DIR, "").strip():
            b.results_dir(merged[RESULTS_DIR])
        if merged.get(ADAPTER_MODE, "").strip():
            b.adapter_mode(_parse_mode(merged[ADAPTER_MODE]))
        if IMPORT_REALTIME in merged:
            b.import_realtime(_parse_bool(merged[IMPORT_REALTIME]))
        if CERT_VALIDATION in merged:
            b.cert_validation(_parse_bool(merged[CERT_VALIDATION]))
        b.batch_size(_parse_int(merged.get(BATCH_SIZE), DoqaConfig.DEFAULT_BATCH_SIZE))
        b.request_timeout_ms(_parse_int(merged.get(REQUEST_TIMEOUT_MS),
                                        DoqaConfig.DEFAULT_REQUEST_TIMEOUT_MS))
        b.retries(_parse_int(merged.get(RETRIES), DoqaConfig.DEFAULT_RETRIES))
        b.retry_backoff_ms(_parse_int(merged.get(RETRY_BACKOFF_MS),
                                      DoqaConfig.DEFAULT_RETRY_BACKOFF_MS))
        b.max_trace_length(_parse_int(merged.get(MAX_TRACE_LENGTH),
                                      DoqaConfig.DEFAULT_MAX_TRACE_LENGTH))
        b.max_message_length(_parse_int(merged.get(MAX_MESSAGE_LENGTH),
                                        DoqaConfig.DEFAULT_MAX_MESSAGE_LENGTH))
        b.max_parameter_length(_parse_int(merged.get(MAX_PARAMETER_LENGTH),
                                          DoqaConfig.DEFAULT_MAX_PARAMETER_LENGTH))
        return b.build()


def canonical(key: Optional[str]) -> Optional[str]:
    """Map an arbitrary key (canonical / alias / dashed / snake) to a canonical field name."""
    if key is None:
        return None
    k = key.strip().lower().replace("-", "").replace("_", "")
    return _CANONICAL.get(k)


def _load_file(cli, env, override) -> Dict[str, str]:
    path = override
    explicit = override is not None
    if path is None:
        from_cli = cli.get("doqa.config") if cli else None
        from_env = env.get("DOQA_CONFIG") if env else None
        p = from_cli if from_cli is not None else from_env
        explicit = p is not None
        path = p if p is not None else "doqa.properties"
    path = os.fspath(path)
    out: Dict[str, str] = {}
    if not os.path.isfile(path) or not os.access(path, os.R_OK):
        if explicit:
            LOG.warning("DoQA config file %s is not readable - ignoring it", path)
        return out
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip()
    except (OSError, UnicodeDecodeError) as e:
        LOG.warning("DoQA config file %s could not be parsed: %s", path, e)
        return {}
    return out


def _collect_env(env: Mapping[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, field in _ENV_ORDER:
        v = env.get(key)
        if v and field not in out:
            out[field] = v
    if PIPELINE_ID not in out:
        # the form the CI binding stores: folder/job/tests#83
        job = _env_value(env, "JOB_NAME")
        number = _env_value(env, "BUILD_NUMBER")
        if job and number and re.fullmatch(r"[0-9]+", number):
            out[PIPELINE_ID] = job.replace("/job/", "/").replace("/", "/job/") + "#" + number
        else:
            build_id = _teamcity_build_id(env)
            if build_id:
                out[PIPELINE_ID] = build_id
    return out


def _env_value(env: Mapping[str, str], key: str) -> Optional[str]:
    value = env.get(key)
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed if trimmed and not _UNEXPANDED_REF.fullmatch(trimmed) else None


def _teamcity_build_id(env: Mapping[str, str]) -> Optional[str]:
    explicit = _env_value(env, "TEAMCITY_BUILD_ID")
    if explicit and re.fullmatch(r"[0-9]+", explicit):
        return explicit
    path = _env_value(env, "TEAMCITY_BUILD_PROPERTIES_FILE")
    if not path:
        return None
    try:
        properties = read_properties(path)
        for key in ("teamcity.build.id", "build.id"):
            value = properties.get(key, "").strip()
            if re.fullmatch(r"[0-9]+", value):
                return value
    except (OSError, ValueError):
        LOG.warning("DoQA: TeamCity build properties could not be read; configure "
                    "DOQA_PIPELINE_ID with the internal build ID")
    return None


def _collect_cli(cli: Optional[Mapping[str, str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not cli:
        return out
    for name, value in cli.items():
        if name.startswith("doqa."):
            out[name[len("doqa."):]] = value
    return out


def _normalize(raw: Mapping[str, str], keep_empty: bool) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in raw.items():
        field = canonical(key)
        if field is None or value is None:
            continue
        trimmed = value.strip()
        if _UNEXPANDED_REF.fullmatch(trimmed):
            LOG.warning('DoQA: %s is set to the unexpanded variable reference "%s" - treating '
                        "it as unset. Check that the CI variable exists and is exported to "
                        "this job.", field, trimmed)
            continue
        if trimmed or keep_empty:
            out[field] = trimmed
    return out


def _parse_mode(v: str) -> int:
    """``0|1|2`` or a symbolic name: ``selective``, ``existing``, ``new``."""
    s = v.strip().lower()
    if s == "selective":
        return DoqaConfig.MODE_SELECTIVE
    if s in ("existing", "existingrun", "existing-run"):
        return DoqaConfig.MODE_EXISTING_RUN
    if s in ("new", "newrun", "new-run"):
        return DoqaConfig.MODE_NEW_RUN
    return _parse_int(s, DoqaConfig.DEFAULT_ADAPTER_MODE)


def _parse_int(v: Optional[str], default: int) -> int:
    if v is None or not v.strip():
        return default
    try:
        return int(v.strip())
    except ValueError:
        return default


def _parse_bool(v: Optional[str]) -> bool:
    if v is None:
        return False
    return v.strip().lower() in ("1", "true", "yes", "on", "y")
