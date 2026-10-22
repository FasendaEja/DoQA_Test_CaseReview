"""Multipart wire format of ``POST /api/autotests/attachments`` and the content-type table."""

from typing import Optional, Tuple

_CONTENT_TYPES = (
    (".png", "image/png"),
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".gif", "image/gif"),
    (".mp4", "video/mp4"),
    (".webm", "video/webm"),
    (".txt", "text/plain"),
    (".log", "text/plain"),
    (".html", "text/html"),
    (".json", "application/json"),
    (".xml", "application/xml"),
    (".zip", "application/zip"),
)

DEFAULT_CONTENT_TYPE = "application/octet-stream"


def content_type_for(filename: Optional[str]) -> str:
    if filename is None:
        return DEFAULT_CONTENT_TYPE
    lower = filename.lower()
    for suffix, content_type in _CONTENT_TYPES:
        if lower.endswith(suffix):
            return content_type
    return DEFAULT_CONTENT_TYPE


def encode(boundary: str, token: Optional[str], space_id: Optional[str],
           filename: Optional[str], content_type: Optional[str]) -> Tuple[bytes, bytes]:
    """Preamble and epilogue framing the streamed file bytes; separators are CRLF, UTF-8."""
    ct = content_type if content_type else DEFAULT_CONTENT_TYPE
    preamble = (
        "--" + boundary + "\r\n"
        'Content-Disposition: form-data; name="token"\r\n'
        "\r\n"
        + (token or "") + "\r\n"
        "--" + boundary + "\r\n"
        'Content-Disposition: form-data; name="space_id"\r\n'
        "\r\n"
        + (space_id or "") + "\r\n"
        "--" + boundary + "\r\n"
        'Content-Disposition: form-data; name="file"; filename="'
        + sanitize_filename(filename) + '"\r\n'
        "Content-Type: " + ct + "\r\n"
        "\r\n"
    )
    epilogue = "\r\n--" + boundary + "--\r\n"
    return preamble.encode("utf-8"), epilogue.encode("utf-8")


def sanitize_filename(filename: Optional[str]) -> str:
    """Header-injection guard: strip CR/LF, escape backslash and double quote."""
    if filename is None:
        return ""
    cleaned = filename.replace("\r", "").replace("\n", "")
    return cleaned.replace("\\", "\\\\").replace('"', '\\"')
