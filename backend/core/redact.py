"""Keep API keys out of logs and error messages.

redact() strips key-carrying query parameters (appid, apiKey, key, api_key) and the literal value of
every configured API key. install_log_redaction() applies it to every log record when it is created,
so all handlers (including test log capture) only ever see redacted text.
"""

import logging
import re

KEY_PARAMS = ("appid", "apikey", "api_key", "key")
_PARAM = re.compile(rf"(?i)(?<![\w-])({'|'.join(KEY_PARAMS)})=([^&\s'\"<>]+)")
MASK = "***"
MIN_SECRET_LEN = 8  # don't mask short strings that happen to be configured


def _secrets() -> list[str]:
    try:
        from backend.core.config import get_settings

        settings = get_settings()
        values = [settings.openweather_api_key, settings.geoapify_api_key, settings.google_api_key,
                  settings.langsmith_api_key]
        return [v for v in (s.get_secret_value() for s in values) if len(v) >= MIN_SECRET_LEN]
    except Exception:  # settings unavailable (e.g. while config is being imported): params only
        return []


def redact(text: str) -> str:
    """`text` with API keys masked: query parameters like appid=..., and configured key values."""
    text = _PARAM.sub(rf"\1={MASK}", text)
    for secret in _secrets():
        text = text.replace(secret, MASK)
    return text


_installed = False


def install_log_redaction() -> None:
    """Redact every log record's message and traceback as it is created (idempotent)."""
    global _installed
    if _installed:
        return
    make_record = logging.getLogRecordFactory()

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = make_record(*args, **kwargs)
        try:
            record.msg, record.args = redact(record.getMessage()), None
        except Exception:  # a malformed log call: leave it to logging's own error handling
            return record
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
