from __future__ import annotations

import logging
import os
import re
import traceback
from logging.handlers import RotatingFileHandler
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_KEYS = frozenset({
    "access_token", "access-token", "refresh_token", "refresh-token", "id_token", "id-token",
    "token", "password", "passwd", "client_secret", "client-secret", "api_key", "api-key",
    "apikey", "authorization", "cookie", "set-cookie", "bot_token", "bot-token", "secret",
    "secret_key", "private_key", "proxy", "auth", "key", "session", "xs", "c_user",
    "sessionid", "session_key", "code",
})
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(
    r"(?i)([\"']?(?:access[_-]?token|refresh[_-]?token|id[_-]?token|token|password|passwd|client[_-]?secret|api[_-]?key|apikey|authorization|cookie|set-cookie|bot[_-]?token|secret|secret[_-]?key|private[_-]?key|proxy|auth|key|xs|c_user|sessionid|session[_-]?key|code)[\"']?\s*[:=]\s*)([\"']?)"
    # لا تبتلع كلمة المخطط (Basic/Bearer) وحدها فتترك الرمز مكشوفاً
    r"(?!(?:basic|bearer|digest|apikey)\b)([^\s,;&\"']+)([\"']?)"
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_BASIC_RE = re.compile(r"(?i)(\bBasic\s+)[A-Za-z0-9+/=]{8,}")
_TELEGRAM_BOT_RE = re.compile(r"(?i)(/bot)[A-Za-z0-9:_-]{20,}")
_KEY_TOKEN_RE = re.compile(
    r"(?i)\b(?:AIza[0-9A-Za-z_-]{20,}|ya29\.[0-9A-Za-z_-]+|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|sk_(?:live|test)_[A-Za-z0-9]{16,}|EA[A-Za-z0-9]{32,})\b"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_URL_USERINFO_RE = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@")


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    suffix = ""
    while raw and raw[-1] in ".,;:!?)]}":
        suffix = raw[-1] + suffix
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        netloc = parsed.netloc
        if parsed.username is not None or parsed.password is not None:
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            netloc = f"[REDACTED]@{host}"
        path = _TELEGRAM_BOT_RE.sub(r"\1[REDACTED]", parsed.path)
        query = urlencode([
            (key, "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ])
        fragment = parsed.fragment
        if "=" in fragment:
            fragment = urlencode([
                (key, "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else value)
                for key, value in parse_qsl(fragment, keep_blank_values=True)
            ])
        return urlunsplit((parsed.scheme, netloc, path, query, fragment)) + suffix
    except (TypeError, ValueError):
        return "[REDACTED_URL]" + suffix


def redact_sensitive_text(value: object) -> str:
    text = str(value or "")
    text = _URL_RE.sub(_redact_url, text)
    text = _URL_USERINFO_RE.sub(r"\1[REDACTED]@", text)
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _BASIC_RE.sub(r"\1[REDACTED]", text)
    text = _ASSIGNMENT_RE.sub(r"\1\2[REDACTED]\4", text)
    text = _KEY_TOKEN_RE.sub("[REDACTED]", text)
    text = _JWT_RE.sub("[REDACTED]", text)
    return text


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_sensitive_text(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "[log message suppressed]"
            record.args = ()
        if record.exc_info:
            try:
                record.exc_text = redact_sensitive_text("".join(traceback.format_exception(*record.exc_info)))
            except Exception:
                record.exc_text = "[exception details suppressed]"
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_sensitive_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_sensitive_text(record.stack_info)
        return True


class SecureRotatingFileHandler(RotatingFileHandler):
    """ملف سجل بصلاحية 0600 من لحظة الإنشاء، لا بعد فتحه.

    ``open(path, "a")`` ينشئ الملف بـ0644 حتى ينفّذ chmod لاحقاً، وكل تدوير
    يعيد الفجوة نفسها. هنا يُفتح بصلاحية مقيّدة منذ البداية، ويرفض الروابط
    الرمزية بدل متابعتها.
    """

    def _open(self):
        if os.name != "posix":
            return super()._open()
        flags = os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= os.O_APPEND if "a" in self.mode else os.O_TRUNC
        descriptor = os.open(self.baseFilename, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            os.close(descriptor)
            raise
        return os.fdopen(descriptor, self.mode, encoding=self.encoding)


def secure_log_handler(handler: logging.Handler) -> logging.Handler:
    handler.addFilter(SensitiveDataFilter())
    return handler


def mask_secret(value: object, visible: int = 4) -> str:
    text = str(value or "")
    if not text:
        return ""
    visible_count = max(0, int(visible))
    if len(text) <= visible_count:
        return "•" * 8
    suffix = text[-visible_count:] if visible_count else ""
    return "•" * max(8, min(32, len(text) - visible_count)) + suffix
