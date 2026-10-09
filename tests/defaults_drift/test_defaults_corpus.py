"""defaults_corpus: which sources are searched, and how they are scanned."""

import pytest

from osism_drift import defaults_corpus as dc
from osism_drift.source import SourceError

_BASE_YML = b"""---
generics_version: 'v0.20261007.0'
manager_playbooks_version: 'v0.20260721.0'
playbooks_version: 'v0.20261005.0'
ansible_collections:
  community.general: '13.4.0'
  osism.commons: '0.20261005.0'
  osism.services: '0.20261007.0'
ansible_roles:
  geerlingguy.dotfiles: master
  hardening: e77c311442cb1d1ef8caa7df9d9c00471afa75e7
  pdns_recursor: 'v1.8.1'
"""

_ROLES_YML = b"""---
geerlingguy.certbot: geerlingguy/ansible-role-certbot
geerlingguy.dotfiles: geerlingguy/ansible-role-dotfiles
hardening: openstack/ansible-hardening
pdns_recursor: PowerDNS/pdns_recursor-ansible
"""


def _stub_release(
    monkeypatch,
    base=_BASE_YML,
    roles=_ROLES_YML,
    releases=("2025.2", "2026.1"),
    flavours=("quincy", "reef"),
):
    def read(repo, path, config):
        assert repo == "release"
        return {"latest/base.yml": base, "etc/roles.yml": roles}[path]

    monkeypatch.setattr(dc.source, "read", read)
    monkeypatch.setattr(dc.enablement, "release_range", lambda config: list(releases))
    monkeypatch.setattr(
        dc.source, "release_to_ref", lambda repo, rel, config: f"stable/{rel}"
    )
    monkeypatch.setattr(
        dc.playbooks, "ceph_ansible_flavours", lambda config: list(flavours)
    )
    pins = {"quincy": "stable-7.0", "reef": "stable-8.0"}
    monkeypatch.setattr(
        dc.playbooks, "pin", lambda f, key, config: pins[f[len("ceph-") : -len(".yml")]]
    )


def test_pin_to_ref():
    assert dc.pin_to_ref("0.20261005.0") == "v0.20261005.0"
    assert dc.pin_to_ref("v0.20261005.0") == "v0.20261005.0"
    assert dc.pin_to_ref("main") == "main"
    assert dc.pin_to_ref("latest") == "latest"


def test_sources_cover_osism_repos_pins_and_the_upstream_range(monkeypatch):
    _stub_release(monkeypatch)
    got = dc.sources(None)
    assert got[0] == dc.Source("defaults", None)
    main_repos = [s.repo for s in got if s.ref is None]
    assert "ansible_collection_commons" in main_repos
    assert "ansible_collection_services" in main_repos
    assert not any("community" in r for r in main_repos)
    for repo in dc.CORE_REPOS:
        assert repo in main_repos
    pinned = {
        (s.repo, s.ref)
        for s in got
        if s.ref is not None and not s.upstream and not s.external
    }
    assert pinned == {
        ("ansible_collection_commons", "v0.20261005.0"),
        ("ansible_collection_services", "v0.20261007.0"),
        ("ansible_playbooks", "v0.20261005.0"),
        ("ansible_playbooks_manager", "v0.20260721.0"),
        ("generics", "v0.20261007.0"),
    }
    upstream = {(s.repo, s.ref) for s in got if s.upstream and not s.external}
    assert upstream == {
        ("kolla_ansible", "stable/2025.2"),
        ("kolla_ansible", "stable/2026.1"),
        ("ceph_ansible", "stable-7.0"),
        ("ceph_ansible", "stable-8.0"),
    }
    external = {(s.repo, s.ref) for s in got if s.external}
    assert external == {
        ("geerlingguy/ansible-role-dotfiles", "master"),
        ("openstack/ansible-hardening", "e77c311442cb1d1ef8caa7df9d9c00471afa75e7"),
        ("PowerDNS/pdns_recursor-ansible", "v1.8.1"),
    }
    assert len(got) == len(set(got))


def test_sources_fail_on_a_missing_pin_key(monkeypatch):
    _stub_release(
        monkeypatch, base=_BASE_YML.replace(b"generics_version", b"other_version")
    )
    with pytest.raises(SourceError, match="generics_version"):
        dc.sources(None)


def test_sources_fail_on_empty_release_range(monkeypatch):
    # No upstream would report every kolla-only variable.
    _stub_release(monkeypatch, releases=())
    with pytest.raises(SourceError, match="release"):
        dc.sources(None)


def test_sources_fail_without_ceph_ansible_flavour(monkeypatch):
    _stub_release(monkeypatch, flavours=())
    with pytest.raises(SourceError, match="ceph-ansible"):
        dc.sources(None)


def test_iter_files_skips_git_prose_and_upstream_tests(tmp_path, git_repo, make_cfg):
    git_repo(
        "kolla_ansible".replace("_", "-"),
        {
            "stable/x": {
                "ansible/roles/a/tasks/main.yml": "x: '{{ reader }}'\n",
                "tests/fixture.yml": "x: '{{ fixture_only }}'\n",
                "doc/source/a.yml": "x: '{{ doc_only }}'\n",
                "README.rst": "prose_only\n",
            }
        },
    )
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "tests").mkdir()
    (tmp_path / "widget" / "tests" / "t.yml").write_text(
        "x: '{{ osism_test_reader }}'\n"
    )
    (tmp_path / "widget" / "CHANGELOG.md").write_text("changelog_only\n")
    cfg = make_cfg(tmp_path, pinned={"kolla_ansible": "main"})

    upstream = dict(
        dc.iter_files(dc.Source("kolla_ansible", "stable/x", upstream=True), cfg)
    )
    assert list(upstream) == ["ansible/roles/a/tasks/main.yml"]

    osism = dict(dc.iter_files(dc.Source("widget", None), cfg))
    assert list(osism) == ["tests/t.yml"]  # OSISM tests/ still count; prose does not


def test_iter_files_skips_upstream_tox_files(tmp_path, git_repo, make_cfg):
    # ceph-ansible tox-update.ini passes `--extra-vars "osd_scenario=lvm"` to
    # tests/functional/lvm_setup.yml: tox files drive the skipped tests/ tree,
    # so a name they mention is not a read.
    git_repo(
        "ceph-ansible",
        {
            "stable-8.0": {
                "roles/a/tasks/main.yml": "x: '{{ reader }}'\n",
                "tox.ini": "[testenv]\n",
                "tox-update.ini": "commands = -e osd_scenario=lvm\n",
                "roles/a/files/tox.ini": "nested_tox_reader\n",
            }
        },
    )
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "tox.ini").write_text("osism_tox_reader\n")
    cfg = make_cfg(tmp_path, pinned={"ceph_ansible": "main"})

    upstream = dict(
        dc.iter_files(dc.Source("ceph_ansible", "stable-8.0", upstream=True), cfg)
    )
    assert sorted(upstream) == ["roles/a/files/tox.ini", "roles/a/tasks/main.yml"]

    osism = dict(dc.iter_files(dc.Source("widget", None), cfg))
    assert list(osism) == ["tox.ini"]  # only upstream tox files are skipped


def test_binary_files_are_skipped(tmp_path, make_cfg):
    # Binary and undecodable files must not break or pollute the scan.
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "logo.png").write_bytes(b"\x89PNG\x00\x00binary_token")
    (tmp_path / "widget" / "bad.txt").write_bytes(b"latin1_ok \xff\xfe\n")
    cfg = make_cfg(tmp_path)
    files = dict(dc.iter_files(dc.Source("widget", None), cfg))
    assert "logo.png" not in files
    assert "latin1_ok" in files["bad.txt"]


def test_scan_unions_reads_across_refs(tmp_path, git_repo, make_cfg):
    git_repo(
        "ceph-ansible",
        {
            "stable-7.0": {"roles/facts/tasks/main.yml": "x: '{{ old_reader_var }}'\n"},
            "stable-8.0": {"roles/facts/tasks/main.yml": "x: 1\n"},
        },
    )
    cfg = make_cfg(tmp_path, pinned={"ceph_ansible": "main"})
    srcs = [
        dc.Source("ceph_ansible", "stable-7.0", upstream=True),
        dc.Source("ceph_ansible", "stable-8.0", upstream=True),
    ]
    corpus = dc.scan(cfg, srcs)
    assert corpus.reads("old_reader_var")
    assert corpus.labels == ["ceph-ansible@stable-7.0", "ceph-ansible@stable-8.0"]


def test_scan_reads_an_osism_repo_at_its_pin(tmp_path, git_repo, make_cfg):
    git_repo(
        "ansible-collection-services",
        {
            "v0.1.0": {"roles/r/tasks/main.yml": "x: '{{ pinned_reader_var }}'\n"},
            "v0.2.0": {"roles/r/tasks/main.yml": "x: 1\n"},
        },
    )
    cfg = make_cfg(tmp_path)
    at_main = dc.scan(cfg, [dc.Source("ansible_collection_services", None)])
    at_pin = dc.scan(cfg, [dc.Source("ansible_collection_services", "v0.1.0")])
    assert not at_main.reads("pinned_reader_var")
    assert at_pin.reads("pinned_reader_var")


def test_scan_collects_patterns_unresolved_calls_and_near_misses(tmp_path, make_cfg):
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "a.yml").write_text(
        "x: \"{{ lookup('community.general.merge_variables', '^k3s_add_labels__.+$') }}\"\n"
        "y: \"{{ lookup('community.general.merge_variables', some_var) }}\"\n"
    )
    cfg = make_cfg(tmp_path)
    corpus = dc.scan(cfg, [dc.Source("widget", None)])
    assert corpus.reads("k3s_add_labels__network")
    assert not corpus.reads("k3s_add_labels_monitoring")
    assert corpus.near_miss("k3s_add_labels_monitoring") == (
        "k3s_add_labels__monitoring",
        "^k3s_add_labels__.+$",
        "widget@main:a.yml",
    )
    assert corpus.unresolved == ["widget@main:a.yml"]


def test_external_sources_follow_the_others_sorted_by_role_name(monkeypatch):
    _stub_release(monkeypatch)
    got = dc.sources(None)
    ext = [s for s in got if s.external]
    assert got[-len(ext) :] == ext
    assert ext == [
        dc.Source(
            "geerlingguy/ansible-role-dotfiles", "master", upstream=True, external=True
        ),
        dc.Source(
            "openstack/ansible-hardening",
            "e77c311442cb1d1ef8caa7df9d9c00471afa75e7",
            upstream=True,
            external=True,
        ),
        dc.Source(
            "PowerDNS/pdns_recursor-ansible", "v1.8.1", upstream=True, external=True
        ),
    ]


def test_role_pins_are_used_verbatim(monkeypatch):
    base = _BASE_YML.replace(b"master", b"1a2b3c4").replace(b"v1.8.1", b"3.1.0")
    _stub_release(monkeypatch, base=base)
    refs = {s.repo: s.ref for s in dc.sources(None) if s.external}
    assert refs["geerlingguy/ansible-role-dotfiles"] == "1a2b3c4"
    assert refs["PowerDNS/pdns_recursor-ansible"] == "3.1.0"


def test_a_pinned_role_missing_from_roles_yml_fails(monkeypatch):
    _stub_release(monkeypatch, roles=_ROLES_YML.replace(b"hardening:", b"other_role:"))
    with pytest.raises(SourceError, match="hardening"):
        dc.sources(None)


@pytest.mark.parametrize("block", [b"ansible_roles:\n", b"ansible_roles: {}\n"])
def test_empty_pinned_roles_adds_no_external_sources(monkeypatch, block):
    head = _BASE_YML.split(b"ansible_roles:")[0]
    _stub_release(monkeypatch, base=head + block)
    assert not [s for s in dc.sources(None) if s.external]


def test_missing_ansible_roles_key_is_an_error(monkeypatch):
    head = _BASE_YML.split(b"ansible_roles:")[0]
    _stub_release(monkeypatch, base=head)
    with pytest.raises(SourceError, match="ansible_roles"):
        dc.sources(None)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.replace(b"  osism.commons", b"  x.commons").replace(
            b"  osism.services", b"  x.services"
        ),
        lambda b: b.replace(b"ansible_collections:", b"collections:"),
    ],
)
def test_no_osism_collection_is_an_error(monkeypatch, mutate):
    _stub_release(monkeypatch, base=mutate(_BASE_YML))
    with pytest.raises(SourceError, match="osism"):
        dc.sources(None)


def test_external_label_is_verbatim():
    src = dc.Source(
        "PowerDNS/pdns_recursor-ansible", "v1.8.1", upstream=True, external=True
    )
    assert src.label(None) == "PowerDNS/pdns_recursor-ansible@v1.8.1"


def test_iter_files_walks_the_github_tree_with_upstream_skips(tmp_path, monkeypatch):
    root = tmp_path / "role"
    for rel, text in {
        "tasks/main.yml": "x: '{{ role_reader }}'\n",
        "tests/test.yml": "x: '{{ test_only }}'\n",
        "molecule/default/converge.yml": "x: '{{ molecule_reader }}'\n",
        "tox.ini": "[testenv]\n",
        "README.md": "prose_only\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    calls = []

    def fake(owner, slug, ref, config):
        calls.append((owner, slug, ref))
        return root

    monkeypatch.setattr(dc.source, "github_tree_dir", fake)
    src = dc.Source("PowerDNS/pdns_recursor-ansible", "v1.8.1", True, True)
    got = dict(dc.iter_files(src, None))
    assert calls == [("PowerDNS", "pdns_recursor-ansible", "v1.8.1")]
    assert sorted(got) == ["molecule/default/converge.yml", "tasks/main.yml"]


def test_walk_skips_tooling_directories(tmp_path, make_cfg):
    (tmp_path / "widget").mkdir()
    for d in (".tox", ".venv", "venv", "node_modules", "src"):
        (tmp_path / "widget" / d).mkdir()
        (tmp_path / "widget" / d / "x.yml").write_text("a: 1\n")
    cfg = make_cfg(tmp_path)
    files = dict(dc.iter_files(dc.Source("widget", None), cfg))
    assert list(files) == ["src/x.yml"]


def test_commented_merge_calls_are_ignored(tmp_path, make_cfg):
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "a.yml").write_text(
        "# x: \"{{ lookup('community.general.merge_variables', '^old__.+$') }}\"\n"
        "  # y: \"{{ lookup('community.general.merge_variables', some_var) }}\"\n"
    )
    cfg = make_cfg(tmp_path)
    corpus = dc.scan(cfg, [dc.Source("widget", None)])
    assert not corpus.reads("old__x")
    assert corpus.unresolved == []
