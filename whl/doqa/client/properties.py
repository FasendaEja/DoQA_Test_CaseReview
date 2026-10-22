"""Java properties encoding for CI metadata and TeamCity's build properties."""

import re
from typing import Dict


def escape_value(value: str) -> str:
    """Keep each value on one physical line and preserve it for Java readers."""
    escaped = (value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
               .replace("\t", "\\t").replace("\f", "\\f").replace("=", "\\=")
               .replace(":", "\\:").replace("#", "\\#").replace("!", "\\!")
               .replace(" ", "\\ "))
    # ASCII-only: Latin-1 and UTF-8 readers see the same text
    out = []
    for char in escaped:
        if ord(char) < 128:
            out.append(char)
        else:
            units = char.encode("utf-16-be")
            for i in range(0, len(units), 2):
                out.append("\\u%04x" % int.from_bytes(units[i:i + 2], "big"))
    return "".join(out)


def _unescape(value: str) -> str:
    def decode(match):
        escaped = match.group(1)
        if escaped.startswith("u"):
            return chr(int(escaped[1:], 16))
        return {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(escaped, escaped)
    decoded = re.sub(r"\\(u[0-9a-fA-F]{4}|.)", decode, value)
    return decoded.encode("utf-16", "surrogatepass").decode("utf-16", "surrogatepass")


def read_properties(path: str) -> Dict[str, str]:
    """Read the Java Properties.load(InputStream) format (ISO-8859-1)."""
    out: Dict[str, str] = {}
    pending = ""
    with open(path, encoding="iso-8859-1") as file:
        for physical in file:
            line = physical.rstrip("\r\n").lstrip(" \t\f")
            if not pending and (not line or line.startswith(("#", "!"))):
                continue
            line = pending + line
            backslashes = len(line) - len(line.rstrip("\\"))
            if backslashes % 2:
                pending = line[:-1]
                continue
            pending = ""
            _put_property(out, line)
    if pending:
        _put_property(out, pending)
    return out


def _put_property(out: Dict[str, str], line: str) -> None:
    escaped = False
    split = len(line)
    for i, char in enumerate(line):
        if not escaped and char in "=: \t\f":
            split = i
            break
        escaped = not escaped if char == "\\" else False
    key = _unescape(line[:split])
    rest = line[split:].lstrip(" \t\f")
    if rest.startswith(("=", ":")):
        rest = rest[1:].lstrip(" \t\f")
    out[key] = _unescape(rest)
