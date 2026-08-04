from pathlib import Path
import pytest
import requests
import responses
from osism_drift import http
from osism_drift.config import Allowlist, AllowEntry, Config, PluginCfg, Remote
from osism_drift.http import SourceError
from osism_drift.model import DriftEntry
from osism_drift.drift import kolla_source_ref_phase as plugin

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"
TARBALLS = "https://tarballs.opendev.org/openstack"


@pytest.fixture(autouse=True)
def _no_backoff_wait(monkeypatch):
    """head() retries a transient failure; don't spend the backoff in tests."""
    monkeypatch.setattr(http, "_HEAD_BACKOFF", 0)


def _exists(*present):
    """An existence stub: the (project, ref) pairs opendev publishes."""
    have = set(present)
    return lambda project, ref: (project, ref) in have


def _inconclusive(kind="HTTP 503", detail="HTTP 503 probing foo"):
    """An existence stub whose probe never settles."""

    def exists(project, ref):
        raise plugin.ProbeInconclusive(kind, detail)

    return exists


def test_flags_ref_when_a_later_phase_tarball_is_published():
    """stable-B is still served but frozen; unmaintained-B is the live artifact."""
    got = plugin.scan({"foo": "stable-B"}, "B", _exists(("foo", "unmaintained-B")))
    assert got.stale == [("foo", "stable-B", "unmaintained-B")]
    assert got.unresolved == []


def test_silent_when_no_later_phase_is_published():
    """The EOL case: nothing later exists, so the frozen tarball is all there is."""
    assert plugin.scan({"foo": "stable-B"}, "B", _exists()).stale == []


def test_silent_when_already_on_the_latest_published_phase():
    """Configured unmaintained-B, and nothing later: correct, no drift."""
    got = plugin.scan(
        {"foo": "unmaintained-B"}, "B", _exists(("foo", "unmaintained-B"))
    )
    assert got.stale == []


def test_does_not_probe_or_flag_the_configured_phase_itself():
    """Only later phases matter, so the configured ref is never probed."""
    probed = []

    def exists(project, ref):
        probed.append(ref)
        return False

    assert plugin.scan({"foo": "unmaintained-B"}, "B", exists).stale == []
    assert probed == []


def test_probes_only_the_phases_opendev_publishes_a_tarball_for():
    """The -eom/-eol tags get no tarball, so probing them is wasted work.

    Upstream tags both transitions -- <r>-eom entering the unmaintained phase,
    <r>-eol on deletion -- but no tarball is built for a tag. A candidate that
    can never resolve costs a request per project and offers a target nobody can
    download.
    """
    probed = []

    def exists(project, ref):
        probed.append(ref)
        return False

    plugin.scan({"foo": "stable-B"}, "B", exists)
    assert probed == ["unmaintained-B"]


def test_a_release_already_moved_costs_no_probes():
    """unmaintained is the last published phase, so nothing is left to check."""
    probed = []

    def exists(project, ref):
        probed.append(ref)
        return False

    plugin.scan({"a": "unmaintained-B", "b": "unmaintained-B"}, "B", exists)
    assert probed == []


def test_later_phases_are_only_those_after_the_configured_one():
    """The ordering the scan depends on, without the phases that have no tarball."""
    assert plugin._later_phases("B", "stable-B") == ["unmaintained-B"]
    assert plugin._later_phases("B", "unmaintained-B") == []
    assert plugin._later_phases("B", "stable/4.6") == []


def test_skips_ref_that_does_not_name_the_release():
    """gnocchi-style independent branch naming (stable/4.6) is out of scope."""
    got = plugin.scan(
        {"gnocchi": "stable/4.6"}, "B", _exists(("gnocchi", "unmaintained-B"))
    )
    assert got.stale == []


def test_skips_valueless_ref():
    got = plugin.scan({"nova": None}, "B", _exists(("nova", "unmaintained-B")))
    assert got.stale == []


def test_preserves_hyphenated_project_names():
    got = plugin.scan(
        {"neutron-dynamic-routing": "stable-B"},
        "B",
        _exists(("neutron-dynamic-routing", "unmaintained-B")),
    )
    assert got.stale == [("neutron-dynamic-routing", "stable-B", "unmaintained-B")]


# --- an unanswered probe is unknown, not clean -------------------------------


def test_inconclusive_probe_is_reported_unresolved_not_clean():
    """The blind spot this plugin closes: a failed probe must not read as 'no drift'."""
    got = plugin.scan({"foo": "stable-B"}, "B", _inconclusive("HTTP 503", "boom"))
    assert got.stale == []
    assert got.unresolved == [("foo", "stable-B", "HTTP 503", "boom")]


def test_inconclusive_probe_does_not_also_yield_a_stale_finding():
    """With one candidate unknown, naming a target phase would be a guess."""
    got = plugin.scan({"foo": "stable-B"}, "B", _inconclusive())
    assert [p for p, *_ in got.stale] == []


def test_one_inconclusive_project_does_not_hide_the_others():
    """A per-project failure must not cost the whole release's findings."""

    def exists(project, ref):
        if project == "bad":
            raise plugin.ProbeInconclusive("HTTP 503", "boom")
        return True

    got = plugin.scan({"bad": "stable-B", "good": "stable-B"}, "B", exists)
    assert got.stale == [("good", "stable-B", "unmaintained-B")]
    assert [p for p, *_ in got.unresolved] == ["bad"]


def test_probe_inconclusive_is_a_source_error():
    """So an uncaught one degrades to the ordinary abort, not a traceback."""
    assert issubclass(plugin.ProbeInconclusive, SourceError)


def _mock_tarball(project, ref, status):
    responses.add(
        responses.HEAD, f"{TARBALLS}/{project}/{project}-{ref}.tar.gz", status=status
    )


@responses.activate
def test_tarball_exists_true_for_a_published_artifact():
    _mock_tarball("nova", "unmaintained-2024.1", 200)
    assert plugin.tarball_exists("nova", "unmaintained-2024.1") is True


@responses.activate
def test_tarball_exists_false_when_absent():
    _mock_tarball("nova", "unmaintained-2024.1", 404)
    assert plugin.tarball_exists("nova", "unmaintained-2024.1") is False


@responses.activate
def test_tarball_exists_raises_instead_of_reporting_absent_on_outage():
    """An upstream 5xx must not be read as 'no later phase' and silently hide drift."""
    _mock_tarball("nova", "unmaintained-2024.1", 503)
    with pytest.raises(plugin.ProbeInconclusive) as e:
        plugin.tarball_exists("nova", "unmaintained-2024.1")
    assert e.value.kind == "HTTP 503"


@responses.activate
def test_tarball_exists_classifies_a_transport_failure():
    """No status to report, but still inconclusive rather than absent."""
    responses.add(
        responses.HEAD,
        f"{TARBALLS}/nova/nova-unmaintained-2024.1.tar.gz",
        body=requests.exceptions.ConnectionError("boom"),
    )
    with pytest.raises(plugin.ProbeInconclusive) as e:
        plugin.tarball_exists("nova", "unmaintained-2024.1")
    assert e.value.kind == "network error"


def test_declares_the_host_no_base_dir_can_serve():
    """The driver reads this to refuse a local-only run up front."""
    assert plugin.EXTERNAL_HOSTS == ("tarballs.opendev.org",)


def _entry(project, release="2024.1", latest="unmaintained-2024.1"):
    return DriftEntry(
        plugin=plugin.NAME,
        image=project,
        alias=project,
        expected=latest,
        found=f"stable-{release}",
        expected_src=plugin.EXPECTED_SRC,
        found_src=f"osism/release latest/openstack-{release}.yml",
        remediation=plugin.remediation_for(release, latest),
    )


def _unresolved_entry(project, release="2024.1", kind="HTTP 503"):
    return DriftEntry(
        plugin=plugin.NAME,
        image=project,
        alias=project,
        expected=plugin.UNRESOLVED_EXPECTED,
        found=f"stable-{release}",
        expected_src=plugin.EXPECTED_SRC,
        found_src=f"osism/release latest/openstack-{release}.yml",
        summary=plugin.UNRESOLVED_SUMMARY,
        remediation=plugin.unresolved_remediation(kind),
    )


def test_report_names_the_target_phase_and_the_file_to_edit():
    """ "the later phase" is not actionable; the concrete phase must reach the report."""
    from osism_drift import report

    text = "\n".join(report.format_text([_entry("neutron")], [plugin]))
    assert "neutron" in text
    assert "unmaintained-2024.1" in text
    assert "latest/openstack-2024.1.yml" in text


def test_report_does_not_offer_a_phase_that_has_no_tarball():
    """Naming -eol/-eom as a target sends the reader after a 404."""
    from osism_drift import report

    text = "\n".join(report.format_text([_entry("neutron")], [plugin]))
    assert "2024.1-eol" not in text
    assert "2024.1-eom" not in text


def test_projects_moving_to_the_same_phase_share_one_report_block():
    """Per-entry remediation must not embed the project, or the report fragments."""
    from osism_drift import report

    drifts = [_entry("neutron"), _entry("nova")]
    text = "\n".join(report.format_text(drifts, [plugin]))
    assert text.count(plugin.NAME) == 1
    assert "neutron, nova" in text


def test_plugin_level_remediation_describes_the_rename_without_inventing_phases():
    """The fallback constant is used when an entry carries no override."""
    assert "stable-<release>" in plugin.REMEDIATION
    assert "unmaintained/<release>" in plugin.REMEDIATION
    assert "-eol" not in plugin.REMEDIATION
    assert "-eom" not in plugin.REMEDIATION


def test_unresolved_report_says_unknown_rather_than_clean():
    """An operator must not read a failed probe as a passing check."""
    from osism_drift import report

    text = "\n".join(report.format_text([_unresolved_entry("neutron")], [plugin]))
    flowed = " ".join(text.split())  # the report wraps at 76 columns
    assert "could not be established" in flowed
    assert "unknown rather than confirmed current" in flowed
    assert "HTTP 503" in flowed
    assert "neutron" in text


def test_unresolved_refs_failing_alike_share_one_report_block():
    """Keying the remediation on the cause alone keeps the block from fragmenting."""
    from osism_drift import report

    drifts = [_unresolved_entry("neutron"), _unresolved_entry("nova")]
    text = "\n".join(report.format_text(drifts, [plugin]))
    assert text.count(plugin.NAME) == 1
    assert "neutron, nova" in text


def test_unresolved_and_stale_render_as_separate_blocks():
    """They call for different actions: move the ref vs re-run the check."""
    from osism_drift import report

    text = "\n".join(
        report.format_text([_entry("nova"), _unresolved_entry("neutron")], [plugin])
    )
    assert text.count(plugin.NAME) == 2


def test_unresolved_entry_is_actionable_so_the_run_exits_nonzero():
    """A check that could not answer is not a pass."""
    assert _unresolved_entry("nova").severity == "actionable"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://raw.githubusercontent.com/", f"{API}/", "main", "osism"),
        base_dirs=(str(FIXT),),
        remote_fallback=True,
        release_version="latest",
        plugins={"kolla_source_ref_phase": PluginCfg(enabled=True)},
        releases=("B",),
    )


@responses.activate
def test_run_reports_drift_read_from_the_release_manifest(cfg):
    """End to end over fixtures/release/latest/openstack-B.yml (all on stable-B)."""
    for project in ("foo", "bar", "multi-word"):
        _mock_tarball(project, "unmaintained-B", 200 if project == "foo" else 404)

    drifts = plugin.run(cfg, Allowlist(()))

    assert len(drifts) == 1
    d = drifts[0]
    assert (d.plugin, d.image, d.expected, d.found) == (
        "kolla_source_ref_phase",
        "foo",
        "unmaintained-B",
        "stable-B",
    )
    assert d.found_src == "osism/release latest/openstack-B.yml"
    assert "tarballs.opendev.org" in d.expected_src


@responses.activate
def test_run_reports_an_unverified_ref_when_the_probe_fails(cfg):
    """One project's outage becomes one unverified finding, not a lost run."""
    _mock_tarball("foo", "unmaintained-B", 200)
    for project in ("bar", "multi-word"):
        _mock_tarball(project, "unmaintained-B", 503)

    drifts = plugin.run(cfg, Allowlist(()))

    by_image = {d.image: d for d in drifts}
    assert by_image["foo"].expected == "unmaintained-B"
    assert by_image["bar"].expected == plugin.UNRESOLVED_EXPECTED
    assert "HTTP 503" in by_image["bar"].remediation


@responses.activate
def test_run_allowlists_an_unverified_ref_for_a_pinned_project(cfg):
    """A deliberately pinned project's phase does not matter, probe or no probe."""
    for project in ("foo", "bar", "multi-word"):
        _mock_tarball(project, "unmaintained-B", 503)
    allowlist = Allowlist(
        (AllowEntry(plugin=plugin.NAME, image="bar", reason="pinned on purpose"),)
    )

    drifts = plugin.run(cfg, allowlist)

    by_image = {d.image: d for d in drifts}
    assert by_image["bar"].allowlisted is True
    assert by_image["foo"].allowlisted is False
