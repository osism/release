from pathlib import Path

import pytest
import responses

from osism_drift import catalog
from osism_drift.config import Config, Remote, PluginCfg, SourceCfg
from osism_drift.source import SourceError

FIXT = Path(__file__).parent / "fixtures"
API = "https://api.github.com/repos"
RAW = "https://raw.githubusercontent.com"

ENUMS = b"""
class Role:
    def __init__(self, name, dependencies=None):
        pass

VALIDATE_PLAYBOOKS = {
    "barbican-config": {"runtime": "kolla-ansible", "playbook": "barbican"},
    "ntp": {"runtime": "osism-ansible", "environment": "generic"},
}

MAP_ROLE2ROLE = {
    "nutshell": [
        Role("a", dependencies=[Role("b", dependencies=[Role("c")])]),
        Role("d"),
    ],
}
"""


@pytest.fixture
def cfg():
    # No local python-osism fixture tree exists (unlike kolla-ansible/
    # ceph-ansible/osism-ansible, nothing here mirrors a real build script),
    # so every read falls through remote_fallback to the mocked raw URL --
    # exercising the real source.read() remote path rather than a stub.
    return Config(
        remote=Remote(f"{RAW}/", f"{API}/", "main", "osism"),
        base_dirs=(str(FIXT),),
        remote_fallback=True,
        release_version="latest",
        plugins={"catalog_role_missing": PluginCfg(enabled=True)},
        sources={"python_osism": SourceCfg(owner="osism", branch="main")},
        releases=("A", "B"),
    )


def _serve_enums(body: bytes, ref: str = "main") -> None:
    """Mock the raw-GitHub GET for python-osism's enums.py at `ref`.

    Routes the body through the real source.read()/responses machinery
    (rather than monkeypatching source.read itself) so the test also
    exercises repo-name hyphenation and URL construction, not just the AST
    extraction.
    """
    responses.add(
        responses.GET,
        f"{RAW}/osism/python-osism/{ref}/osism/data/enums.py",
        body=body,
        status=200,
    )


@responses.activate
def test_collections_collect_nested_roles(cfg):
    _serve_enums(ENUMS)
    assert catalog.collections(cfg) == {"nutshell": ["a", "b", "c", "d"]}


@responses.activate
def test_validators_default_the_playbook_name(cfg):
    _serve_enums(ENUMS)
    v = catalog.validators(cfg)
    assert v["barbican-config"]["playbook"] == "barbican"
    assert v["ntp"]["playbook"] == "validate-ntp"  # derived, validate.py:89-92
    assert v["ntp"]["environment"] == "generic"
    assert v["barbican-config"]["environment"] is None


@responses.activate
def test_non_literal_map_role2role_raises(cfg):
    _serve_enums(b"MAP_ROLE2ROLE = build_it()\nVALIDATE_PLAYBOOKS = {}\n")
    with pytest.raises(SourceError, match="MAP_ROLE2ROLE"):
        catalog.collections(cfg)


@responses.activate
def test_missing_assignment_raises(cfg):
    _serve_enums(b"VALIDATE_PLAYBOOKS = {}\n")
    with pytest.raises(SourceError, match="MAP_ROLE2ROLE"):
        catalog.collections(cfg)


@responses.activate
def test_non_literal_validate_playbooks_raises(cfg):
    _serve_enums(b"MAP_ROLE2ROLE = {}\nVALIDATE_PLAYBOOKS = build_it()\n")
    with pytest.raises(SourceError, match="VALIDATE_PLAYBOOKS"):
        catalog.validators(cfg)


@responses.activate
def test_role_with_non_literal_name_raises(cfg):
    # A Role(...) whose name is not a literal (here, a bare Name node) must
    # not be silently dropped from its collection -- that would make the
    # role vanish and the plugin report no drift for it at all.
    _serve_enums(b"""
class Role:
    def __init__(self, name, dependencies=None):
        pass

VALIDATE_PLAYBOOKS = {}

MAP_ROLE2ROLE = {
    "broken": [
        Role(SOME_NAME),
    ],
}
""")
    with pytest.raises(SourceError, match="broken"):
        catalog.collections(cfg)


@responses.activate
def test_validator_entry_without_runtime_raises(cfg):
    _serve_enums(b"""
class Role:
    def __init__(self, name, dependencies=None):
        pass

VALIDATE_PLAYBOOKS = {
    "orphan": {"playbook": "orphan"},
}

MAP_ROLE2ROLE = {}
""")
    with pytest.raises(SourceError, match="orphan"):
        catalog.validators(cfg)


@responses.activate
def test_non_literal_collection_key_raises(cfg):
    _serve_enums(b"""
class Role:
    def __init__(self, name, dependencies=None):
        pass

VALIDATE_PLAYBOOKS = {}

MAP_ROLE2ROLE = {
    SOME_NAME: [Role("a")],
}
""")
    with pytest.raises(SourceError, match="non-literal collection name"):
        catalog.collections(cfg)
