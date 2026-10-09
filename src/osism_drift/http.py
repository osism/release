"""Shared HTTP transport and error-handling layer for remote source reads."""

import datetime
import os
import time
from urllib.parse import urlparse

import requests


class SourceError(Exception):
    """Raised on any read/list failure that should abort the run.

    `status` carries the HTTP status when the failure was an HTTP response, and
    is None for transport failures and non-HTTP read errors. A caller that
    treats some failures as inconclusive rather than fatal classifies on it
    instead of parsing the message (see drift/kolla_source_ref_phase.py).
    """

    def __init__(self, message, *, status: int | None = None):
        super().__init__(message)
        self.status = status


_GITHUB_HOSTS = frozenset({"github.com", "api.github.com", "raw.githubusercontent.com"})

# Per-run request tally, reported by the driver at the end of every run.
# _get and head are the only places drift checks send HTTP, so counting here
# makes each run state its own cost -- above all against GitHub's anonymous
# REST limit (60 requests an hour), which a nightly job without a token shares
# across every plugin group. Hosts are counted per hop. An archive download is
# a 302 from api.github.com to codeload.github.com; GitHub does not charge it to
# the REST rate limit (measured 2026-10-09: three anonymous tarball downloads
# left /rate_limit unchanged, one contents call took one), and the redirect's
# X-RateLimit-* headers do not reflect what was used. So archive requests are
# counted under their own label and their headers are ignored.
_requests_by_host: dict = {}
_github_limit: dict = {}
_LIMIT_FIELDS = ("limit", "remaining", "used", "reset")


def reset_stats() -> None:
    """Forget the tally; the driver calls this at the start of a run."""
    _requests_by_host.clear()
    _github_limit.clear()


def _is_archive(parsed) -> bool:
    return parsed.hostname == "api.github.com" and (
        "/tarball/" in parsed.path or "/zipball/" in parsed.path
    )


def _count_url(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or url
    if _is_archive(parsed):
        host += " archives"
    _requests_by_host[host] = _requests_by_host.get(host, 0) + 1


def _count_response(r) -> None:
    """Count every hop of `r`, and keep the latest api.github.com rate limit."""
    for hop in (*r.history, r):
        _count_url(hop.url)
        parsed = urlparse(hop.url)
        if (
            parsed.hostname == "api.github.com"
            and not _is_archive(parsed)
            and "X-RateLimit-Remaining" in hop.headers
        ):
            for field in _LIMIT_FIELDS:
                _github_limit[field] = hop.headers.get(f"X-RateLimit-{field.title()}")


def summary() -> str | None:
    """One line: requests per host, and the GitHub API budget left, if seen."""
    if not _requests_by_host:
        return None
    hosts = ", ".join(f"{h} {n}" for h, n in sorted(_requests_by_host.items()))
    line = f"HTTP requests: {hosts}"
    if _github_limit.get("remaining") is not None:
        line += (
            f"; GitHub API rate limit: {_github_limit['remaining']} of "
            f"{_github_limit.get('limit')} left"
        )
        reset = _github_limit.get("reset") or ""
        if reset.isdigit():
            when = datetime.datetime.fromtimestamp(int(reset), datetime.timezone.utc)
            line += f" (resets {when:%H:%M} UTC)"
    return line


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


def _rate_limit_hint(r, url: str) -> str | None:
    """A helpful hint when response `r` is a GitHub rate-limit rejection, else None.

    Hints are host-scoped, because everything below describes GitHub's limits
    specifically. A non-GitHub host gets at most a generic Retry-After echo:
    naming raw.githubusercontent.com in a failure that came from some other
    service would send the reader chasing the wrong one, and the --base-dir
    advice need not apply there at all. Reachable today through a non-GitHub
    github_raw/github_api override -- the same case _auth_headers already keeps
    a token from leaking into.

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
    if (urlparse(url).hostname or "") not in _GITHUB_HOSTS:
        if retry_after and retry_after.isdigit():
            return f"The host asked for a {retry_after}s wait before retrying."
        return None
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
    hint = _rate_limit_hint(r, url)
    if hint:
        msg = f"{msg} — {hint}"
    return SourceError(msg, status=r.status_code)


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
        _count_url(url)
        raise SourceError(f"network error {action} {url}: {e}") from e
    _count_response(r)
    if not r.ok and r.status_code not in ok:
        raise _http_error(action, url, r)
    return r


# Statuses worth one more attempt: throttling, and the server-side blips a
# static file server emits while a backend hiccups or a maintenance window
# starts. Everything else is a settled answer and is raised on the first try.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_HEAD_ATTEMPTS = 2
_HEAD_BACKOFF = 2.0


def head(
    action: str,
    url: str,
    *,
    ok=(),
    timeout: int = 30,
    attempts: int = _HEAD_ATTEMPTS,
    sleep=time.sleep,
):
    """HEAD `url` with auth + a timeout; return the Response.

    For existence probes. Same contract as _get: a transport failure, or a
    non-ok status whose code is not in `ok`, raises SourceError. The caller
    whitelists the codes that carry meaning for it (typically 404 for "absent")
    so that an outage or a throttled response cannot be mistaken for one.

    A transport failure or a transient status is retried up to `attempts` times
    with a linear backoff, because a probe sweep makes many small requests and
    one blip should not decide the outcome. The retry is insurance against a
    blip, not a throttling strategy: a host that keeps refusing still raises,
    and the caller decides what an unanswered probe means.
    """
    attempt = 0
    while True:
        attempt += 1
        final = attempt >= attempts
        try:
            r = requests.head(
                url, timeout=timeout, headers=_auth_headers(url), allow_redirects=True
            )
        except requests.RequestException as e:
            _count_url(url)
            if final:
                raise SourceError(f"network error {action} {url}: {e}") from e
        else:
            _count_response(r)
            if r.ok or r.status_code in ok:
                return r
            if final or r.status_code not in _TRANSIENT_STATUS:
                raise _http_error(action, url, r)
        sleep(_HEAD_BACKOFF * attempt)
