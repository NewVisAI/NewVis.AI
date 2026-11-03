"""Strip credentials from stream URLs before they leave the server.

Live analytics stores each camera's source (e.g. rtsp://admin:pass@10.0.0.5/...)
as events.video_path, and several API responses carry those rows. These helpers
remove the user:password@ part, both in plain URLs and percent-encoded inside
another URL (as clip_service builds clip links: rtsp%3A//admin%3Apass%40...).
"""
import re

_USERINFO_PATTERN = r'((?:rtsps?|rtmps?|https?)(?::|%3A)(?://|%2F%2F))[^\s/@"\\&?#]*?(?:@|%40)'
_RE_STR = re.compile(_USERINFO_PATTERN, re.IGNORECASE)
_RE_BYTES = re.compile(_USERINFO_PATTERN.encode(), re.IGNORECASE)


def redact_url_credentials(text):
    """Return text with userinfo removed from every stream URL in it."""
    return _RE_STR.sub(r"\1", text) if isinstance(text, str) else text


def redact_url_credentials_bytes(data: bytes) -> bytes:
    return _RE_BYTES.sub(rb"\1", data)
