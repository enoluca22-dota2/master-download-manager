"""Token auth + localhost-only enforcement for the local API.

The API is only meant to be reachable from this machine's own browser, so
every request is checked twice: the server only binds to 127.0.0.1, and
this dependency independently re-checks the peer address in case something
proxies a request from elsewhere.

Header: X-MDM-Token. X-Enagrio-Token (from earlier versions of the browser
extension) is still accepted so an already-paired extension keeps working.
"""

import hmac
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 600
_request_log = defaultdict(deque)


def enforce_localhost(request: Request):
    client_host = request.client.host if request.client else None
    if client_host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="This API only accepts local connections.")


def enforce_rate_limit(request: Request):
    client_host = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _request_log[client_host]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many requests. Slow down.")
    bucket.append(now)


def make_token_verifier(settings_store):
    def verify_token(request: Request,
                     x_mdm_token: str = Header(default=None),
                     x_enagrio_token: str = Header(default=None)):
        enforce_localhost(request)
        enforce_rate_limit(request)
        expected = settings_store.get("api_token") or ""
        supplied = x_mdm_token or x_enagrio_token or ""
        # constant-time comparison: don't leak the token through timing
        if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="Missing or invalid X-MDM-Token header (security token).")
    return verify_token
