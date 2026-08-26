from osism_drift import enablement
from osism_drift.config import Config, Remote

API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"


def _write_defaults(tmp_path, files):
    dall = tmp_path / "defaults" / "all"
    dall.mkdir(parents=True)
    for name, text in files.items():
        (dall / name).write_text(text)
    vt = tmp_path / "container-image-kolla-ansible" / "files" / "src" / "templates"
    vt.mkdir(parents=True)
    (vt / "versions.yml.j2").write_text("")


def _cfg(tmp_path):
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        remote_fallback=False,
        release_version="latest",
        plugins={},
        sources={},
        releases=("A",),
    )


def test_groupvars_values_merges_and_parses():
    # Two bodies; the later one wins for the overlapping key, values are parsed
    # (int stays int, string stays string) — a strict mirror needs real types.
    bodies = [b"a: 1\nb: two\n", b"b: three\nc: 3\n"]
    assert enablement.groupvars_values(bodies) == {"a": 1, "b": "three", "c": 3}


def test_groupvars_values_skips_non_mapping_and_empty():
    assert enablement.groupvars_values([b"- 1\n- 2\n", b""]) == {}


def test_osism_mirror_values_reads_only_001(tmp_path):
    dall = tmp_path / "defaults" / "all"
    dall.mkdir(parents=True)
    (dall / "001-kolla-defaults.yml").write_text("k: 5000\nname: v\n")
    (dall / "099-kolla.yml").write_text("other: 1\n")  # must NOT be read
    cfg = Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(tmp_path),),
        remote_fallback=False,
        release_version="latest",
        plugins={},
        sources={},
        releases=("A",),
    )
    # Only 001 is read, values parsed; the 099 key is absent.
    assert enablement.osism_mirror_values(cfg) == {"k": 5000, "name": "v"}


def test_mirror_values_merges_the_whole_001_layer(tmp_path):
    # Two mirror files: the later one wins, matching Ansible's lexical merge.
    _write_defaults(
        tmp_path,
        {
            "001-common.yml": "shared: from_common\nonly_common: 1\n",
            "001-database.yml": "shared: from_database\n",
            "099-kolla.yml": "osism_opinion: 1\n",
        },
    )
    values = enablement.osism_mirror_values(_cfg(tmp_path))
    assert values["shared"] == "from_database"  # 001-database sorts last
    assert values["only_common"] == 1
    assert "osism_opinion" not in values  # 099 is not in the layer


def test_supply_excluding_mirror_excludes_every_layer_file(tmp_path):
    _write_defaults(
        tmp_path,
        {
            "001-common.yml": "in_layer: 1\n",
            "001-database.yml": "also_in_layer: 1\n",
            "099-kolla.yml": "outside_layer: 1\n",
        },
    )
    keys = enablement.osism_supply_excluding_mirror(_cfg(tmp_path))
    assert "outside_layer" in keys
    assert "in_layer" not in keys
    assert "also_in_layer" not in keys
