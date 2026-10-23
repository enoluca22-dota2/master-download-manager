"""Turn engine exceptions into something a person can act on.

Every failure is reported as three parts:
    reason      one short sentence ("The requested format is unavailable.")
    suggestion  what to do about it ("Choose another quality or use Best available.")
    technical   the original, redacted error text for "View technical details" / "Copy error"
"""

import dataclasses

from yt_dlp.utils import DownloadCancelled, DownloadError

from .logging_setup import redact


class PauseRequested(DownloadCancelled):
    """Raised from inside a progress hook / polling loop to stop the current
    transfer without treating it as a permanent cancellation. Shared by every
    engine so the queue's except-clauses stay engine-agnostic."""


class UnsupportedMediaError(Exception):
    """The URL isn't supported, or the media is protected (DRM). MDM never
    attempts to bypass DRM, sign-in walls, or paywalls."""


@dataclasses.dataclass
class ErrorInfo:
    reason: str
    suggestion: str = ""
    technical: str = ""

    def as_text(self):
        parts = ["Download failed", "", f"Reason:\n{self.reason}"]
        if self.suggestion:
            parts += ["", f"Suggested action:\n{self.suggestion}"]
        if self.technical:
            parts += ["", f"Technical details:\n{self.technical}"]
        return "\n".join(parts)


# (substrings to look for in the lowercased error text, reason, suggestion)
_RULES = [
    (("requested format is not available", "requested format not available"),
     "The requested format is unavailable.",
     "Choose another quality or use Best available."),
    (("drm",),
     "This media is DRM-protected and cannot be downloaded.",
     "Master Download Manager does not bypass DRM protection."),
    (("not a bot", "confirm you’re not a bot", "confirm you're not a bot"),
     "The site is asking to confirm you're not a bot.",
     "Try again later, or enable browser cookies from a signed-in browser in Settings → Authentication."),
    (("confirm your age", "age-restricted", "age restricted", "inappropriate for some users"),
     "This media is age-restricted.",
     "Enable browser cookies from a signed-in account in Settings → Authentication."),
    (("private video", "this video is private", "members-only", "join this channel", "sign in", "login required",
      "log in", "requires authentication", "you need to log in"),
     "This media is private or requires signing in.",
     "If you have access, enable browser cookies (or a cookie file) in Settings → Authentication."),
    (("not available in your country", "available in your country", "geo restricted", "geo-restricted",
      "not available in your region",
      "blocked it in your country", "georestricted"),
     "This media is not available in your region.",
     "If you're permitted to, configure a proxy in Settings → Network."),
    (("premieres in", "live event will begin", "this live event", "is not live", "waiting for scheduled stream"),
     "This live stream or premiere hasn't started yet.",
     "Try again once it has started."),
    (("video unavailable", "has been removed", "no longer available", "this video has been deleted", "does not exist"),
     "This media is unavailable (it may have been removed or made private).",
     "Check that the link still works in your browser."),
    (("http error 404", "404: not found", "404 client error"),
     "The server says the file was not found (HTTP 404).",
     "Check the link, or get a fresh one from the website."),
    (("http error 403", "403: forbidden", "403 client error"),
     "The server refused the request (HTTP 403).",
     "Retry later. If it keeps happening the link may have expired — get a fresh link."),
    (("http error 429", "too many requests", "429 client error"),
     "The site is rate-limiting requests (HTTP 429).",
     "Wait a while before retrying. Browser cookies from a signed-in account can also help."),
    (("http error 5", "502 bad gateway", "503 service", "504 gateway", "internal server error", "server error:"),
     "The server had a temporary problem.",
     "Master Download Manager retries automatically; you can also retry later."),
    (("unsupported url", "is not a valid url", "no suitable", "unable to extract", "no video formats found",
      "no media found"),
     "This URL isn't supported or no downloadable media was found on it.",
     "Check the link. For a file, use the direct download link."),
    (("ffprobe", "ffmpeg"),
     "FFmpeg failed or was not found. It's needed to merge video+audio and to convert audio.",
     "Reinstall Master Download Manager, or set the FFmpeg location in Settings → Advanced."),
    (("certificate verify failed", "ssl:", "ssl error", "certificate"),
     "A secure (HTTPS) connection couldn't be verified.",
     "Check your system clock and any antivirus/proxy that inspects HTTPS traffic."),
    (("proxy",),
     "The proxy connection failed.",
     "Check the proxy address in Settings → Network."),
    (("urlopen error", "temporary failure", "connection", "timed out", "timeout", "network", "getaddrinfo",
      "name resolution", "reset by peer", "unreachable", "incompleteread", "remote end closed"),
     "Network error — check your internet connection and try again.",
     "Master Download Manager retries automatically. Check your connection, VPN or proxy."),
]

TRANSIENT_HINTS = ("network", "timed out", "timeout", "connection", "temporary failure", "urlopen error",
                   "temporary problem", "reset by peer", "incompleteread", "http error 5", "rate-limiting")


def is_transient_error(message):
    lowered = (message or "").lower()
    return any(hint in lowered for hint in TRANSIENT_HINTS)


def explain_error(exc, extra_lines=()):
    """Map an exception (from any engine) to an ErrorInfo."""
    if isinstance(exc, PauseRequested):
        return ErrorInfo("Paused.")
    if isinstance(exc, DownloadCancelled):
        return ErrorInfo("Download cancelled.")

    raw = str(exc)
    technical = redact("\n".join([f"{type(exc).__name__}: {raw}", *extra_lines]).strip())
    text = raw.lower()

    if isinstance(exc, UnsupportedMediaError):
        return ErrorInfo(raw, "", technical)
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return ErrorInfo("Not enough disk space to finish this download.",
                         "Free up space or choose a different download folder.", technical)
    if isinstance(exc, PermissionError) or "permission denied" in text or "access is denied" in text:
        return ErrorInfo("Permission denied while writing to the download folder.",
                         "Choose a different folder in Settings → Downloads, or check the folder's permissions.",
                         technical)
    for needles, reason, suggestion in _RULES:
        if any(n in text for n in needles):
            return ErrorInfo(reason, suggestion, technical)
    if isinstance(exc, DownloadError):
        message = raw.replace("ERROR: ", "", 1).strip()
        return ErrorInfo(f"Download failed: {message}", "Retry, or choose a different format.", technical)
    return ErrorInfo(f"Unexpected error: {raw}", "Retry. If it keeps failing, copy the error and report it.",
                     technical)


def classify_error(exc):
    """Short user-facing reason (kept for callers that only want one line)."""
    return explain_error(exc).reason
