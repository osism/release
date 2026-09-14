"""Tests for kolla_retired_patch_orphan."""

import pytest
import responses
from osism_drift.config import (
    Config,
    Remote,
    PluginCfg,
    SourceCfg,
    Allowlist,
    AllowEntry,
)
from osism_drift import source
from osism_drift.source import SourceError
from osism_drift.drift import kolla_retired_patch_orphan as plugin

API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"

# Three releases: A (oldest) < B (middle) < C (newest).
RELEASES = ("A", "B", "C")
NEWEST = "C"


# ---------------------------------------------------------------------------
# Fixture / config helpers
# ---------------------------------------------------------------------------


def _write_kolla_yml(tmp_path, keys):
    d = tmp_path / "defaults" / "all"
    d.mkdir(parents=True, exist_ok=True)
    content = "".join(f'{k}: "yes"\n' for k in keys)
    (d / "099-kolla.yml").write_text(content or "# empty\n")


def _write_patches(tmp_path, patches_by_rel):
    """patches_by_rel: {release: {filename: content_str}}"""
    base = tmp_path / "container-image-kolla-ansible" / "patches"
    for rel, files in patches_by_rel.items():
        d = base / rel
        d.mkdir(parents=True, exist_ok=True)
        for fname, content in files.items():
            (d / fname).write_text(content)


def _cfg(tmp_path, releases=RELEASES):
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        remote_fallback=True,
        release_version="latest",
        plugins={plugin.NAME: PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=releases,
    )


# ---------------------------------------------------------------------------
# Upstream mock helpers (kolla-ansible is pinned — always remote)
# ---------------------------------------------------------------------------


def _mock_ref(release):
    responses.add(
        responses.GET,
        f"{API}/openstack/kolla-ansible/commits/stable/{release}",
        status=200,
    )


def _mock_groupvars(release, keys=()):
    """Mock monolithic group_vars/all.yml with the given top-level keys."""
    body = "".join(f'{k}: "x"\n' for k in keys) or "# empty\n"
    responses.add(
        responses.GET,
        f"{RAW}/openstack/kolla-ansible/stable/{release}/ansible/group_vars/all.yml",
        body=body.encode(),
        status=200,
    )


def _mock_roles(release, role_defs=None):
    """role_defs: {role_name: yaml_body_str}"""
    role_defs = role_defs or {}
    responses.add(
        responses.GET,
        f"{API}/openstack/kolla-ansible/contents/ansible/roles?ref=stable/{release}",
        json=[{"name": r, "type": "dir"} for r in role_defs],
        status=200,
    )
    for role, body in role_defs.items():
        responses.add(
            responses.GET,
            f"{RAW}/openstack/kolla-ansible/stable/{release}/ansible/roles/{role}/defaults/main.yml",
            body=(body.encode() if isinstance(body, str) else body),
            status=200,
        )


def _mock_upstream(releases=RELEASES, gv_by_rel=None, roles_by_rel=None):
    """Mock all upstream calls for every release; empty definitions by default."""
    gv_by_rel = gv_by_rel or {}
    roles_by_rel = roles_by_rel or {}
    for r in releases:
        _mock_ref(r)
        _mock_groupvars(r, gv_by_rel.get(r, ()))
        _mock_roles(r, roles_by_rel.get(r))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@responses.activate
def test_reports_retired_patch_orphan(tmp_path):
    """Key in older patches, absent from newest patch dir, absent upstream → reported."""
    _write_kolla_yml(tmp_path, ["my_orphan_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_orphan_key: something\n"},
            "B": {"fix.patch": "my_orphan_key: something\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["my_orphan_key"]
    assert not drifts[0].allowlisted


@responses.activate
def test_disabled_patch_at_newest_is_consumer(tmp_path):
    """CENTRAL: .disabled patch at newest release counts as a consumer — not reported.

    #808 parked patches as .disabled at 2025.1 bring-up; four were re-enabled
    in-cycle.  This test pins that .disabled is parking state, not retirement.
    """
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "B": {"fix.patch": "my_key: something\n"},
            "C": {
                "fix.patch.disabled": "my_key: something\n",
                "sentinel.patch": "# ensures newest dir is non-empty\n",
            },
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert drifts == []


@responses.activate
def test_live_patch_at_newest_not_reported(tmp_path):
    """Key with a live (non-.disabled) patch at the newest release → not reported."""
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "C": {"fix.patch": "my_key: something\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert drifts == []


@responses.activate
def test_defined_upstream_in_groupvars_not_reported(tmp_path):
    """Key consumed by an older patch but defined in upstream group_vars/all → not reported."""
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    _mock_upstream(gv_by_rel={"B": ["my_key"]})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert drifts == []


@responses.activate
def test_defined_upstream_in_role_default_only_not_reported(tmp_path):
    """Decision 3: upstream role defaults count as upstream definitions — not reported."""
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    _mock_upstream(roles_by_rel={"B": {"my_role": 'my_key: "x"\n'}})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert drifts == []


@responses.activate
def test_defined_upstream_at_older_release_only_not_reported(tmp_path):
    """Union runs over ALL supported releases; defined at any one → not reported.

    This is the case a stable/-only ref assumption silently breaks: if 2024.1
    resolves to unmaintained/2024.1 and the code guesses stable/2024.1, the
    older-release definition is missed and a false positive fires.
    """
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    # my_key defined only at release A (older), absent at B and C.
    _mock_upstream(gv_by_rel={"A": ["my_key"]})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert drifts == []


@responses.activate
def test_mentioned_upstream_but_not_defined_is_reported(tmp_path):
    """Decision 3 is definition-based: a key that only appears in a template value
    is NOT counted as an upstream definition — the key is still reported.
    """
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    # my_key appears only as a value reference, not as a top-level YAML key.
    _mock_upstream(roles_by_rel={"A": {"my_role": 'other_key: "{{ my_key }}"\n'}})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["my_key"]


@responses.activate
def test_owned_by_dead_non_allowlisted_service_not_reported(tmp_path, monkeypatch):
    """Key owned by a dead non-allowlisted service is skipped; kolla_orphan_config
    already covers it and the two plugins must not double-report.
    """
    from osism_drift.drift import kolla_enablement_orphan

    monkeypatch.setattr(
        kolla_enablement_orphan, "orphan_ids", lambda config: {"dead_svc"}
    )
    _write_kolla_yml(tmp_path, ["dead_svc_port"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "dead_svc_port: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert drifts == []


@responses.activate
def test_owned_by_dead_allowlisted_service_is_reported(tmp_path, monkeypatch):
    """Decision 4 regression: kolla_orphan_config uses the allowlist-filtered dead
    set, so an allowlisted service's keys are NOT reported by it.  If this plugin
    also used the raw orphan_ids() set, those keys would go unreported by BOTH.
    kolla_operations is the motivating example.
    """
    from osism_drift.drift import kolla_enablement_orphan

    monkeypatch.setattr(
        kolla_enablement_orphan, "orphan_ids", lambda config: {"kolla_operations"}
    )
    _write_kolla_yml(tmp_path, ["kolla_operations_extra_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"kolla-operations.patch": "kolla_operations_extra_key: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    al = Allowlist(
        (
            AllowEntry(
                plugin=kolla_enablement_orphan.NAME,
                image="kolla_operations",
                reason="OSISM invention",
            ),
        )
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), al)
    assert [d.image for d in drifts] == ["kolla_operations_extra_key"]


@responses.activate
def test_never_consumed_by_patch_not_reported(tmp_path):
    """Condition 1: a key never mentioned in any patch dir is not a candidate."""
    _write_kolla_yml(tmp_path, ["unconsumable_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "other_key: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert drifts == []


@responses.activate
def test_substring_safety(tmp_path):
    """foo_bar is not consumed by a patch that only mentions foo_bar_baz."""
    _write_kolla_yml(tmp_path, ["foo_bar"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "foo_bar_baz: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert drifts == []


@responses.activate
def test_allowlist_applies(tmp_path):
    """An allowlisted key is returned allowlisted, not silently dropped."""
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    al = Allowlist(
        (AllowEntry(plugin=plugin.NAME, image="my_key", reason="intentionally kept"),)
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), al)
    assert len(drifts) == 1
    assert drifts[0].image == "my_key"
    assert drifts[0].allowlisted is True


@responses.activate
def test_advisory_severity(tmp_path):
    """Findings are advisory — the check tests 'consumed', not 'functional'."""
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert len(drifts) == 1
    assert drifts[0].severity == "advisory"


def test_fail_closed_empty_release_range(tmp_path):
    """Empty release range → SourceError (not a silent all-clear)."""
    _write_kolla_yml(tmp_path, ["my_key"])
    # Create an empty release/latest/ so release_range() returns [].
    (tmp_path / "release" / "latest").mkdir(parents=True)
    cfg = Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        release_version="latest",
        plugins={plugin.NAME: PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=(),  # empty → derive from release/latest/ which has no files
    )
    with pytest.raises(SourceError, match="release range"):
        plugin.run(cfg, Allowlist(()))


def test_fail_closed_empty_kolla_yml(tmp_path):
    """Empty 099-kolla.yml → SourceError (not a silent zero-drift result)."""
    d = tmp_path / "defaults" / "all"
    d.mkdir(parents=True)
    (d / "099-kolla.yml").write_text("# no top-level keys\n")
    with pytest.raises(SourceError, match="099-kolla.yml"):
        plugin.run(_cfg(tmp_path), Allowlist(()))


def test_fail_closed_newest_patches_missing(tmp_path):
    """Decision 6: patches/<newest> absent → SourceError (absence of evidence ≠ evidence
    of absence — the newest release dir may not exist yet when this runs).
    """
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            # patches/C is not created
        },
    )
    with pytest.raises(SourceError, match="patches/C"):
        plugin.run(_cfg(tmp_path), Allowlist(()))


def test_fail_closed_newest_patches_empty(tmp_path):
    """Decision 6: empty patches/<newest> dir → SourceError."""
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(tmp_path, {"A": {"fix.patch": "my_key: something\n"}})
    (tmp_path / "container-image-kolla-ansible" / "patches" / "C").mkdir(parents=True)
    with pytest.raises(SourceError, match="patches/C"):
        plugin.run(_cfg(tmp_path), Allowlist(()))


def _fail_reading(monkeypatch, bad_path):
    """Make source.read raise for one patch path, as a timeout or 429 would."""
    real = source.read

    def fake(repo, rel_path, config):
        if rel_path == bad_path:
            raise SourceError(f"network error fetching {rel_path}")
        return real(repo, rel_path, config)

    monkeypatch.setattr(source, "read", fake)


def test_unreadable_newest_patch_raises(tmp_path, monkeypatch):
    """An unreadable newest-release patch raises instead of reporting a finding.

    Every path scanned came from list_tree at the same ref, so a failed read is
    a transport failure, not an absence. Treating it as "no consumers here"
    would report my_key as retired although patches/C/keep.patch consumes it.
    """
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "C": {"keep.patch": "my_key: something\n"},
        },
    )
    _fail_reading(monkeypatch, "patches/C/keep.patch")
    with pytest.raises(SourceError, match="keep.patch"):
        plugin.run(_cfg(tmp_path), Allowlist(()))


def test_unreadable_older_patch_raises(tmp_path, monkeypatch):
    """An unreadable older-release patch raises instead of returning no drift.

    The opposite direction: swallowing it would drop the only evidence that
    my_key was ever consumed, silently suppressing a legitimate finding.
    """
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: something\n"},
            "C": {"other.patch": "unrelated_key: x\n"},
        },
    )
    _fail_reading(monkeypatch, "patches/A/fix.patch")
    with pytest.raises(SourceError, match="fix.patch"):
        plugin.run(_cfg(tmp_path), Allowlist(()))


@responses.activate
def test_missing_older_patches_dir_is_not_error(tmp_path):
    """A missing patches/<older_rel> dir means no consumers there; run continues.
    A key consumed at B (but not at the missing A) is still reported.
    """
    _write_kolla_yml(tmp_path, ["my_key"])
    # patches/A does NOT exist; patches/B has the key; patches/C doesn't.
    _write_patches(
        tmp_path,
        {
            "B": {"fix.patch": "my_key: something\n"},
            "C": {"sentinel.patch": "# newest must not be empty\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["my_key"]


@responses.activate
def test_retired_while_applied_uses_behaviour_change_block(tmp_path):
    """Patch still applied at the last release that carried it → behaviour changed.

    This is the kolla_disable_python_deprecation_warnings shape: the patch was
    live through the previous release, so removing it at the newest release
    stopped the behaviour it implemented.
    """
    _write_kolla_yml(tmp_path, ["applied_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "applied_key: yes\n"},
            "B": {"fix.patch": "applied_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["applied_key"]
    assert drifts[0].summary == plugin.SUMMARY
    assert plugin.REMEDIATION in drifts[0].remediation
    assert "applied at B" in drifts[0].found


@responses.activate
def test_retired_while_parked_uses_cleanup_block(tmp_path):
    """Patch already .disabled where it was last carried → nothing changed now.

    The kolla-operations shape: parked at B, dropped at C. The key stopped
    having an effect when the patch was parked, so the newest release changes
    nothing and the finding must not read as a behaviour regression.
    """
    _write_kolla_yml(tmp_path, ["parked_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"ops.patch": "parked_key: yes\n"},
            "B": {"ops.patch.disabled": "parked_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["parked_key"]
    assert drifts[0].summary == plugin.PARKED_SUMMARY
    assert plugin.PARKED_REMEDIATION in drifts[0].remediation
    assert "already .disabled at B" in drifts[0].found


@responses.activate
def test_applied_file_wins_over_disabled_at_same_release(tmp_path):
    """Two consumers at the last carrying release, one applied → treated as applied.

    A key mentioned by both an applied and a parked patch was still having an
    effect, so it belongs in the behaviour-change block and the applied file is
    the one worth naming.
    """
    _write_kolla_yml(tmp_path, ["mixed_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"ops.patch": "mixed_key: yes\n"},
            "B": {
                "ops.patch.disabled": "mixed_key: yes\n",
                "zz-live.patch": "mixed_key: yes\n",
            },
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["mixed_key"]
    assert drifts[0].summary == plugin.SUMMARY
    assert "zz-live.patch" in drifts[0].found
    assert "applied at B" in drifts[0].found


@responses.activate
def test_two_blocks_report_separately(tmp_path):
    """A run mixing both kinds keeps them in distinct report groups.

    report.py groups on (plugin, expected_src, found_src, summary, remediation),
    so distinct summary/remediation pairs are what actually split the blocks.
    """
    _write_kolla_yml(tmp_path, ["applied_key", "parked_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "applied_key: yes\n", "ops.patch": "parked_key: yes\n"},
            "B": {
                "fix.patch": "applied_key: yes\n",
                "ops.patch.disabled": "parked_key: yes\n",
            },
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert sorted(d.image for d in drifts) == ["applied_key", "parked_key"]
    groups = {(d.summary, d.remediation) for d in drifts}
    assert len(groups) == 2


@responses.activate
def test_found_carries_per_release_state(tmp_path):
    """found names the state at every release, so --format json records it."""
    _write_kolla_yml(tmp_path, ["parked_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"ops.patch": "parked_key: yes\n"},
            "B": {"ops.patch.disabled": "parked_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert (
        "still consumed at A (patch active); "
        "dead at B (patch parked) and C (patch absent)"
    ) in drifts[0].found
    # The representative-file prefix survives: the range does not name it.
    assert "already .disabled at B" in drifts[0].found


@responses.activate
def test_missing_older_patch_dir_renders_as_absent(tmp_path):
    """A release with no patches/ dir at all is 'patch absent', not omitted."""
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert (
        "still consumed at A (patch active); dead at B, C (patch absent)"
    ) in drifts[0].found


@responses.activate
def test_non_contiguous_live_range(tmp_path):
    """A patch parked at one release and revived at the next keeps both live.

    Four releases: active at A, parked at B, active again at C, gone at D.
    The live range is A and C -- not A through C.
    """
    rels = ("A", "B", "C", "D")
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: yes\n"},
            "B": {"fix.patch.disabled": "my_key: yes\n"},
            "C": {"fix.patch": "my_key: yes\n"},
            "D": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream(releases=rels)
    drifts = plugin.run(_cfg(tmp_path, releases=rels), Allowlist(()))
    assert (
        "still consumed at A, C (patch active); "
        "dead at B (patch parked) and D (patch absent)"
    ) in drifts[0].found


@responses.activate
def test_live_range_named_in_fix(tmp_path):
    """The remediation opens with the measured range, not a hedge."""
    _write_kolla_yml(tmp_path, ["parked_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"ops.patch": "parked_key: yes\n"},
            "B": {"ops.patch.disabled": "parked_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert drifts[0].remediation.startswith(
        "still consumed at A (patch active); "
        "dead at B (patch parked) and C (patch absent). "
    )


@responses.activate
def test_gate_boundary_is_release_after_newest_live(tmp_path):
    """The named boundary is the first release the key is no longer live at."""
    _write_kolla_yml(tmp_path, ["parked_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"ops.patch": "parked_key: yes\n"},
            "B": {"ops.patch.disabled": "parked_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert "openstack_version gate below B" in drifts[0].remediation
    assert "Do not simply remove it" in drifts[0].remediation


@responses.activate
def test_non_contiguous_gate_boundary(tmp_path):
    """Boundary follows the NEWEST live release, not the oldest dead one.

    Active at A, parked at B, active again at C, gone at D: gating below B
    would drop the key at C, where the patch is applied.
    """
    rels = ("A", "B", "C", "D")
    _write_kolla_yml(tmp_path, ["my_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "my_key: yes\n"},
            "B": {"fix.patch.disabled": "my_key: yes\n"},
            "C": {"fix.patch": "my_key: yes\n"},
            "D": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream(releases=rels)
    drifts = plugin.run(_cfg(tmp_path, releases=rels), Allowlist(()))
    assert "openstack_version gate below D" in drifts[0].remediation
    assert "below B" not in drifts[0].remediation


@responses.activate
def test_no_live_release_asks_for_removal(tmp_path):
    """Parked everywhere it was carried: nothing is live, so remove it."""
    _write_kolla_yml(tmp_path, ["parked_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"ops.patch.disabled": "parked_key: yes\n"},
            "B": {"ops.patch.disabled": "parked_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    rem = drifts[0].remediation
    assert rem.startswith("no supported release still applies its patch: ")
    assert "Remove it from osism/defaults all/099-kolla.yml." in rem
    assert "openstack_version gate" not in rem
    assert "Allowlist it instead" in rem


@responses.activate
def test_applied_block_keeps_and_does_not_order_a_gate(tmp_path):
    """The applied class gets the same constraint, and no imperative gate.

    Its reader may restore the behaviour instead of retiring it -- an ordered
    "Gate it below C" would tell them to break the patch they just wrote.
    """
    _write_kolla_yml(tmp_path, ["applied_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"fix.patch": "applied_key: yes\n"},
            "B": {"fix.patch": "applied_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    rem = drifts[0].remediation
    assert plugin.REMEDIATION in rem
    assert "openstack_version gate below C" in rem
    assert "Gate it below" not in rem


@responses.activate
def test_keep_branch_close_does_not_claim_removal_is_the_only_exit(tmp_path):
    """Keep branch: recurrence close, and no 'until the key is gone'.

    A consumer restored at the newest release clears the finding with the key
    still defined, so removal is not the only exit and the close must not say
    it is.
    """
    _write_kolla_yml(tmp_path, ["parked_key"])
    _write_patches(
        tmp_path,
        {
            "A": {"ops.patch": "parked_key: yes\n"},
            "B": {"ops.patch.disabled": "parked_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    rem = drifts[0].remediation
    assert "does not by itself clear this finding" in rem
    assert "until the key is gone" not in rem
    assert "Allowlist it instead" not in rem


@responses.activate
def test_gated_definition_still_reported(tmp_path):
    """A release-gated value is still a top-level key, so it still reports.

    This is what the closing sentence promises the reader: gating the value
    does not clear the finding.
    """
    ddir = tmp_path / "defaults" / "all"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "099-kolla.yml").write_text(
        "parked_key: \"{{ ['/x'] if openstack_version in ['A'] else [] }}\"\n"
    )
    _write_patches(
        tmp_path,
        {
            "A": {"ops.patch": "parked_key: yes\n"},
            "B": {"ops.patch.disabled": "parked_key: yes\n"},
            "C": {"other.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream()
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["parked_key"]


@responses.activate
def test_same_range_groups_one_block_different_ranges_split(tmp_path):
    """report.py groups on remediation, so the range is the block key.

    All three keys are in the same class (parked when last carried) and the
    same branch (keep), and all three have the SAME gate boundary -- so the
    range sentence is the only thing that can separate them. key_one and
    key_two share a range and must land in one block; key_three is live at one
    release fewer and must land in its own.
    """
    rels = ("A", "B", "C", "D")
    _write_kolla_yml(tmp_path, ["key_one", "key_two", "key_three"])
    _write_patches(
        tmp_path,
        {
            "A": {
                "ops.patch": "key_one: yes\nkey_two: yes\n",
                "other.patch.disabled": "key_three: yes\n",
            },
            "B": {
                "ops.patch": "key_one: yes\nkey_two: yes\n",
                "other.patch": "key_three: yes\n",
            },
            "C": {
                "ops.patch.disabled": "key_one: yes\nkey_two: yes\n",
                "other.patch.disabled": "key_three: yes\n",
            },
            "D": {"unrelated.patch": "# unrelated sentinel\n"},
        },
    )
    _mock_upstream(releases=rels)
    drifts = plugin.run(_cfg(tmp_path, releases=rels), Allowlist(()))
    by_key = {d.image: (d.summary, d.remediation) for d in drifts}
    assert sorted(by_key) == ["key_one", "key_three", "key_two"]

    # Same class, same branch, same boundary for all three.
    for key in by_key:
        summary, remediation = by_key[key]
        assert summary == plugin.PARKED_SUMMARY
        assert "openstack_version gate below C" in remediation

    # So only the range can split them, and it does.
    assert by_key["key_one"] == by_key["key_two"]
    assert by_key["key_three"] != by_key["key_one"]
    assert len(set(by_key.values())) == 2
    assert "still consumed at A, B (patch active)" in by_key["key_one"][1]
    assert "still consumed at B (patch active)" in by_key["key_three"][1]
