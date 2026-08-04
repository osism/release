import pytest
import requests
import responses
from osism_drift import http
from osism_drift.http import SourceError

URL = "https://tarballs.opendev.org/openstack/nova/nova-unmaintained-2024.1.tar.gz"
GH = "https://api.github.com/repos/osism/release/contents/latest"


def _head(*args, **kwargs):
    """head() with the backoff wait removed, so tests don't sit in time.sleep."""
    kwargs.setdefault("sleep", lambda _s: None)
    return http.head(*args, **kwargs)


@responses.activate
def test_head_returns_the_response_for_a_published_artifact():
    responses.add(responses.HEAD, URL, status=200)
    assert _head("probing", URL).status_code == 200


@responses.activate
def test_head_returns_a_whitelisted_status_without_raising():
    """A 404 means 'absent' to a caller that whitelists it, not a failure."""
    responses.add(responses.HEAD, URL, status=404)
    assert _head("probing", URL, ok=(404,)).status_code == 404


@responses.activate
def test_head_raises_on_a_status_that_is_not_whitelisted():
    responses.add(responses.HEAD, URL, status=404)
    with pytest.raises(SourceError, match="HTTP 404 probing"):
        _head("probing", URL)


@responses.activate
def test_head_raises_on_server_error_even_when_404_is_whitelisted():
    """Only 404 may mean absent; a 5xx is an outage and must not read as absent."""
    responses.add(responses.HEAD, URL, status=503)
    with pytest.raises(SourceError, match="HTTP 503 probing"):
        _head("probing", URL, ok=(404,))


@responses.activate
def test_head_raises_on_rate_limit_even_when_404_is_whitelisted():
    responses.add(responses.HEAD, URL, status=429)
    with pytest.raises(SourceError, match="HTTP 429 probing"):
        _head("probing", URL, ok=(404,))


@responses.activate
def test_head_raises_source_error_on_transport_failure():
    responses.add(responses.HEAD, URL, body=requests.exceptions.ConnectionError("boom"))
    with pytest.raises(SourceError, match="network error probing"):
        _head("probing", URL)


@responses.activate
def test_head_reports_the_status_on_the_error():
    """So a caller can classify the failure without parsing the message."""
    responses.add(responses.HEAD, URL, status=503)
    with pytest.raises(SourceError) as e:
        _head("probing", URL)
    assert e.value.status == 503


@responses.activate
def test_head_leaves_the_status_unset_for_a_transport_failure():
    """There is no status to report, which is itself the distinction."""
    responses.add(responses.HEAD, URL, body=requests.exceptions.ConnectionError("boom"))
    with pytest.raises(SourceError) as e:
        _head("probing", URL)
    assert e.value.status is None


# --- retry: insurance against a blip, not a throttling strategy ---------------


@responses.activate
def test_head_retries_a_transient_status_and_returns_the_recovered_response():
    """One 503 in a long probe sweep should not decide the outcome."""
    responses.add(responses.HEAD, URL, status=503)
    responses.add(responses.HEAD, URL, status=200)
    assert _head("probing", URL).status_code == 200
    assert len(responses.calls) == 2


@responses.activate
def test_head_retries_a_transport_failure():
    responses.add(responses.HEAD, URL, body=requests.exceptions.ConnectionError("boom"))
    responses.add(responses.HEAD, URL, status=404)
    assert _head("probing", URL, ok=(404,)).status_code == 404


@responses.activate
def test_head_still_raises_when_the_host_keeps_refusing():
    """A retry does not turn a sustained outage into an answer."""
    responses.add(responses.HEAD, URL, status=503)
    responses.add(responses.HEAD, URL, status=503)
    with pytest.raises(SourceError, match="HTTP 503"):
        _head("probing", URL)
    assert len(responses.calls) == 2


@responses.activate
def test_head_does_not_retry_a_settled_answer():
    """A 403 is a decision, not a blip; retrying it only wastes a request."""
    responses.add(responses.HEAD, URL, status=403)
    with pytest.raises(SourceError, match="HTTP 403"):
        _head("probing", URL)
    assert len(responses.calls) == 1


@responses.activate
def test_head_does_not_retry_a_whitelisted_status():
    responses.add(responses.HEAD, URL, status=404)
    _head("probing", URL, ok=(404,))
    assert len(responses.calls) == 1


@responses.activate
def test_head_backs_off_between_attempts():
    """Retrying instantly would just hand the host two requests at once."""
    waited = []
    responses.add(responses.HEAD, URL, status=503)
    responses.add(responses.HEAD, URL, status=200)
    http.head("probing", URL, sleep=waited.append)
    assert waited == [http._HEAD_BACKOFF]


# --- rate-limit hints are host-scoped ----------------------------------------


@responses.activate
def test_a_non_github_throttle_does_not_blame_github():
    """Naming the wrong service sends the reader chasing the wrong problem.

    The --base-dir advice in the GitHub hint is doubly wrong here: no checkout
    can stand in for what opendev publishes.
    """
    responses.add(responses.HEAD, URL, status=429)
    with pytest.raises(SourceError) as e:
        _head("probing", URL, ok=(404,))
    assert "raw.githubusercontent.com" not in str(e.value)
    assert "--base-dir" not in str(e.value)


@responses.activate
def test_a_non_github_throttle_echoes_retry_after_when_offered():
    responses.add(responses.HEAD, URL, status=429, headers={"Retry-After": "30"})
    with pytest.raises(SourceError, match="30s"):
        _head("probing", URL, ok=(404,))


@responses.activate
def test_a_github_throttle_still_gets_the_github_hint():
    """The existing advice is right for the host it was written about."""
    responses.add(responses.HEAD, GH, status=429)
    with pytest.raises(SourceError, match="raw.githubusercontent.com"):
        _head("reading", GH)
