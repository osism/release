from pathlib import Path
import pytest
from osism_drift.config import Allowlist, AllowEntry, Config, Remote, PluginCfg
from osism_drift.drift import kolla_version_chain_inner as plugin

FIXT = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    return Config(
        remote=Remote("https://raw/", "https://api/", "main"),
        base_dirs=(str(FIXT),),
        release_version="latest",
        plugins={"kolla_version_chain_inner": PluginCfg(enabled=True)},
    )


def test_flags_all_inert_keys(cfg):
    # present_a/present_b/kolla_toolbox are in the SBOM; foo/off/inert_x are not.
    drifts = plugin.run(cfg, Allowlist(()))
    assert sorted(d.image for d in drifts) == ["foo", "inert_x", "off"]
    assert all(d.plugin == "kolla_version_chain_inner" for d in drifts)
    assert all(d.allowlisted is False for d in drifts)


def test_enabled_and_buildable_key_is_an_add(cfg):
    # foo: enable_foo truthy AND docker/foo exists -> wire the SBOM key.
    d = [x for x in plugin.run(cfg, Allowlist(())) if x.image == "foo"][0]
    assert "tag-images-with-the-version.py" in d.expected_src
    assert "versions.yml.j2" in d.found_src
    assert "SBOM_IMAGE_TO_VERSION" in d.remediation
    assert "remove" not in d.remediation.lower()


def test_disabled_or_unbuildable_key_is_a_remove(cfg):
    # off: buildable (docker/off) but enable_off is "no" -> dead line.
    # inert_x: neither enabled nor buildable -> dead line.
    by = {x.image: x for x in plugin.run(cfg, Allowlist(()))}
    for key in ("off", "inert_x"):
        d = by[key]
        assert "remove" in d.remediation.lower()
        assert "versions.yml.j2" in d.found_src  # the template line to delete
        assert "tag-images-with-the-version.py" not in d.expected_src


def test_add_and_remove_render_as_separate_blocks(cfg):
    # The two buckets carry distinct (expected_src, found_src), so the grouped
    # report renders one add block and one remove block, never one merged hint.
    drifts = plugin.run(cfg, Allowlist(()))
    add = [d for d in drifts if d.image == "foo"][0]
    rm = [d for d in drifts if d.image == "off"][0]
    assert (add.expected_src, add.found_src) != (rm.expected_src, rm.found_src)


def test_hyphen_underscore_is_not_drift(cfg):
    # kolla_toolbox (template) vs kolla-toolbox (SBOM) — normalised match.
    drifts = plugin.run(cfg, Allowlist(()))
    assert all(d.image != "kolla_toolbox" for d in drifts)


def test_allowlist_marks_allowlisted(cfg):
    al = Allowlist(
        (
            AllowEntry(
                plugin="kolla_version_chain_inner",
                image="inert_x",
                reason="tracked in issue X",
            ),
        )
    )
    by = {d.image: d for d in plugin.run(cfg, al)}
    assert by["inert_x"].allowlisted is True
    assert by["inert_x"].reason == "tracked in issue X"
