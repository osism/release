import json

from osism_drift.mirror_sync import render
from osism_drift.mirror_sync.model import Plan, Row, SyncOpts


def _plan(**kw):
    base = dict(
        target_release="2026.1",
        release_shas={"2025.2": "a" * 40, "2026.1": "b" * 40},
        defaults_base_sha="d" * 40,
        prev_release="2025.2",
        range_base_sha="c" * 40,
        mirror_writes={"all/001-nova.yml": b"x\n"},
        mirror_deletes=("all/001-zun.yml",),
        compat_writes={"all/010-2025.2.yml": "---\n"},
        compat_deletes=(),
        unowned_compat=(),
        changed_writes=("all/001-nova.yml",),
        created_paths=(),
        rows=(
            Row(
                "kolla_base_distro_version_default_map",
                "bookworm",
                "trixie",
                "semantic",
                ("536f4ae1a Add support for Debian Trixie (13)",),
                "",
            ),
            Row("notify_transport_url", "{% if a %}", "{%if a %}", "notation", (), ""),
            Row("enable_x", "no", False, "representation", (), "not proven inert"),
            Row(
                "openstack_release",
                "2025.2",
                "2026.1",
                "overridden",
                (),
                "supplied unconditionally by versions.yml",
            ),
        ),
        added_keys=("_ssl_modern_options",),
        dropped_keys={"enable_zun": "all/010-2025.2.yml"},
        added_files=("all/001-valkey.yml",),
        deleted_files=("all/001-zun.yml",),
    )
    base.update(kw)
    return Plan(**base)


def test_json_payload_is_deterministic_and_versioned():
    p = render.json_payload(_plan())
    assert p["schema"] == 1
    assert p["has_changes"] is True
    assert p["missing_dispositions"] == ["kolla_base_distro_version_default_map"]
    assert p["applied"] is None
    # No wall-clock anywhere, so two runs diff cleanly.
    assert "timestamp" not in json.dumps(p)
    # Pins round-trip for --pins-from, which validates these three fields.
    assert p["target_release"] == "2026.1"
    assert p["release_shas"]["2026.1"] == "b" * 40
    assert p["defaults_base_sha"] == "d" * 40


def test_json_payload_round_trips_through_the_pins_validator():
    from osism_drift.mirror_sync import effective

    payload = json.loads(json.dumps(render.json_payload(_plan())))
    shas, base = effective.validate_pins_payload(
        payload, "2026.1", ["2025.2", "2026.1"]
    )
    assert shas == {"2025.2": "a" * 40, "2026.1": "b" * 40}
    assert base == "d" * 40


def test_json_counts_each_class_separately():
    counts = render.json_payload(_plan())["counts"]
    assert counts == {
        "semantic": 1,
        "notation": 1,
        "representation": 1,
        "overridden": 1,
    }


def test_text_labels_representation_and_shows_attribution():
    out = render.text(_plan())
    assert "not proven inert" in out
    assert "536f4ae1a" in out  # attribution shown for semantic rows
    assert "all/010-2025.2.yml" in out  # dropped-key destination
    assert "all/001-valkey.yml" in out  # added file


def test_text_offers_only_the_dispositions_this_build_supports():
    out = render.text(_plan())
    assert "--accept-upstream" in out
    # --retain / --retain-unverified arrive with --apply; argparse rejects
    # them here, so pointing at them would send the operator to a dead option.
    assert "--retain" not in out


def test_text_distinguishes_no_rows_from_nothing_computed():
    empty = _plan(rows=(), changed_writes=(), mirror_deletes=(), compat_deletes=())
    out = render.text(empty)
    assert "no value differences" in out


def test_exit_code_2_when_a_blocker_exists_even_with_no_semantic_row():
    # An unowned 010 file stops --apply, so reporting 1 ("would apply cleanly")
    # would be false.
    p = _plan(rows=(), unowned_compat=("all/010-2025.1.yml",))
    assert render.exit_code(p, SyncOpts(target="2026.1")) == 2
    assert render.json_payload(p)["blocked"] == [
        "all/010-2025.1.yml lacks the generator marker"
    ]


def test_text_names_the_blocker_and_how_to_clear_it():
    out = render.text(_plan(unowned_compat=("all/010-2025.1.yml",)))
    assert "--apply is blocked" in out
    assert "generated-by: sync-mirror" in out


def test_exit_code_2_when_a_semantic_row_is_undispositioned():
    assert render.exit_code(_plan(), SyncOpts(target="2026.1")) == 2


def test_exit_code_1_when_changes_are_planned_and_all_dispositioned():
    opts = SyncOpts(
        target="2026.1",
        accept_upstream=frozenset({"kolla_base_distro_version_default_map"}),
    )
    assert render.exit_code(_plan(), opts) == 1


def test_exit_code_0_when_nothing_would_change():
    empty = _plan(rows=(), changed_writes=(), mirror_deletes=(), compat_deletes=())
    assert render.exit_code(empty, SyncOpts(target="2026.1")) == 0


def test_exit_code_1_when_only_files_change_and_no_row_needs_review():
    # A clean round: files added or dropped, nothing semantic. Neither "nothing to
    # do" nor "needs review" -- this is the case a single-axis status cannot state.
    p = _plan(rows=(), changed_writes=("all/001-new.yml",))
    assert render.exit_code(p, SyncOpts(target="2026.1")) == 1
    assert render.json_payload(p)["has_changes"] is True


def test_retain_flags_do_not_clear_a_missing_disposition():
    # They exist on SyncOpts for --apply, but the gate parser that gives them
    # meaning is not in this build; honouring them would report "all dispositioned"
    # without performing the checks they promise.
    opts = SyncOpts(
        target="2026.1",
        retain=frozenset({"kolla_base_distro_version_default_map"}),
        retain_unverified=frozenset({"kolla_base_distro_version_default_map"}),
    )
    assert render.exit_code(_plan(), opts) == 2
