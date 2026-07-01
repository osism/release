"""Unit tests for the synthetic demo-report builder."""

from osism_drift import demo
from osism_drift.drift import IMAGE_PLUGINS, KOLLA_PLUGINS


def test_three_entries_per_plugin():
    drifts = demo.build_demo_drifts(IMAGE_PLUGINS)
    for p in IMAGE_PLUGINS:
        mine = [d for d in drifts if d.plugin == p.NAME]
        assert len(mine) == 3, p.NAME


def test_entries_carry_live_plugin_text():
    """Demo pulls SUMMARY/REMEDIATION live so wording changes are reflected."""
    for p in KOLLA_PLUGINS:
        mine = [d for d in demo.build_demo_drifts([p])]
        assert mine, p.NAME
        for d in mine:
            assert d.summary == p.SUMMARY, p.NAME
            assert d.remediation == p.REMEDIATION, p.NAME


def test_entries_are_actionable():
    for d in demo.build_demo_drifts(IMAGE_PLUGINS):
        assert d.allowlisted is False


def test_show_values_plugin_covers_both_render_branches():
    """A SHOW_VALUES plugin gets both an empty-expected and a pinned entry."""
    show_values = [p for p in IMAGE_PLUGINS if getattr(p, "SHOW_VALUES", False)]
    assert show_values, "expected at least one SHOW_VALUES image plugin"
    for p in show_values:
        expecteds = {d.expected for d in demo.build_demo_drifts([p])}
        assert "" in expecteds, p.NAME
        assert any(e for e in expecteds), p.NAME


def test_paths_reflect_plugin_input_files():
    for p in IMAGE_PLUGINS:
        first_repo = p.INPUT_FILES[0][0]
        last_repo = p.INPUT_FILES[-1][0]
        for d in demo.build_demo_drifts([p]):
            assert d.expected_src.startswith(first_repo + "/"), p.NAME
            assert d.found_src.startswith(last_repo + "/"), p.NAME
