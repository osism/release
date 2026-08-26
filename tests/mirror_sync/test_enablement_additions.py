from osism_drift import enablement


def test_mirror_prefix_is_public_and_matches_the_private_constant():
    # sync-mirror builds filenames from this; a sibling module must not have to
    # reach for a private name across a boundary.
    assert enablement.MIRROR_PREFIX == "001-"
    assert enablement.MIRROR_PREFIX == enablement._MIRROR_PREFIX


def test_upstream_groupvars_files_is_public(monkeypatch):
    monkeypatch.setattr(
        enablement,
        "_upstream_groupvars_named_bodies",
        lambda rel, cfg: [("nova.yml", b"a: 1\n")],
    )
    assert enablement.upstream_groupvars_files("2025.2", None) == [
        ("nova.yml", b"a: 1\n")
    ]
