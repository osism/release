import pytest
import responses
import yaml
from osism_drift.config import (
    Config,
    Remote,
    PluginCfg,
    SourceCfg,
    Allowlist,
    AllowEntry,
)
from osism_drift.drift import kolla_mirror_verbatim as plugin

API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"


def _mirror(mapping):
    # Build fixture YAML with safe_dump, NOT an f-string: f"{k}: {v}" would coerce
    # (no -> False, yes -> True) and mangle Jinja values, breaking the parsed-value
    # equality the plugin relies on. safe_dump round-trips to the intended value.
    return yaml.safe_dump(mapping)


def _write_defaults(tmp_path, files, versions="", overlays=None):
    dall = tmp_path / "defaults" / "all"
    dall.mkdir(parents=True)
    for name, text in files.items():
        (dall / name).write_text(text)
    # osism_supply_excluding_mirror reads versions.yml.j2 UNCONDITIONALLY (via
    # source.read, not read_optional), so it must always exist — even empty — or
    # every test fails with SourceError before classification.
    vt = tmp_path / "container-image-kolla-ansible" / "files" / "src" / "templates"
    vt.mkdir(parents=True)
    (vt / "versions.yml.j2").write_text(versions)
    # ...and the per-release overlays/<release>/kolla-ansible.yml (optional here;
    # the overlays tree read is missing_ok).
    for release, text in (overlays or {}).items():
        od = tmp_path / "container-image-kolla-ansible" / "overlays" / release
        od.mkdir(parents=True)
        (od / "kolla-ansible.yml").write_text(text)


def _cfg(tmp_path, releases=("A", "B")):
    # sorted(("A","B"))[-1] == "B" -> newest; "A" is the older release.
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        remote_fallback=True,  # kolla-ansible is not local here -> remote (mocked)
        release_version="latest",
        plugins={"kolla_mirror_verbatim": PluginCfg(enabled=True)},
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="stable/2025.2")},
        releases=releases,
    )


def _mock_release(ref, mapping):
    # release_to_ref probes stable/<r> (200), then reads the monolithic all.yml.
    body = _mirror(mapping).encode()  # safe_dump, not f-string (see _mirror)
    responses.add(
        responses.GET, f"{API}/openstack/kolla-ansible/commits/{ref}", status=200
    )
    responses.add(
        responses.GET,
        f"{RAW}/openstack/kolla-ansible/{ref}/ansible/group_vars/all.yml",
        body=body,
        status=200,
    )


@responses.activate
def test_clean_mirror_no_drift(tmp_path):
    _write_defaults(
        tmp_path, {"001-kolla-defaults.yml": _mirror({"foo": 1, "bar": "two"})}
    )
    _mock_release("stable/A", {"foo": 1, "bar": "two"})
    _mock_release("stable/B", {"foo": 1, "bar": "two"})
    assert plugin.run(_cfg(tmp_path), Allowlist(())) == []


@responses.activate
def test_value_diff_flagged(tmp_path):  # Shape A
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": _mirror({"k": 1})})
    _mock_release("stable/A", {"k": 1})
    _mock_release("stable/B", {"k": 2})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["k"]
    assert "restore" in drifts[0].remediation and "099" in drifts[0].remediation


@responses.activate
def test_missing_from_mirror_flagged(tmp_path):  # Shape C
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": _mirror({"have": 1})})
    _mock_release("stable/A", {"have": 1})
    _mock_release("stable/B", {"have": 1, "want": 9})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["want"]
    assert drifts[0].found == "(absent)"
    assert "mirror" in drifts[0].remediation


@responses.activate
def test_dropped_key_routed_to_010(tmp_path):  # Shape B-dropped
    # 001 has venus_x; older release A still defines it; newest B dropped it. D8
    # routes it to all/010-A.yml (A = newest release still carrying the key), NOT 099.
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": _mirror({"venus_x": 1})})
    _mock_release("stable/A", {"venus_x": 1})
    _mock_release("stable/B", {})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["venus_x"]
    assert "all/010-A.yml" in drifts[0].remediation


@responses.activate
def test_dup_key_delete(tmp_path):  # Shape B-dup
    # 001 has dup; a second (non-001) defaults file also supplies it; newest lacks it.
    _write_defaults(
        tmp_path,
        {
            "001-kolla-defaults.yml": _mirror({"dup": 1}),
            "099-x.yml": _mirror({"dup": 1}),
        },
    )
    _mock_release("stable/A", {})
    _mock_release("stable/B", {})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["dup"]
    assert "delete" in drifts[0].remediation


@responses.activate
def test_invented_key(tmp_path):  # Shape B-invented
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": _mirror({"osism_special": 1})})
    _mock_release("stable/A", {})
    _mock_release("stable/B", {})
    drifts = plugin.run(_cfg(tmp_path), Allowlist(()))
    assert [d.image for d in drifts] == ["osism_special"]
    assert "invented" in drifts[0].summary.lower()
    assert "099-*" in drifts[0].remediation


@responses.activate
def test_typo_mirrored_not_flagged(tmp_path):
    # The upstream typo is in 001 AND upstream-newest with the same value -> matches.
    _write_defaults(
        tmp_path,
        {"001-kolla-defaults.yml": _mirror({"eutron_external_interface": "eth1"})},
    )
    _mock_release("stable/A", {"eutron_external_interface": "eth1"})
    _mock_release("stable/B", {"eutron_external_interface": "eth1"})
    assert plugin.run(_cfg(tmp_path), Allowlist(())) == []


@responses.activate
def test_newest_selected_by_sort(tmp_path):
    # config.releases is intentionally OUT OF ORDER. newest must be chosen by an
    # explicit sort (-> 2025.2), not by [-1] of caller order (-> 2024.1). 001
    # matches 2025.2 but differs from 2024.1, so if newest were wrongly 2024.1 the
    # value diff would flag Shape A; asserting no drift proves 2025.2 is the target.
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": _mirror({"k": 1})})
    _mock_release("stable/2025.2", {"k": 1})  # true mirror target -> matches 001
    _mock_release("stable/2024.1", {"k": 999})  # older; read by dropped_key_release_map
    cfg = _cfg(tmp_path, releases=("2025.2", "2024.1"))
    assert plugin.run(cfg, Allowlist(())) == []  # matches 2025.2 -> no drift


@responses.activate
def test_dropped_key_classified_with_out_of_order_releases(tmp_path):
    # Guards dropped_key_release_map's sort: with an out-of-order config.releases, a
    # bare [:-1] would slice off the wrong release and misclassify this B-dropped key
    # as B-invented. venus_x is in the OLDER release (2024.1) but not newest (2025.2),
    # so it must stay B-dropped and route to 010-2024.1.yml, not B-invented.
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": _mirror({"venus_x": 1})})
    _mock_release("stable/2025.2", {})  # newest dropped it
    _mock_release("stable/2024.1", {"venus_x": 1})  # older still defines it
    cfg = _cfg(tmp_path, releases=("2025.2", "2024.1"))
    drifts = plugin.run(cfg, Allowlist(()))
    assert [d.image for d in drifts] == ["venus_x"]
    assert "all/010-2024.1.yml" in drifts[0].remediation


@responses.activate
def test_allowlist_marks_entry(tmp_path):
    _write_defaults(tmp_path, {"001-kolla-defaults.yml": _mirror({"venus_x": 1})})
    _mock_release("stable/A", {"venus_x": 1})
    _mock_release("stable/B", {})
    al = Allowlist(
        (AllowEntry(plugin="kolla_mirror_verbatim", image="venus_x", reason="test"),)
    )
    entry = [d for d in plugin.run(_cfg(tmp_path), al) if d.image == "venus_x"][0]
    assert entry.allowlisted is True


def test_empty_release_range_raises(tmp_path):
    from osism_drift.source import SourceError

    _write_defaults(tmp_path, {"001-kolla-defaults.yml": _mirror({"foo": 1})})
    (tmp_path / "release" / "latest").mkdir(parents=True)  # empty -> empty range
    with pytest.raises(SourceError, match="empty supported release range"):
        plugin.run(_cfg(tmp_path, releases=()), Allowlist(()))
