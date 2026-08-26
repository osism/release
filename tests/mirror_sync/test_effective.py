import pytest
from osism_drift.config import Config, Remote, SourceCfg
from osism_drift.mirror_sync import effective
from osism_drift.mirror_sync.model import SyncOpts


def _cfg(tmp_path, releases=("2024.1", "2025.1", "2025.2", "2026.1")):
    rel = tmp_path / "release" / "latest"
    rel.mkdir(parents=True)
    for r in releases:
        (rel / f"openstack-{r}.yml").write_text("openstack_projects: {cinder: x}\n")
    return Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        release_version="latest",
        plugins={},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        base_dirs=(str(tmp_path),),
    )


def test_effective_range_stops_at_target(tmp_path):
    # The whole point: --target 2025.2 must not drag 2026.1 in, or the 010 back-compat layer emits
    # compatibility data for the wrong release.
    assert effective.effective_range(_cfg(tmp_path), "2025.2") == [
        "2024.1",
        "2025.1",
        "2025.2",
    ]


def test_effective_range_rejects_unsupported_target(tmp_path):
    with pytest.raises(ValueError, match="not a supported release"):
        effective.effective_range(_cfg(tmp_path), "2027.1")


def test_derive_sets_releases_override_and_fresh_caches(tmp_path):
    base = _cfg(tmp_path)
    base.ref_cache["poison"] = "must-not-leak"
    shas = {"2024.1": "1" * 40, "2025.1": "2" * 40, "2025.2": "3" * 40}
    eff = effective.derive(base, SyncOpts(target="2025.2"), str(tmp_path / "wt"), shas)
    assert list(eff.releases) == ["2024.1", "2025.1", "2025.2"]
    # Pinned refs replace the branch for every upstream reader.
    assert eff.release_refs["kolla_ansible"]["2025.2"] == "3" * 40
    # Fresh per-run state: a memo from the unpinned config must not survive.
    assert "poison" not in eff.ref_cache
    assert eff.groupvars_cache == {}


def test_derive_prepends_the_worktree_parent_to_base_dirs(tmp_path):
    wt = tmp_path / "sync-2025.2" / "defaults"
    eff = effective.derive(
        _cfg(tmp_path), SyncOpts(target="2025.2"), str(wt), {"2025.2": "a" * 40}
    )
    # _local_repo_dir takes the FIRST base_dir containing <repo>; the worktree
    # parent must win over a user --base-dir that also holds a defaults checkout.
    assert eff.base_dirs[0] == str(wt.parent)
    assert str(tmp_path) in eff.base_dirs[1:]


def test_requested_shas_rejects_pin_and_pins_from_together():
    with pytest.raises(ValueError, match="pass one"):
        effective.requested_shas(
            SyncOpts(target="2025.2", pin="a" * 40, pins={"2025.2": "b" * 40}),
            ["2025.2"],
            {"2025.2": "c" * 40},
        )


def test_requested_shas_rejects_a_non_sha():
    with pytest.raises(ValueError, match="40-hex"):
        effective.requested_shas(
            SyncOpts(target="2025.2", pin="stable/2025.2"),
            ["2025.2"],
            {"2025.2": "c" * 40},
        )


def test_requested_shas_applies_the_pin_only_to_the_target():
    shas = effective.requested_shas(
        SyncOpts(target="2025.2", pin="a" * 40),
        ["2025.1", "2025.2"],
        {"2025.1": "b" * 40, "2025.2": "c" * 40},
    )
    assert shas == {"2025.1": "b" * 40, "2025.2": "a" * 40}


def test_check_pin_validates_against_the_real_tip_not_itself(monkeypatch):
    # Regression: an earlier draft passed a shas map into which the pin had already
    # been written, making the bound pin <= pin, which merge-base answers 0 for.
    seen = []

    def fake(repo, anc, desc, cfg):
        seen.append((anc, desc))
        return True

    monkeypatch.setattr(effective.source, "is_ancestor", fake)
    effective.check_pin(None, "2025.2", "p" * 40, "t" * 40, "b" * 40)
    assert ("p" * 40, "t" * 40) in seen  # pin vs TIP, never pin vs pin


def test_check_pin_rejects_a_commit_before_the_branch_point(monkeypatch):
    def fake(repo, anc, desc, cfg):
        return not (anc == "b" * 40 and desc == "p" * 40)  # range_base..pin False

    monkeypatch.setattr(effective.source, "is_ancestor", fake)
    with pytest.raises(effective.source.SourceError, match="before the branch point"):
        effective.check_pin(None, "2025.2", "p" * 40, "t" * 40, "b" * 40)


def test_check_pin_rejects_a_pin_not_reachable_from_the_tip(monkeypatch):
    monkeypatch.setattr(effective.source, "is_ancestor", lambda repo, a, d, cfg: False)
    with pytest.raises(effective.source.SourceError, match="not reachable"):
        effective.check_pin(None, "2025.2", "p" * 40, "t" * 40, "b" * 40)


def test_check_pin_is_a_noop_when_nothing_was_overridden(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not compare when pin == tip")

    monkeypatch.setattr(effective.source, "is_ancestor", boom)
    effective.check_pin(None, "2025.2", "t" * 40, "t" * 40, "b" * 40)


def test_validate_pins_payload_rejects_a_target_mismatch():
    with pytest.raises(ValueError, match="produced for target"):
        effective.validate_pins_payload(
            {"schema": 1, "target_release": "2026.1", "release_shas": {}},
            "2025.2",
            ["2025.2"],
        )


def test_validate_pins_payload_rejects_an_incomplete_release_set():
    with pytest.raises(ValueError, match="does not match the effective range"):
        effective.validate_pins_payload(
            {
                "schema": 1,
                "target_release": "2025.2",
                "release_shas": {"2025.2": "a" * 40},
                "defaults_base_sha": "d" * 40,
            },
            "2025.2",
            ["2025.1", "2025.2"],
        )


def test_validate_pins_payload_rejects_a_malformed_release_sha():
    # Validate the whole payload at its boundary rather than relying on a later
    # call in the intended sequence.
    with pytest.raises(ValueError, match="not a 40-hex"):
        effective.validate_pins_payload(
            {
                "schema": 1,
                "target_release": "2025.2",
                "release_shas": {"2025.1": "a" * 40, "2025.2": "nope"},
                "defaults_base_sha": "d" * 40,
            },
            "2025.2",
            ["2025.1", "2025.2"],
        )


def test_validate_pins_payload_rejects_a_non_mapping_release_shas():
    with pytest.raises(ValueError, match="not a mapping"):
        effective.validate_pins_payload(
            {"schema": 1, "target_release": "2025.2", "release_shas": ["a"]},
            "2025.2",
            ["2025.2"],
        )


def test_validate_pins_payload_rejects_an_unknown_schema():
    with pytest.raises(ValueError, match="unsupported schema"):
        effective.validate_pins_payload({"schema": 99}, "2025.2", ["2025.2"])


def test_validate_pins_payload_returns_the_map_and_base(tmp_path):
    shas, base = effective.validate_pins_payload(
        {
            "schema": 1,
            "target_release": "2025.2",
            "release_shas": {"2025.1": "a" * 40, "2025.2": "b" * 40},
            "defaults_base_sha": "d" * 40,
        },
        "2025.2",
        ["2025.1", "2025.2"],
    )
    assert shas == {"2025.1": "a" * 40, "2025.2": "b" * 40}
    assert base == "d" * 40


def test_resolve_tips_uses_release_to_ref_then_resolve_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        effective.source, "release_to_ref", lambda repo, rel, cfg: f"stable/{rel}"
    )
    monkeypatch.setattr(
        effective.source,
        "resolve_commit",
        lambda repo, ref, cfg: ref.replace("stable/", "").replace(".", "") * 8,
    )
    tips = effective.resolve_tips(_cfg(tmp_path), ["2025.1", "2025.2"])
    assert set(tips) == {"2025.1", "2025.2"}
    assert tips["2025.2"].startswith("20252")
