from osism_drift import enablement
from osism_drift.config import Config, Remote

API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"


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
