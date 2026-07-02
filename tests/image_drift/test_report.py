"""Tests for report.format_text — SHOW_VALUES mode and kolla regression."""

import types

from osism_drift import report
from osism_drift.drift import kolla_version_chain_inner as kvc
from osism_drift.model import DriftEntry


def _sv_plugin(name="role_shadows"):
    return types.SimpleNamespace(
        NAME=name,
        SHOW_VALUES=True,
        SUMMARY="{n} stale pins:",
        REMEDIATION="fix it.",
    )


def _entry(
    plugin,
    alias,
    found,
    expected,
    found_src,
    expected_src="release/latest/base.yml",
    summary=None,
    remediation=None,
    allowlisted=False,
):
    return DriftEntry(
        plugin=plugin,
        image=alias,
        alias=alias,
        expected=expected,
        found=found,
        expected_src=expected_src,
        found_src=found_src,
        allowlisted=allowlisted,
        summary=summary,
        remediation=remediation,
    )


_FS_ADMINER = "ansible-collection-services/roles/adminer/defaults/main.yml"
_FS_NETBOX = "ansible-collection-services/roles/netbox/defaults/main.yml"
_FS_MANAGER = "ansible-collection-services/roles/manager/defaults/main.yml"

_LIVE_SUM = "{n} LIVE — no images.yml override"
_LIVE_REM = "bump the tag."
_DORMANT_SUM = "{n} DORMANT — overridden by images.yml"
_DORMANT_REM = "sync when convenient."


def _sv_entries():
    """Two advice classes across three role files."""
    return [
        # live class — netbox (one file)
        _entry(
            "role_shadows",
            "netbox_redis",
            "7.0.0",
            "7.5.0",
            _FS_NETBOX,
            summary=_LIVE_SUM,
            remediation=_LIVE_REM,
        ),
        # dormant class — adminer + manager (two different files)
        _entry(
            "role_shadows",
            "adminer",
            "4.7",
            "5.4.2",
            _FS_ADMINER,
            summary=_DORMANT_SUM,
            remediation=_DORMANT_REM,
        ),
        _entry(
            "role_shadows",
            "ara_server_mariadb",
            "11.8.3",
            "11.8.4",
            _FS_MANAGER,
            summary=_DORMANT_SUM,
            remediation=_DORMANT_REM,
        ),
        _entry(
            "role_shadows",
            "manager_redis",
            "7.4.6-alpine",
            "7.5.0",
            _FS_MANAGER,
            summary=_DORMANT_SUM,
            remediation=_DORMANT_REM,
        ),
    ]


def test_show_values_produces_two_blocks():
    p = _sv_plugin()
    lines = report.format_text(_sv_entries(), [p])
    text = "\n".join(lines)
    # Exactly two lead lines — one per advice class.
    assert text.count("role_shadows —") == 2


def test_show_values_live_block_collapses_file():
    p = _sv_plugin()
    lines = report.format_text(_sv_entries(), [p])
    text = "\n".join(lines)
    assert "netbox_redis_tag (7.0.0 → 7.5.0)   roles/netbox/defaults/main.yml" in text


def test_show_values_dormant_block_collapses_two_files():
    p = _sv_plugin()
    lines = report.format_text(_sv_entries(), [p])
    text = "\n".join(lines)
    assert "adminer_tag (4.7 → 5.4.2)   roles/adminer/defaults/main.yml" in text
    assert (
        "ara_server_mariadb_tag (11.8.3 → 11.8.4)   roles/manager/defaults/main.yml"
        in text
    )
    assert (
        "manager_redis_tag (7.4.6-alpine → 7.5.0)   roles/manager/defaults/main.yml"
        in text
    )


def test_show_values_refs_single_line_only():
    p = _sv_plugin()
    lines = report.format_text(_sv_entries(), [p])
    for i, line in enumerate(lines):
        if line.startswith("  Refs:"):
            # The next non-blank line (if any) must not be an indented path continuation.
            following = [ln for ln in lines[i + 1 :] if ln.strip()]
            if following:
                assert not following[0].startswith(
                    "        "
                ), f"Expected single-line Refs but found continuation: {following[0]!r}"


def test_show_values_item_order_by_path_then_alias():
    p = _sv_plugin()
    lines = report.format_text(_sv_entries(), [p])
    # Within the dormant block: adminer (adminer/) sorts before
    # ara_server_mariadb and manager_redis (both manager/).
    text = "\n".join(lines)
    idx_adminer = text.index("adminer_tag")
    idx_ara = text.index("ara_server_mariadb_tag")
    idx_mgr_redis = text.index("manager_redis_tag")
    # adminer/ < manager/ alphabetically
    assert idx_adminer < idx_ara
    # within manager/ file, ara_server_mariadb < manager_redis alphabetically
    assert idx_ara < idx_mgr_redis


def test_show_values_fix_and_refs_present():
    p = _sv_plugin()
    lines = report.format_text(_sv_entries(), [p])
    text = "\n".join(lines)
    assert "Fix: bump the tag." in text
    assert "Fix: sync when convenient." in text
    assert "  Refs: release/latest/base.yml" in text


# ---------------------------------------------------------------------------
# SHOW_VALUES with empty expected (role_unpinned — "no release pin" form)
# ---------------------------------------------------------------------------


def _unpinned_plugin():
    return types.SimpleNamespace(
        NAME="role_unpinned",
        SHOW_VALUES=True,
        SUMMARY="{n} role-default pins with no release base.yml pin:",
        REMEDIATION="add a pin to release base.yml or allowlist it.",
    )


_FS_CIINTERNAL = "ansible-collection-services/roles/ciinternal/defaults/main.yml"
_FS_WIDGET = "ansible-collection-services/roles/widget/defaults/main.yml"


def _unpinned_entries():
    return [
        _entry(
            "role_unpinned",
            "ciinternal",
            "1.0",
            "",
            _FS_CIINTERNAL,
        ),
        _entry(
            "role_unpinned",
            "widget",
            "2.0",
            "",
            _FS_WIDGET,
        ),
    ]


def test_show_values_empty_expected_renders_no_release_pin():
    p = _unpinned_plugin()
    lines = report.format_text(_unpinned_entries(), [p])
    text = "\n".join(lines)
    assert (
        "ciinternal_tag (1.0, no release pin)   roles/ciinternal/defaults/main.yml"
        in text
    )
    assert "widget_tag (2.0, no release pin)   roles/widget/defaults/main.yml" in text
    # Item lines must not contain an arrow (only the orientation header may have one)
    item_lines = [ln for ln in lines if ln.startswith("    ") and "_tag" in ln]
    assert all("→" not in ln for ln in item_lines)


def test_show_values_empty_expected_refs_single_line():
    p = _unpinned_plugin()
    lines = report.format_text(_unpinned_entries(), [p])
    for i, line in enumerate(lines):
        if line.startswith("  Refs:"):
            following = [ln for ln in lines[i + 1 :] if ln.strip()]
            if following:
                assert not following[0].startswith(
                    "        "
                ), f"Expected single-line Refs but found continuation: {following[0]!r}"


def test_show_values_empty_expected_sorted_by_path_then_alias():
    p = _unpinned_plugin()
    lines = report.format_text(_unpinned_entries(), [p])
    text = "\n".join(lines)
    idx_ci = text.index("ciinternal_tag")
    idx_wid = text.index("widget_tag")
    # ciinternal/ < widget/ alphabetically
    assert idx_ci < idx_wid


def test_show_values_nonempty_expected_still_uses_arrow():
    """Confirm existing arrow-form is unaffected when expected is non-empty."""
    p = _sv_plugin()
    lines = report.format_text(_sv_entries(), [p])
    text = "\n".join(lines)
    assert "netbox_redis_tag (7.0.0 → 7.5.0)" in text
    assert "no release pin" not in text


# ---------------------------------------------------------------------------
# Regression: kolla_version_chain_inner rendering is unchanged by the new key
# ---------------------------------------------------------------------------


def _kvc_add(image):
    return DriftEntry(
        plugin=kvc.NAME,
        image=image,
        alias=image,
        expected=kvc._ADD_EXPECTED_SRC,
        found="absent",
        expected_src=kvc._ADD_EXPECTED_SRC,
        found_src=kvc._ADD_FOUND_SRC,
        summary=kvc._ADD_SUMMARY,
        remediation=kvc._ADD_REMEDIATION,
    )


_DEAD_EXPECTED_SRC = "openstack/kolla docker/ @ stable/2025.2 (no OSISM-built image)"


def _kvc_dead(image):
    return DriftEntry(
        plugin=kvc.NAME,
        image=image,
        alias=image,
        expected=_DEAD_EXPECTED_SRC,
        found="dead",
        expected_src=_DEAD_EXPECTED_SRC,
        found_src=kvc._DEAD_FOUND_SRC,
        summary=kvc._DEAD_SUMMARY,
        remediation=kvc._DEAD_REMEDIATION,
    )


def test_kolla_chain_inner_regression_two_blocks():
    """Adding summary/remediation to the key must not merge ADD and DEAD blocks."""
    p = types.SimpleNamespace(
        NAME=kvc.NAME,
        SUMMARY=kvc.SUMMARY,
        REMEDIATION=kvc.REMEDIATION,
    )
    drifts = [_kvc_add("foo"), _kvc_dead("off"), _kvc_dead("inert_x")]
    lines = report.format_text(drifts, [p])
    text = "\n".join(lines)
    assert text.count(f"{kvc.NAME} —") == 2
    assert kvc._ADD_FOUND_SRC in text
    assert kvc._DEAD_FOUND_SRC in text
    assert "        " in text  # two-line Refs block present (non-SHOW_VALUES)
