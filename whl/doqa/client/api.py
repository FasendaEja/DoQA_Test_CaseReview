"""Direct Autotest API client: endpoints, retries, circuit breaker, token masking.

Authentication is carried in the body for POST (``token`` + ``space_id`` first in the JSON
object) and in the query for GET (``?token=...``) - no Authorization headers.

Retry policy: 429 is always retried (the server did not process the request); 5xx only for
idempotent requests (a non-idempotent POST could have been applied before the error
response - a replay would duplicate a run/result); other 4xx never. Network errors retry
when the request is idempotent or the connection was never established.
"""

import io
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional

from . import jsonio, multipart
from .config import DoqaConfig
from .models import AutotestDef, AutotestResult, put_if_present
from .transport import HttpClientTransport, Request, Response, Transport, is_connect_failure

_TOKEN_IN_QUERY = re.compile(r"([?&]token=)[^&]*")

CIRCUIT_THRESHOLD = 5
CIRCUIT_COOLDOWN_MS = 30_000


class ApiError(RuntimeError):
    """The single error type of the client; ``__cause__`` carries the network error.

    ``status`` is the HTTP status the server answered with, or 0 when no response arrived at
    all (connection failure, timeout, open circuit) - callers branch on it to explain a
    failure and to decide what a retry could still achieve.
    """

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status

    def is_auth_failure(self) -> bool:
        """401/403: the credentials are unusable, every further request with them fails too."""
        return self.status in (401, 403)


class ApiClient:

    def __init__(self, config: DoqaConfig, transport: Optional[Transport] = None,
                 max_retries: Optional[int] = None,
                 retry_backoff_ms: Optional[int] = None):
        self._config = config
        self._base = (config.url or "").rstrip("/")
        self._transport = transport if transport is not None else HttpClientTransport(config)
        self._max_retries = max_retries if max_retries is not None else max(1, config.retries)
        self._retry_backoff_ms = (retry_backoff_ms if retry_backoff_ms is not None
                                  else config.retry_backoff_ms)
        self._consecutive_failures = 0
        self._last_failure_at_ms = 0.0

    # ------------------------------------------------------------------ endpoints
    def upsert_autotests(self, defs: Iterable[AutotestDef]) -> Dict[str, Any]:
        body = self._auth_body()
        body["autotests"] = [d.to_payload() for d in defs]
        resp = self._request(Request.post_json(self._url("upsert"), jsonio.write(body)))
        result = jsonio.parse_object(resp.body).get("map")
        return result if isinstance(result, dict) else {}

    def create_test_run(self, name: Optional[str] = None,
                        configuration_id: Optional[str] = None,
                        external_key: Optional[str] = None) -> Optional[str]:
        body = self._auth_body()
        put_if_present(body, "name", name if name is not None else self._config.test_run_name)
        put_if_present(body, "configuration_id",
                       configuration_id if configuration_id is not None
                       else self._config.configuration_id)
        put_if_present(body, "external_key", external_key)
        # pipeline_id/branch/environment link the run to its CI pipeline; without them DoQA
        # cannot show the pipeline and jobs for a run started straight from CI.
        put_if_present(body, "pipeline_id", self._config.pipeline_id)
        put_if_present(body, "source_key", self._config.source_key)
        put_if_present(body, "branch", self._config.branch)
        put_if_present(body, "environment", self._config.environment)
        # with an external_key the server deduplicates the create - the request becomes
        # idempotent and may be replayed; without one a lost response could mean two runs.
        resp = self._request(Request.post_json(self._url("test-runs"), jsonio.write(body),
                                               idempotent=external_key is not None))
        run_id = jsonio.parse_object(resp.body).get("runId")
        return None if run_id is None else str(run_id)

    def get_run_autotests(self, run_id: str,
                          configuration_id: Optional[str] = None) -> List[str]:
        conf = (configuration_id if configuration_id is not None
                else self._config.configuration_id)
        query = "?token=" + _enc(self._config.token or "")
        if conf is not None:
            query += "&configuration_id=" + _enc(conf)
        if self._config.ci_run_id is not None:
            # camelCase on purpose: the established plan parameter, ignored by older servers
            query += "&ciRunId=" + _enc(self._config.ci_run_id)
        for key, value in self._ci_context().items():
            if key != "ci_run_id" and value is not None:
                query += "&" + key + "=" + _enc(value)
        resp = self._request(Request.get(
            self._url("test-runs/" + str(run_id) + "/autotests") + query))
        out: List[str] = []
        autotests = jsonio.parse_object(resp.body).get("autotests")
        if isinstance(autotests, list):
            for item in autotests:
                if not isinstance(item, dict):
                    continue
                external_id = item.get("externalId")
                if external_id is None:
                    # an explicit null camelCase key must still fall back to snake_case
                    external_id = item.get("external_id")
                if external_id is not None:
                    out.append(str(external_id))
        return out

    def upload_attachment(self, path: "os.PathLike[str]") -> Optional[str]:
        path = os.fspath(path)
        try:
            size = os.path.getsize(path)
        except OSError as e:
            raise ApiError("failed to read attachment " + path + ": " + str(e)) from e
        filename = os.path.basename(path)
        # streamed from disk: UI-test videos must not be buffered in memory
        return self._upload_attachment(filename, size, lambda: open(path, "rb"),
                                       multipart.content_type_for(filename))

    def upload_attachment_bytes(self, filename: str, content: Optional[bytes],
                                content_type: Optional[str] = None) -> Optional[str]:
        data = content if content is not None else b""
        ct = content_type if content_type is not None else multipart.content_type_for(filename)
        return self._upload_attachment(filename, len(data), lambda: io.BytesIO(data), ct)

    def _upload_attachment(self, filename, size, open_stream, content_type):
        boundary = "----DoqaBoundary" + format(time.monotonic_ns(), "x")
        preamble, epilogue = multipart.encode(boundary, self._config.token,
                                              self._config.space_id, filename, content_type)

        def stream_factory():
            return _ConcatStream(preamble, open_stream(), epilogue)

        resp = self._request(Request.post_stream(
            self._url("attachments"), stream_factory,
            len(preamble) + size + len(epilogue),
            "multipart/form-data; boundary=" + boundary))
        media_file_id = jsonio.parse_object(resp.body).get("mediaFileId")
        return None if media_file_id is None else str(media_file_id)

    def upload_results(self, run_id: Optional[str], configuration_id: Optional[str],
                       results: Iterable[AutotestResult]) -> Dict[str, Any]:
        body = self._auth_body()
        put_if_present(body, "test_run_id", run_id)
        put_if_present(body, "configuration_id",
                       configuration_id if configuration_id is not None
                       else self._config.configuration_id)
        # CI correlation echo: without it the backend cannot link results to the pipeline
        # (per-pipeline quality gate and autotest sources would see an empty run).
        self._put_ci_context(body)
        body["results"] = [r.to_payload() for r in results]
        resp = self._request(Request.post_json(self._url("results"), jsonio.write(body)))
        return jsonio.parse_object(resp.body)

    # ------------------------------------------------------------------ plumbing
    def _ci_context(self) -> Dict[str, Optional[str]]:
        return {
            "ci_run_id": self._config.ci_run_id,
            "pipeline_id": self._config.pipeline_id,
            "source_key": self._config.source_key,
            "correlation_id": self._config.correlation_id,
        }

    def _put_ci_context(self, body: Dict[str, Any]) -> None:
        for key, value in self._ci_context().items():
            put_if_present(body, key, value)

    def _auth_body(self) -> Dict[str, Any]:
        return {"token": self._config.token, "space_id": self._config.space_id}

    def _url(self, path: str) -> str:
        return self._base + "/api/autotests/" + path.lstrip("/")

    def _request(self, req: Request) -> Response:
        attempts = self._circuit_attempts(req)
        last_io: Optional[OSError] = None
        for attempt in range(attempts):
            try:
                resp = self._transport.send(req)
            except OSError as e:
                last_io = e
                if (req.idempotent or is_connect_failure(e)) and attempt + 1 < attempts:
                    self._backoff(attempt)
                    continue
                self._record_failure()
                raise ApiError("request to " + safe_url(req.url) + " failed: " + str(e)) from e
            retriable = resp.status == 429 or (resp.status >= 500 and req.idempotent)
            if retriable and attempt + 1 < attempts:
                self._backoff(attempt)
                continue
            if resp.status >= 400:
                self._record_failure()
                raise ApiError("%s %s -> %d: %s"
                               % (req.method, safe_url(req.url), resp.status, resp.body[:500]),
                               resp.status)
            self._consecutive_failures = 0
            return resp
        self._record_failure()
        error = ApiError("request to " + safe_url(req.url) + " failed after retries")
        if last_io is not None:
            raise error from last_io
        raise error

    def _circuit_attempts(self, req: Request) -> int:
        # a dead backend must not cost retries x timeout on every call
        if self._consecutive_failures < CIRCUIT_THRESHOLD:
            return self._max_retries
        if _now_ms() - self._last_failure_at_ms < CIRCUIT_COOLDOWN_MS:
            # the failure counter/stamp stay untouched: the cooldown counts from the last
            # REAL failure, not from rejected calls
            raise ApiError("request to " + safe_url(req.url)
                           + " rejected: circuit open after "
                           + str(self._consecutive_failures) + " consecutive failures")
        return 1  # one probe: success closes the circuit, failure re-stamps the clock

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        self._last_failure_at_ms = _now_ms()

    def _backoff(self, attempt: int) -> None:
        # a KeyboardInterrupt in the sleep propagates on purpose: Ctrl-C stops the run
        time.sleep((self._retry_backoff_ms << attempt) / 1000.0)


def safe_url(url: str) -> str:
    """URL with the token query value masked; tokens never reach error messages or logs."""
    return _TOKEN_IN_QUERY.sub(r"\g<1>***", url)


def _enc(value: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(value)


def _now_ms() -> float:
    return time.monotonic() * 1000.0


class _ConcatStream(io.RawIOBase):
    """Streams preamble + file + epilogue without buffering the file in memory."""

    def __init__(self, preamble: bytes, stream, epilogue: bytes):
        self._parts = [io.BytesIO(preamble), stream, io.BytesIO(epilogue)]
        self._index = 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunks = []
            while self._index < len(self._parts):
                chunks.append(self._parts[self._index].read())
                self._index += 1
            return b"".join(chunks)
        while self._index < len(self._parts):
            chunk = self._parts[self._index].read(size)
            if chunk:
                return chunk
            self._index += 1
        return b""

    def close(self) -> None:
        for part in self._parts:
            try:
                part.close()
            except Exception:
                pass
        super().close()
