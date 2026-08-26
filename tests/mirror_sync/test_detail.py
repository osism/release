"""The detail report: the values the terminal deliberately does not print."""

from osism_drift.mirror_sync import detail
from osism_drift.mirror_sync.model import Plan, Row, SyncOpts


def _plan(**kw):
    base = dict(
        target_release="2026.1",
        release_shas={"2025.2": "a" * 40, "2026.1": "b" * 40},
        defaults_base_sha="d" * 40,
        prev_release="2025.2",
        range_base_sha="c" * 40,
        mirror_writes={},
        mirror_deletes=(),
        compat_writes={},
        compat_deletes=(),
        unowned_compat=(),
        changed_writes=(),
        created_paths=(),
        rows=(
            Row("k_sem", "{{ a }}", "{{\n  a\n}}", "semantic", ("abc123 Re-wrap",), ""),
            Row("k_rep", "no", False, "representation", (), "not proven inert"),
        ),
        added_keys=("_ssl_modern_options",),
        dropped_keys={"enable_zun": "all/010-2025.2.yml"},
        added_files=("all/001-valkey.yml",),
        deleted_files=("all/001-zun.yml",),
    )
    base.update(kw)
    return Plan(**base)


def test_multiline_value_is_written_with_real_newlines():
    """The whole point: repr() turns a re-wrap into one line of \\n escapes."""
    out = detail.text(_plan())
    assert "\\n" not in out
    assert "     {{\n       a\n     }}" in out


def test_quoting_is_preserved_for_short_values():
    """'no' -> False is the representation class; str() would hide the difference."""
    out = detail.text(_plan())
    assert "     'no'" in out
    assert "     False" in out


def test_each_class_carries_its_meaning():
    out = detail.text(_plan())
    assert "semantic (1)" in out
    assert "each key needs a disposition" in out
    assert "representation (1)" in out
    assert "byte-for-byte mirror" in out


def test_attribution_and_note_travel_with_the_row():
    out = detail.text(_plan())
    assert "upstream: abc123 Re-wrap" in out
    assert "not proven inert" in out


def test_header_names_the_base_ref_and_both_shas():
    out = detail.text(_plan(), SyncOpts(target="2026.1", base_ref="my-branch"))
    assert f"target        2026.1 @ {'b' * 40}" in out
    assert f"defaults base my-branch @ {'d' * 40}" in out


def test_files_and_keys_the_terminal_truncates_are_all_here():
    out = detail.text(_plan())
    assert "+ all/001-valkey.yml" in out
    assert "- all/001-zun.yml" in out
    assert "_ssl_modern_options" in out
    assert "enable_zun -> all/010-2025.2.yml" in out


def test_a_class_with_no_rows_gets_no_section():
    out = detail.text(_plan(rows=()))
    assert "semantic" not in out
    assert "notation" not in out
