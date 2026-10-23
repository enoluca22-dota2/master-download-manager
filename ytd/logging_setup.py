"""Application logging.

Separate files under the data directory's logs/ folder:

    app.log      everything (all subsystems)
    download.log queue/engine decisions and the direct HTTP engine
    media.log    media engine (yt-dlp) messages
    torrent.log  torrent engine
    browser.log  local API used by the browser extensions

Secrets are redacted *inside* messages (query-string credentials, cookie and
authorization headers, the API token) rather than dropping whole lines, so
logs stay useful without ever containing credentials.
"""

import logging
import logging.handlers
import os
import re
import sys

from .config import LOG_DIR

ROOT_LOGGER = "ytd"

SUBSYSTEM_FILES = {
    "engine": "download.log",
    "http": "download.log",
    "media": "media.log",
    "torrent": "torrent.log",
    "api": "browser.log",
}

_SENSITIVE_PARAM = r"(?:token|access_token|api_key|apikey|key|sig|signature|auth|authorization|password|passwd|pwd|secret|session|sessionid|cookie|xt_token)"
_REDACTIONS = [
    # ?token=abc&sig=... -> ?token=[REDACTED]
    (re.compile(rf"(?i)([?&;]{_SENSITIVE_PARAM}=)[^&#\s'\"]+"), r"\1[REDACTED]"),
    # Cookie headers: everything to the end of the line (cookies are ';'-separated)
    (re.compile(r"(?i)\b(cookie|set-cookie)(\s*[:=]\s*)[^\r\n]+"), r"\1\2[REDACTED]"),
    # "Authorization: Bearer ...", "X-MDM-Token: ..."
    (re.compile(r"(?i)\b(authorization|proxy-authorization|x-[a-z-]*token)(\s*[:=]\s*)[^\r\n,;]+"),
     r"\1\2[REDACTED]"),
    # user:password@host in URLs (proxies!)
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^/\s@]+@"), r"\1[REDACTED]@"),
    # Explicit "password=..." / "token=..." outside a query string
    (re.compile(rf"(?i)\b({_SENSITIVE_PARAM})(\s*=\s*)(?!\[REDACTED\])[^\s,;&]+"), r"\1\2[REDACTED]"),
]

_known_secrets = set()


def register_secret(value):
    """Exact strings (e.g. the API token) that must never appear in any log."""
    if value and len(value) >= 8:
        _known_secrets.add(value)


def redact(text):
    text = str(text)
    for secret in list(_known_secrets):
        if secret in text:
            text = text.replace(secret, "[REDACTED]")
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not kill logging
            message = str(record.msg)
        cleaned = redact(message)
        if cleaned != message or record.args:
            record.msg = cleaned
            record.args = ()
        return True


def _file_handler(path, fmt):
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(fmt)
    handler.addFilter(RedactingFilter())
    return handler


def setup_logging(level=logging.INFO, log_dir=None):
    log_dir = log_dir or LOG_DIR
    logger = logging.getLogger(ROOT_LOGGER)
    if getattr(logger, "_mdm_configured", False):
        return logger
    os.makedirs(log_dir, exist_ok=True)
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    logger.addHandler(_file_handler(os.path.join(log_dir, "app.log"), fmt))
    handlers_by_file = {}
    for subsystem, filename in SUBSYSTEM_FILES.items():
        handler = handlers_by_file.get(filename)
        if handler is None:
            handler = _file_handler(os.path.join(log_dir, filename), fmt)
            handlers_by_file[filename] = handler
        logging.getLogger(f"{ROOT_LOGGER}.{subsystem}").addHandler(handler)

    if sys.stderr is not None:  # a windowed (no console) build has no stderr
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        console.addFilter(RedactingFilter())
        logger.addHandler(console)
    logger._mdm_configured = True
    return logger


def get_logger(subsystem=None):
    return logging.getLogger(f"{ROOT_LOGGER}.{subsystem}" if subsystem else ROOT_LOGGER)


class YtDlpLogger:
    """Adapter handed to yt-dlp as its `logger` param: routes its output into
    media.log (redacted) instead of stdout, which doesn't exist in a windowed
    build. Keeps the last error lines so failures can show technical detail."""

    def __init__(self, job_label=""):
        self._log = get_logger("media")
        self._prefix = f"[{job_label}] " if job_label else ""
        self.last_errors = []

    def debug(self, msg):
        if msg.startswith("[debug] "):
            self._log.debug("%s%s", self._prefix, msg)
        else:
            self._log.info("%s%s", self._prefix, msg)

    def info(self, msg):
        self._log.info("%s%s", self._prefix, msg)

    def warning(self, msg):
        self._log.warning("%s%s", self._prefix, msg)

    def error(self, msg):
        self.last_errors = (self.last_errors + [redact(msg)])[-5:]
        self._log.error("%s%s", self._prefix, msg)
