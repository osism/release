import types

from osism_drift import report
from osism_drift.model import DriftEntry


def _plugin(name, summary="{n} things missing:", remediation="do X."):
    return types.SimpleNamespace(NAME=name, SUMMARY=summary, REMEDIATION=remediation)


def _entry(
    plugin, image, expected_src="E", found_src="F", allowlisted=False, alias=None
):
    return DriftEntry(
        plugin=plugin,
        image=image,
        alias=alias if alias is not None else image,
        expected="exp",
        found="found",
        expected_src=expected_src,
        found_src=found_src,
        allowlisted=allowlisted,
    )


def test_single_plugin_groups_sorts_and_renders():
    p = _plugin("plug_a")
    lines = report.format_text(
        [_entry("plug_a", "zebra"), _entry("plug_a", "alpha")], [p]
    )
    text = "\n".join(lines)
    assert report.HEADER in lines
    assert "plug_a — 2 things missing:" in text
    assert "alpha, zebra" in text  # sorted, comma-joined
    assert "  Fix: do X." in text
    assert "  Refs: E" in text
    assert "        F" in text


def test_orders_blocks_by_plugins_list():
    pa = _plugin("a_plug", "{n} a:")
    pb = _plugin("b_plug", "{n} b:")
    lines = report.format_text([_entry("a_plug", "x"), _entry("b_plug", "y")], [pb, pa])
    text = "\n".join(lines)
    assert text.index("b_plug — 1 b:") < text.index("a_plug — 1 a:")


def test_build_style_splits_one_block_per_ref_pair():
    p = _plugin("build", "{n} svc:")
    drifts = [
        _entry(
            "build",
            "aodh",
            expected_src="openstack/kolla docker/ @ ref-2024.1",
            found_src="osism/release latest/openstack-2024.1.yml",
        ),
        _entry(
            "build",
            "barbican",
            expected_src="openstack/kolla docker/ @ ref-2025.1",
            found_src="osism/release latest/openstack-2025.1.yml",
        ),
    ]
    text = "\n".join(report.format_text(drifts, [p]))
    assert text.count("build — 1 svc:") == 2  # two separate blocks
    assert text.index("2024.1") < text.index("2025.1")  # sorted by src pair


def test_allowlisted_excluded_from_blocks_and_count():
    p = _plugin("plug")
    drifts = [_entry("plug", "shown"), _entry("plug", "hidden", allowlisted=True)]
    text = "\n".join(report.format_text(drifts, [p]))
    assert "shown" in text
    assert "hidden" not in text
    assert "plug — 1 things missing:" in text  # count excludes allowlisted


def test_empty_returns_empty_list():
    p = _plugin("plug")
    assert report.format_text([], [p]) == []
    assert report.format_text([_entry("plug", "x", allowlisted=True)], [p]) == []


def test_entry_summary_remediation_override_plugin_default():
    # A plugin emitting two flavours of finding can override the per-plugin
    # SUMMARY/REMEDIATION on the entry; the report uses the override when set
    # and falls back to the plugin default when it is None.
    p = _plugin("plug", summary="{n} default:", remediation="default fix.")
    add = DriftEntry(
        plugin="plug",
        image="valkey",
        alias="valkey",
        expected="e",
        found="f",
        expected_src="SBOM",
        found_src="TMPL",
        summary="{n} to wire:",
        remediation="add the key.",
    )
    rm = DriftEntry(
        plugin="plug",
        image="zun",
        alias="zun",
        expected="e",
        found="f",
        expected_src="NOIMG",
        found_src="TMPL",
        summary="{n} dead:",
        remediation="remove the line.",
    )
    fallback = _entry("plug", "alpha", expected_src="OTHER", found_src="TMPL")
    text = "\n".join(report.format_text([add, rm, fallback], [p]))
    assert "plug — 1 to wire:" in text
    assert "Fix: add the key." in text
    assert "plug — 1 dead:" in text
    assert "Fix: remove the line." in text
    assert "plug — 1 default:" in text  # fallback entry uses plugin default
    assert "Fix: default fix." in text


def test_lists_image_not_alias():
    p = _plugin("plug")
    text = "\n".join(
        report.format_text([_entry("plug", "real_key", alias="some_alias")], [p])
    )
    assert "real_key" in text
    assert "some_alias" not in text
