"""Shared HTTP transport and error-handling layer for remote source reads."""

import datetime
import os
from urllib.parse import urlparse

import requests


class SourceError(Exception):
    """Raised on any read/list failure that should abort the run."""


_GITHUB_HOSTS = frozenset({"github.com", "api.github.com", "raw.githubusercontent.com"})


def _auth_headers(url: str, extra: dict | None = None) -> dict:
    """Merge a GitHub bearer token into request headers when one is available
    AND `url` targets a public GitHub host.

    Reads GITHUB_TOKEN (then GH_TOKEN) from the environment. When set and the
    request goes to a GitHub host, the read is authenticated, lifting GitHub's
    unauthenticated 60/hr per-IP limit to 5000/hr; otherwise behaviour is
    unchanged (unauthenticated). Gating on the host keeps a token configured for
    github.com from leaking to a non-GitHub github_raw/github_api override. The
    Zuul periodic-daily job carries no token today, so this is a no-op there and
    a win for local/developer and any future authenticated runs.
    """
    headers = dict(extra or {})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and (urlparse(url).hostname or "") in _GITHUB_HOSTS:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _rate_limit_hint(r) -> str | None:
    """A helpful hint when response `r` is a GitHub rate-limit rejection, else None.

    GitHub reports its primary rate limit as HTTP 403 (or, more recently, 429)
    with X-RateLimit-Remaining: 0, and secondary limits as 403/429 with a
    Retry-After header. Unauthenticated requests share a low per-IP hourly
    budget that a full remote drift run can exhaust; a token lifts it (see
    _auth_headers), so the actionable advice differs by auth state. The exact
    quotas are not hardcoded here (GitHub changes them, and this string is read
    whenever the script runs, not when it was written): the actual limit in
    effect is echoed from the response's X-RateLimit-Limit header. A 403 without
    those markers is some other refusal (auth/permission), not throttling, and
    gets no hint.

    Separately, raw.githubusercontent.com (a Fastly CDN serving file bytes, the
    bulk of a remote run) throttles per-IP and returns 429 with none of those
    headers; that markerless 429 gets its own hint pointing at --base-dir, since
    a token does not draw the raw host from the API budget.
    """
    if r.status_code not in (403, 429):
        return None
    retry_after = r.headers.get("Retry-After")
    if r.headers.get("X-RateLimit-Remaining") != "0" and retry_after is None:
        # No GitHub-API rate-limit markers. raw.githubusercontent.com is a Fastly
        # CDN that throttles per-IP and returns 429 with none of these headers, so
        # a markerless 429 is CDN throttling (the API path always carries a marker)
        # and still deserves an actionable hint. A markerless 403, by contrast, is
        # an ordinary auth/permission refusal, not throttling, and gets none.
        if r.status_code == 429:
            return (
                "raw.githubusercontent.com throttled this request. Its rate limit "
                "is per-IP, intermittent, and separate from the GitHub API budget "
                "(a token may raise the anonymous tier but won't guarantee relief). "
                "Prefer local checkouts (--base-dir) to avoid remote fetches, retry "
                "later, or run from a different network."
            )
        return None
    parts = ["GitHub API rate limit hit."]
    limit = r.headers.get("X-RateLimit-Limit")
    quota = f" (limit in effect: {limit}/hr)" if limit and limit.isdigit() else ""
    reset = r.headers.get("X-RateLimit-Reset")
    if retry_after and retry_after.isdigit():
        parts.append(f"Retry after {retry_after}s.")
    elif reset and reset.isdigit():
        when = datetime.datetime.fromtimestamp(
            int(reset), datetime.timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"Limit resets at {when}.")
    if os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        parts.append(
            f"This run is authenticated{quota}; wait for the reset or reduce "
            "concurrency."
        )
    else:
        parts.append(
            f"This run is unauthenticated{quota}; set GITHUB_TOKEN (or GH_TOKEN) "
            "to use the much higher authenticated limit."
        )
    return " ".join(parts)


def _http_error(action: str, url: str, r) -> SourceError:
    """SourceError for a non-ok HTTP response, with a rate-limit hint appended
    when the response looks like GitHub throttling rather than a plain failure."""
    msg = f"HTTP {r.status_code} {action} {url}"
    hint = _rate_limit_hint(r)
    if hint:
        msg = f"{msg} — {hint}"
    return SourceError(msg)


_GH_JSON = {"Accept": "application/vnd.github.v3+json"}


def _get(action: str, url: str, *, json_api: bool = False, ok=(), timeout: int = 30):
    """GET `url` with auth + a timeout; return the Response.

    `json_api` sends the GitHub REST Accept header. A transport failure, or a
    non-ok status whose code is not in `ok`, raises SourceError (with a
    rate-limit hint via _http_error); the caller keeps its own handling for the
    codes it whitelists (e.g. a 404 that means "absent", not "failed").
    """
    extra = _GH_JSON if json_api else None
    try:
        r = requests.get(url, timeout=timeout, headers=_auth_headers(url, extra))
    except requests.RequestException as e:
        raise SourceError(f"network error {action} {url}: {e}") from e
    if not r.ok and r.status_code not in ok:
        raise _http_error(action, url, r)
    return r
