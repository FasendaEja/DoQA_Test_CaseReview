"""HTTP seam: an injectable transport with a stdlib HTTP/1.1 implementation.

The seam keeps :class:`~doqa.client.api.ApiClient` fully testable offline; the real
transport enforces the config timeouts, TLS trust toggle and proxy.
"""

import socket
import ssl
from http.client import HTTPConnection, HTTPSConnection
from typing import Callable, IO, Optional
from urllib.parse import urlsplit

from .config import DoqaConfig


class Request:
    """Immutable request descriptor.

    ``body_stream`` is a stream FACTORY, not a stream: every retry gets a fresh stream and
    can resend the body. ``idempotent`` drives the retry policy.
    """

    def __init__(self, method: str, url: str, json_body: Optional[str] = None,
                 body_stream: Optional[Callable[[], IO[bytes]]] = None,
                 content_length: int = 0, content_type: Optional[str] = None,
                 idempotent: bool = False):
        self.method = method
        self.url = url
        self.json_body = json_body
        self.body_stream = body_stream
        self.content_length = content_length
        self.content_type = content_type
        self.idempotent = idempotent

    @staticmethod
    def get(url: str) -> "Request":
        return Request("GET", url, idempotent=True)

    @staticmethod
    def post_json(url: str, body: Optional[str], idempotent: bool = False) -> "Request":
        return Request("POST", url, json_body=body, idempotent=idempotent)

    @staticmethod
    def post_stream(url: str, stream_factory: Callable[[], IO[bytes]],
                    content_length: int, content_type: str) -> "Request":
        return Request("POST", url, body_stream=stream_factory,
                       content_length=content_length, content_type=content_type)


class Response:
    def __init__(self, status: int, body: Optional[str]):
        self.status = status
        self.body = body if body is not None else ""


class Transport:
    """Sends a :class:`Request`; may raise :class:`OSError` on network failures."""

    def send(self, request: Request) -> Response:
        raise NotImplementedError


class ConnectTimeoutError(OSError):
    """Timeout while establishing the connection (TCP or TLS handshake).

    Kept distinct from a read timeout: here the server has not received the request, so
    a replay is safe even for a non-idempotent POST.
    """


class HttpClientTransport(Transport):
    """Real HTTP/1.1 transport on ``http.client``.

    ``cert_validation=False`` installs a fully trusting TLS context that skips both the
    certificate chain check AND hostname verification - the typical on-prem case is a
    self-signed certificate issued for a different host.
    """

    _CHUNK = 64 * 1024

    def __init__(self, config: DoqaConfig):
        self._timeout = config.request_timeout_ms / 1000.0
        self._proxy = parse_proxy(config.proxy)
        if config.cert_validation:
            self._ssl_context = ssl.create_default_context()
        else:
            self._ssl_context = ssl._create_unverified_context()

    def send(self, request: Request) -> Response:
        parts = urlsplit(request.url)
        secure = parts.scheme == "https"
        host = parts.hostname or ""
        port = parts.port or (443 if secure else 80)
        conn = self._connect(host, port, secure)
        try:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            if self._proxy is not None:
                # plain-http via proxy: absolute-form request target
                if secure:
                    conn.set_tunnel(host, port)
                else:
                    path = request.url
            self._open(conn)
            headers = {"Host": parts.netloc, "Accept": "application/json"}
            body = None
            if request.body_stream is not None:
                headers["Content-Type"] = request.content_type or "application/octet-stream"
                # exact Content-Length, never chunked: some backends reject chunked uploads
                headers["Content-Length"] = str(request.content_length)
                body = request.body_stream()
            elif request.method == "POST":
                headers["Content-Type"] = request.content_type or "application/json"
                body = (request.json_body or "").encode("utf-8")
                headers["Content-Length"] = str(len(body))
            try:
                conn.request(request.method, path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            finally:
                if hasattr(body, "close"):
                    body.close()
            return Response(resp.status, data.decode("utf-8", errors="replace"))
        finally:
            conn.close()

    @staticmethod
    def _open(conn) -> None:
        # explicit connect before the request: a timeout in this phase must stay
        # distinguishable from a read timeout, or the retry policy cannot replay it
        try:
            conn.connect()
        except socket.timeout as e:
            raise ConnectTimeoutError(str(e) or "timed out while connecting") from e

    def _connect(self, host: str, port: int, secure: bool):
        target_host, target_port = (host, port)
        if self._proxy is not None:
            target_host, target_port = self._proxy
        if secure:
            return HTTPSConnection(target_host, target_port, timeout=self._timeout,
                                   context=self._ssl_context)
        return HTTPConnection(target_host, target_port, timeout=self._timeout)


def parse_proxy(proxy: Optional[str]):
    """``host:port`` -> tuple; scheme prefix tolerated, IPv6 in brackets, default port 8080."""
    if not proxy:
        return None
    p = proxy.strip()
    if "://" in p:
        p = p.split("://", 1)[1]
    if p.startswith("["):
        host, _, rest = p[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
        return host, _parse_port(port)
    if p.count(":") == 1:
        host, _, port = p.partition(":")
        return host, _parse_port(port)
    # several colons without brackets: the whole string is a bare IPv6 host
    return p, 8080


def _parse_port(port: str) -> int:
    try:
        return int(port)
    except ValueError:
        return 8080


def is_connect_failure(error: OSError) -> bool:
    """True when the connection was never established - the server processed nothing, so a
    replay is safe even for a non-idempotent POST. A blanket ``TimeoutError`` does NOT
    qualify: a read timeout may have been applied server-side already."""
    return isinstance(error, (ConnectionRefusedError, socket.gaierror, ConnectTimeoutError))
