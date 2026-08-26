import json
import re

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


def test_text_offers_every_disposition_now_that_the_gate_exists():
    out = render.text(_plan())
    for flag in ("--accept-upstream", "--retain", "--retain-unverified"):
        assert flag in out


def test_text_lists_the_dispositions_that_were_applied():
    # validate_dispositions() computes these labels; a report that dropped them
    # would leave the operator unable to tell verified from merely acknowledged.
    out = render.text(_plan(), notes={"k": "verified against the canonical gate"})
    assert "dispositioned:" in out
    assert "k: verified against the canonical gate" in out


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
    # A clean re-sync: files added or dropped, nothing semantic. Neither
    # "nothing to do" nor "needs review" -- the case a single-axis status
    # cannot state.
    p = _plan(rows=(), changed_writes=("all/001-new.yml",))
    assert render.exit_code(p, SyncOpts(target="2026.1")) == 1
    assert render.json_payload(p)["has_changes"] is True


def test_a_retain_flag_clears_a_row_now_that_it_is_validated_elsewhere():
    """render no longer second-guesses the flags.

    gate.validate_dispositions() raises on a bad --retain before a report is ever
    rendered, so a flag present at this point is sound. Re-checking here would
    duplicate the logic and lose the diagnostic that makes the check useful.
    """
    opts = SyncOpts(
        target="2026.1",
        retain=frozenset({"kolla_base_distro_version_default_map"}),
    )
    assert render.exit_code(_plan(), opts) == 1


def test_acceptance_command_prepends_the_worktree_and_keeps_every_base_dir():
    wt = "/repos/osism/defaults.worktrees/sync-2025.2/defaults"
    cmd = render.acceptance_command(wt, ["/repos/osism", "/repos/openstack"])
    dirs = re.findall(r"--base-dir (\S+)", cmd)
    # The worktree parent must come FIRST: _local_repo_dir takes the first
    # --base-dir containing <repo>, so with the operator's checkout ahead of it the
    # detector measures the unsynced tree and looks like a pass on nothing.
    assert dirs[0] == "/repos/osism/defaults.worktrees/sync-2025.2"
    # Every configured root must survive -- dropping /repos/openstack loses
    # kolla-ansible, so the printed command cannot reproduce the check at all.
    assert dirs[1:] == ["/repos/osism", "/repos/openstack"]
    assert "--group all" in cmd
    # Any --base-dir puts check-drift in local mode and the worktree parent holds
    # only `defaults`; without this a repo absent from every root is a hard error.
    assert "--remote-fallback" in cmd


def test_acceptance_command_works_for_a_remote_mode_run():
    cmd = render.acceptance_command("/wt/sync-2025.2/defaults", [])
    assert re.findall(r"--base-dir (\S+)", cmd) == ["/wt/sync-2025.2"]
    assert "--remote-fallback" in cmd


def test_acceptance_command_quotes_paths_with_spaces():
    cmd = render.acceptance_command(
        "/repos/my defaults.worktrees/sync-2025.2/defaults", ["/repos/os ism"]
    )
    # Copy-pasted into a shell, an unquoted path with a space silently means
    # something else.
    assert "'/repos/my defaults.worktrees/sync-2025.2'" in cmd
    assert "'/repos/os ism'" in cmd


def test_acceptance_command_warns_when_the_derived_range_is_newer():
    """The printed command is not the gate when a newer release is declared.

    check-drift derives its range from latest/openstack-*.yml and has no flag
    to narrow it, so with 2026.1 declared it measures that release -- and a
    large count would otherwise read as a failure of a 2025.2 sync. Observed
    live: 230 to act on immediately after a clean apply.
    """
    cmd = render.acceptance_command(
        "/wt/sync-2025.2/defaults", [], target="2025.2", newest="2026.1"
    )
    assert "NOTE" in cmd and "2026.1" in cmd
    assert "releases:" in cmd


def test_acceptance_command_has_no_caveat_when_the_target_is_newest():
    cmd = render.acceptance_command(
        "/wt/sync-2026.1/defaults", [], target="2026.1", newest="2026.1"
    )
    assert "NOTE" not in cmd


def test_text_summary_names_each_class_and_what_it_means():
    """A bare "semantic (16)" does not tell a reader whether it needs them."""
    out = render.text(_plan())
    assert "4 mirror values changed. 1 need" in out
    assert "blocks --apply" in out
    assert "truth value did not" in out
    assert "an unconditional OSISM layer already supplies the key" in out


def test_text_inlines_a_short_value_but_elides_a_long_one():
    """The values worth reading are the ones a terminal renders worst."""
    long_new = "{{\n" + "  x or\n" * 40 + "}}"
    out = render.text(
        _plan(
            rows=(
                Row("short_key", "influxdb", "sqlalchemy", "semantic", (), ""),
                Row("long_key", "{{ x }}", long_new, "semantic", (), ""),
            )
        )
    )
    assert "short_key: 'influxdb' -> 'sqlalchemy'" in out
    assert "long_key: str, 7 chars -> multi-line str," in out
    assert "(see report)" in out
    assert "x or" not in out  # the body never reaches the terminal


def test_text_truncates_a_long_name_list_to_a_sample_and_a_count():
    keys = [f"enable_{i:03d}" for i in range(101)]
    out = render.text(
        _plan(rows=tuple(Row(k, "no", False, "representation", (), "n") for k in keys))
    )
    assert "and 89 more (see report)" in out
    assert "mores" not in out
    assert "enable_100" not in out  # past the cap


def test_text_groups_a_repeated_note_instead_of_repeating_it():
    """classify() emits a constant note per class; printing it 101 times is noise."""
    rows = tuple(
        Row(f"enable_{i}", "no", False, "representation", (), "not proven inert")
        for i in range(8)
    )
    out = render.text(_plan(rows=rows))
    assert out.count("not proven inert") == 1


def test_text_header_names_the_base_ref_alongside_its_sha():
    out = render.text(_plan(), SyncOpts(target="2026.1", base_ref="my-branch"))
    assert f"target        2026.1 @ {'b' * 40}" in out
    assert f"defaults base my-branch @ {'d' * 40}" in out


def test_text_states_the_exit_code_and_where_the_full_values_are():
    out = render.text(_plan(), code=2, report_path="/tmp/sync-mirror-2026.1.txt")
    assert "exit 2: operator action needed; the plan was not applied" in out
    assert "full values for every key: /tmp/sync-mirror-2026.1.txt" in out


def test_text_omits_the_exit_line_when_the_caller_has_no_code_yet():
    assert "exit " not in render.text(_plan())


def test_shape_pluralises_a_single_entry_correctly():
    """A value too big to inline is described by shape; "1 keys" reads as a bug."""
    big = {f"k{i}": "v" * 20 for i in range(9)}
    out = render.text(
        _plan(
            rows=(Row("k", big, {"only": "v" * 80}, "semantic", (), ""),),
            dropped_keys={"enable_zun": "all/010-2025.2.yml"},
        )
    )
    assert "dict, 9 keys -> dict, 1 key (see report)" in out
    assert "all/010-2025.2.yml: 1 key" in out
    assert "1 keys" not in out


def test_a_back_compat_layer_says_whether_it_is_new_or_already_correct():
    """Counts alone read as "75 keys move now"; most rounds move none of them."""
    out = render.text(
        _plan(
            dropped_keys={"a": "all/010-2024.2.yml", "b": "all/010-2025.2.yml"},
            created_paths=("all/010-2025.2.yml",),
            changed_writes=("all/010-2025.2.yml",),
        )
    )
    assert "all/010-2024.2.yml: 1 key (unchanged)" in out
    assert "all/010-2025.2.yml: 1 key (new file)" in out
    assert "not all of them new" in out


def test_a_header_only_rewrite_says_so_instead_of_just_rewritten():
    """A 010 header carries the pinned commits, so the bytes move every run.

    Reporting that as "rewritten" reads as data loss on a file losing no key.
    """
    out = render.text(
        _plan(
            dropped_keys={"a": "all/010-2024.2.yml"},
            changed_writes=("all/010-2024.2.yml",),
            created_paths=(),
            compat_effects={
                "all/010-2024.2.yml": {"added": [], "removed": [], "updated": []}
            },
        )
    )
    assert "only the pin header is refreshed" in out
    assert "rewritten" not in out


def test_real_key_movement_is_reported_as_counts():
    out = render.text(
        _plan(
            dropped_keys={"a": "all/010-2024.2.yml"},
            changed_writes=("all/010-2024.2.yml",),
            created_paths=(),
            compat_effects={
                "all/010-2024.2.yml": {
                    "added": ["x", "y"],
                    "removed": ["z"],
                    "updated": ["w"],
                }
            },
        )
    )
    assert "(+2 keys, -1 key, 1 value updated)" in out


def test_exit_0_after_an_apply_does_not_claim_there_was_nothing_to_do():
    """Exit 0 covers both "nothing to do" and "applied"; they are not the same."""
    applied = render.text(_plan(), code=0, applied=True)
    idle = render.text(_plan(), code=0, applied=None)
    assert "exit 0: the plan was applied to the worktree" in applied
    assert "already matches" not in applied
    assert "exit 0: nothing to do; the mirror already matches" in idle


def test_commit_summary_records_pins_and_dispositions():
    """The 010 headers carry the pins, but nothing in the tree records which
    disposition a key got -- accepting upstream leaves no trace at all."""
    out = render.commit_summary(
        _plan(),
        SyncOpts(
            target="2026.1",
            accept_upstream=frozenset({"key_a", "key_b"}),
            retain=frozenset({"key_c"}),
        ),
    )
    assert "Generated by sync-mirror for 2026.1." in out
    assert f"  2026.1: {'b' * 40}" in out
    assert f"osism/defaults base: {'d' * 40}" in out
    assert "accepted upstream (2):" in out
    assert "key_a, key_b" in out
    assert "retained, gate verified (1):" in out
    assert "key_c" in out
    # a flag nobody used must not appear as an empty heading
    assert "NOT verified" not in out


def test_commit_summary_wraps_at_72_for_a_commit_body():
    keys = {f"a_rather_long_variable_name_{i:02d}" for i in range(20)}
    out = render.commit_summary(
        _plan(), SyncOpts(target="2026.1", accept_upstream=frozenset(keys))
    )
    assert max(len(line) for line in out.split("\n")) <= 72
    for k in keys:
        assert k in out
