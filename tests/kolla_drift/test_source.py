import dataclasses
import subprocess as _sub

import pytest
import requests
import responses
from osism_drift.source import read, SourceError, read_optional, list_dir
from osism_drift.config import load_config, SourceCfg


def _cfg(tmp_path, base_dirs=(), remote_fallback=False):
    cfg = tmp_path / "c.yml"
    cfg.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  default_owner: osism
  branch: main
release_version: latest
plugins:
  release_vs_manager: {enabled: true}
""")
    return dataclasses.replace(
        load_config(cfg),
        base_dirs=tuple(str(b) for b in base_dirs),
        remote_fallback=remote_fallback,
    )


def _make_cfg(tmp_path, sources=None, release_refs=None):
    import yaml

    p = tmp_path / "src.yml"
    body = {
        "remote": {
            "github_raw": "https://raw.githubusercontent.com/",
            "github_api": "https://api.github.com/repos/",
            "branch": "main",
            "default_owner": "osism",
        },
        "release_version": "latest",
        "plugins": {},
    }
    if sources:
        body["sources"] = sources
    if release_refs:
        body["release_refs"] = release_refs
    p.write_text(yaml.safe_dump(body))
    return p


def test_read_local_working_tree_when_found(tmp_path):
    (tmp_path / "release" / "latest").mkdir(parents=True)
    (tmp_path / "release" / "latest" / "base.yml").write_text("hello")
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))
    assert read("release", "latest/base.yml", cfg) == b"hello"


def test_read_not_found_in_base_dir_is_mode_b_error(tmp_path):
    # release dir absent under the only base-dir, no --remote-fallback -> hard error.
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))
    with pytest.raises(SourceError, match="not found under any --base-dir"):
        read("release", "latest/base.yml", cfg)


def test_read_local_file_absent_is_error_not_remote(tmp_path):
    # repo dir present, the file itself absent -> hard error, never silent remote.
    (tmp_path / "release").mkdir()
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))
    with pytest.raises(SourceError, match="not found in local"):
        read("release", "latest/base.yml", cfg)


@responses.activate
def test_read_remote_fallback_when_not_found(tmp_path):
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/release/main/latest/base.yml",
        body="remote-body",
        status=200,
    )
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,), remote_fallback=True)
    assert read("release", "latest/base.yml", cfg) == b"remote-body"


@responses.activate
def test_read_no_base_dir_is_remote(tmp_path):
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/release/main/latest/base.yml",
        body="remote-only",
        status=200,
    )
    cfg = _cfg(tmp_path)
    assert read("release", "latest/base.yml", cfg) == b"remote-only"


def test_first_base_dir_with_the_repo_wins(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    (b / "release" / "latest").mkdir(parents=True)
    (b / "release" / "latest" / "base.yml").write_text("from-b")
    (a).mkdir()  # a/ exists but has no release/
    cfg = _cfg(tmp_path, base_dirs=(a, b))
    assert read("release", "latest/base.yml", cfg) == b"from-b"


def _pinned_kolla_cfg(tmp_path, **kw):
    return dataclasses.replace(
        _cfg(tmp_path, base_dirs=(tmp_path,), **kw),
        sources={"kolla": SourceCfg(owner="openstack", branch="stable/2025.2")},
    )


def test_pinned_local_git_repo_reads_at_pin_ref(tmp_path):
    # kolla is a real git clone under the base-dir -> read at the pin ref via git.
    from osism_drift.source import list_dir

    _make_repo(
        tmp_path / "kolla",
        [
            ("main", "branch", {"docker/old.txt": "x"}),
            (
                "stable/2025.2",
                "branch",
                {"docker/nova/Dockerfile.j2": "n", "docker/base/Dockerfile.j2": "b"},
            ),
        ],
    )
    cfg = _pinned_kolla_cfg(tmp_path)
    assert read("kolla", "docker/nova/Dockerfile.j2", cfg) == b"n"
    assert sorted(list_dir("kolla", "docker", cfg, dirs_only=True)) == ["base", "nova"]


@responses.activate
def test_pinned_non_git_dir_falls_back_to_remote(tmp_path):
    # A plain (non-git) kolla/ dir cannot serve a pinned repo -> --remote-fallback goes remote.
    (tmp_path / "kolla" / "docker").mkdir(parents=True)
    (tmp_path / "kolla" / "docker" / "x.txt").write_text("local")
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla/stable/2025.2/docker/x.txt",
        body="remote",
        status=200,
    )
    cfg = _pinned_kolla_cfg(tmp_path, remote_fallback=True)
    assert read("kolla", "docker/x.txt", cfg) == b"remote"


def test_pinned_non_git_dir_no_fallback_is_mode_b(tmp_path):
    (tmp_path / "kolla").mkdir()  # plain dir, not a git repo, no fallback
    cfg = _pinned_kolla_cfg(tmp_path)
    with pytest.raises(SourceError, match="not found under any --base-dir"):
        read("kolla", "docker/x.txt", cfg)


@responses.activate
def test_read_remote_404_errors(tmp_path):
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/release/main/latest/base.yml",
        body="not found",
        status=404,
    )
    cfg = _cfg(tmp_path)
    with pytest.raises(SourceError, match="404"):
        read("release", "latest/base.yml", cfg)


@responses.activate
def test_read_network_error(tmp_path):
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/release/main/latest/base.yml",
        body=requests.ConnectionError("boom"),
    )
    cfg = _cfg(tmp_path)
    with pytest.raises(SourceError, match="boom"):
        read("release", "latest/base.yml", cfg)


@responses.activate
def test_read_underscore_repo_translates_to_hyphen(tmp_path):
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/ansible-collection-services/main/roles/x/defaults/main.yml",
        body="ok",
        status=200,
    )
    cfg = _cfg(tmp_path)
    assert (
        read("ansible_collection_services", "roles/x/defaults/main.yml", cfg) == b"ok"
    )


def test_read_optional_present_local(tmp_path):
    d = tmp_path / "acs" / "roles" / "x" / "defaults"
    d.mkdir(parents=True)
    (d / "main.yml").write_text("ok")
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))
    assert read_optional("acs", "roles/x/defaults/main.yml", cfg) == b"ok"


def test_read_optional_missing_local_returns_none(tmp_path):
    (tmp_path / "acs" / "roles" / "x").mkdir(parents=True)  # repo present, file absent
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))
    assert read_optional("acs", "roles/x/defaults/main.yml", cfg) is None


@responses.activate
def test_read_optional_404_returns_none(tmp_path):
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/acs/main/roles/x/defaults/main.yml",
        status=404,
    )
    cfg = _cfg(tmp_path)
    assert read_optional("acs", "roles/x/defaults/main.yml", cfg) is None


@responses.activate
def test_read_optional_network_error_still_raises(tmp_path):
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/acs/main/roles/x/defaults/main.yml",
        body=requests.ConnectionError("dns"),
    )
    cfg = _cfg(tmp_path)
    with pytest.raises(SourceError):
        read_optional("acs", "roles/x/defaults/main.yml", cfg)


@responses.activate
def test_read_optional_underscore_repo_translates_to_hyphen(tmp_path):
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/ansible-collection-services/main/roles/x/defaults/main.yml",
        status=404,
    )
    cfg = _cfg(tmp_path)
    assert (
        read_optional("ansible_collection_services", "roles/x/defaults/main.yml", cfg)
        is None
    )


def test_list_dir_local(tmp_path):
    for r in ("a", "b", "c"):
        (tmp_path / "acs" / "roles" / r).mkdir(parents=True)
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))
    assert sorted(list_dir("acs", "roles", cfg)) == ["a", "b", "c"]


@responses.activate
def test_list_dir_remote(tmp_path):
    responses.add(
        responses.GET,
        "https://api.github.com/repos/osism/acs/contents/roles?ref=main",
        json=[{"name": "a", "type": "dir"}, {"name": "b", "type": "dir"}],
        status=200,
    )
    cfg = _cfg(tmp_path)
    assert sorted(list_dir("acs", "roles", cfg)) == ["a", "b"]


@responses.activate
def test_list_dir_remote_404_errors(tmp_path):
    responses.add(
        responses.GET,
        "https://api.github.com/repos/osism/acs/contents/roles?ref=main",
        status=404,
    )
    cfg = _cfg(tmp_path)
    with pytest.raises(SourceError, match="404"):
        list_dir("acs", "roles", cfg)


@responses.activate
def test_list_dir_underscore_repo_translates_to_hyphen(tmp_path):
    responses.add(
        responses.GET,
        "https://api.github.com/repos/osism/ansible-collection-services/contents/roles?ref=main",
        json=[{"name": "a", "type": "dir"}],
        status=200,
    )
    cfg = _cfg(tmp_path)
    assert list_dir("ansible_collection_services", "roles", cfg) == ["a"]


@responses.activate
def test_source_override_redirects_owner_and_ref(tmp_path):
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla/stable/2025.2/docker/x/Dockerfile.j2",
        body="ok",
        status=200,
    )
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  default_owner: osism
  branch: main
sources:
  kolla: {owner: openstack, branch: stable/2025.2}
release_version: latest
plugins: {}
""")
    cfg = load_config(cfg_path)
    assert read("kolla", "docker/x/Dockerfile.j2", cfg) == b"ok"


def test_list_dir_local_dirs_only_excludes_files(tmp_path):
    # kolla unpinned here -> working-tree listing discovered under the base-dir.
    (tmp_path / "kolla" / "docker" / "nova").mkdir(parents=True)
    (tmp_path / "kolla" / "docker" / "base").mkdir(parents=True)
    (tmp_path / "kolla" / "docker" / "macros.j2").write_text("{# jinja #}")
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))
    assert sorted(list_dir("kolla", "docker", cfg, dirs_only=True)) == ["base", "nova"]
    assert "macros.j2" in list_dir("kolla", "docker", cfg)


@responses.activate
def test_list_dir_remote_dirs_only_filters_type(tmp_path):
    responses.add(
        responses.GET,
        "https://api.github.com/repos/openstack/kolla/contents/docker?ref=stable/2025.2",
        json=[{"name": "nova", "type": "dir"}, {"name": "macros.j2", "type": "file"}],
        status=200,
    )
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("""
remote:
  github_raw: https://raw.githubusercontent.com/
  github_api: https://api.github.com/repos/
  default_owner: osism
  branch: main
sources:
  kolla: {owner: openstack, branch: stable/2025.2}
release_version: latest
plugins: {}
""")
    cfg = load_config(cfg_path)
    assert list_dir("kolla", "docker", cfg, dirs_only=True) == ["nova"]


@responses.activate
def test_list_dir_at_ref_uses_explicit_ref_and_owner(tmp_path):
    # kolla pinned to a DIFFERENT branch; list_dir_at_ref must ignore the pin.
    # responses ignores the query string when matching, so register the bare URL
    # and assert the ref/owner via the recorded request (no matcher API needed).
    cfg = load_config(
        _make_cfg(
            tmp_path,
            sources={"kolla": {"owner": "openstack", "branch": "stable/2025.2"}},
        )
    )
    responses.add(
        responses.GET,
        "https://api.github.com/repos/openstack/kolla/contents/docker",
        json=[
            {"name": "valkey", "type": "dir"},
            {"name": "README.rst", "type": "file"},
        ],
        status=200,
    )
    from osism_drift.source import list_dir_at_ref

    out = list_dir_at_ref("kolla", "docker", "unmaintained/2024.1", cfg, dirs_only=True)
    assert out == ["valkey"]
    assert "/openstack/kolla/contents/docker" in responses.calls[0].request.url
    assert "ref=unmaintained/2024.1" in responses.calls[0].request.url


@responses.activate
def test_list_dir_at_ref_404_raises(tmp_path):
    cfg = load_config(_make_cfg(tmp_path, sources={"kolla": {"owner": "openstack"}}))
    responses.add(
        responses.GET,
        "https://api.github.com/repos/openstack/kolla/contents/docker",
        status=404,
    )
    from osism_drift.source import list_dir_at_ref, SourceError

    with pytest.raises(SourceError):
        list_dir_at_ref("kolla", "docker", "stable/2099.1", cfg)


def _commits_url(owner, repo, ref):
    return f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"


@responses.activate
def test_release_to_ref_prefers_stable(tmp_path):
    cfg = load_config(_make_cfg(tmp_path, sources={"kolla": {"owner": "openstack"}}))
    responses.add(
        responses.GET, _commits_url("openstack", "kolla", "stable/2025.2"), status=200
    )
    from osism_drift.source import release_to_ref

    assert release_to_ref("kolla", "2025.2", cfg) == "stable/2025.2"


@responses.activate
def test_release_to_ref_falls_back_through_candidates(tmp_path):
    cfg = load_config(_make_cfg(tmp_path, sources={"kolla": {"owner": "openstack"}}))
    responses.add(
        responses.GET, _commits_url("openstack", "kolla", "stable/2024.1"), status=404
    )
    responses.add(
        responses.GET,
        _commits_url("openstack", "kolla", "unmaintained/2024.1"),
        status=200,
    )
    from osism_drift.source import release_to_ref

    assert release_to_ref("kolla", "2024.1", cfg) == "unmaintained/2024.1"


@responses.activate
def test_release_to_ref_treats_422_as_absent(tmp_path):
    # GitHub's commits API returns 422 (not 404) for a ref that does not resolve
    # (e.g. stable/2024.1 once a release moves to unmaintained/). The probe must
    # treat it as absent and fall through, not abort.
    cfg = load_config(_make_cfg(tmp_path, sources={"kolla": {"owner": "openstack"}}))
    responses.add(
        responses.GET, _commits_url("openstack", "kolla", "stable/2024.1"), status=422
    )
    responses.add(
        responses.GET,
        _commits_url("openstack", "kolla", "unmaintained/2024.1"),
        status=200,
    )
    from osism_drift.source import release_to_ref

    assert release_to_ref("kolla", "2024.1", cfg) == "unmaintained/2024.1"


@responses.activate
def test_release_to_ref_uses_eom_tag_last(tmp_path):
    cfg = load_config(_make_cfg(tmp_path, sources={"kolla": {"owner": "openstack"}}))
    for ref, st in [
        ("stable/2024.1", 404),
        ("unmaintained/2024.1", 404),
        ("2024.1-eol", 404),
        ("2024.1-eom", 200),
    ]:
        responses.add(responses.GET, _commits_url("openstack", "kolla", ref), status=st)
    from osism_drift.source import release_to_ref

    assert release_to_ref("kolla", "2024.1", cfg) == "2024.1-eom"


@responses.activate
def test_release_to_ref_none_raises(tmp_path):
    cfg = load_config(_make_cfg(tmp_path, sources={"kolla": {"owner": "openstack"}}))
    for ref in ["stable/2099.1", "unmaintained/2099.1", "2099.1-eol", "2099.1-eom"]:
        responses.add(
            responses.GET, _commits_url("openstack", "kolla", ref), status=404
        )
    from osism_drift.source import release_to_ref, SourceError

    with pytest.raises(SourceError, match="no upstream ref"):
        release_to_ref("kolla", "2099.1", cfg)


def test_release_to_ref_override_wins(tmp_path):
    # No responses registered: override must short-circuit before any HTTP call.
    cfg = load_config(
        _make_cfg(
            tmp_path,
            sources={"kolla": {"owner": "openstack"}},
            release_refs={"kolla": {"2024.2": "2024.2-eol"}},
        )
    )
    from osism_drift.source import release_to_ref

    assert release_to_ref("kolla", "2024.2", cfg) == "2024.2-eol"


@responses.activate
def test_read_at_ref_returns_bytes_at_explicit_ref(tmp_path):
    # kolla_ansible pinned to a DIFFERENT branch; read_at_ref must ignore the pin
    # and read the supplied ref, honouring the owner override.
    cfg = load_config(
        _make_cfg(
            tmp_path,
            sources={
                "kolla_ansible": {"owner": "openstack", "branch": "stable/2025.2"}
            },
        )
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "unmaintained/2024.1/ansible/group_vars/all.yml",
        body=b'enable_redis: "no"\n',
        status=200,
    )
    from osism_drift.source import read_at_ref

    out = read_at_ref(
        "kolla_ansible", "ansible/group_vars/all.yml", "unmaintained/2024.1", cfg
    )
    assert out == b'enable_redis: "no"\n'
    assert (
        "/openstack/kolla-ansible/unmaintained/2024.1/ansible/group_vars/all.yml"
        in responses.calls[0].request.url
    )


@responses.activate
def test_read_at_ref_optional_404_returns_none(tmp_path):
    cfg = load_config(
        _make_cfg(tmp_path, sources={"kolla_ansible": {"owner": "openstack"}})
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.2/ansible/group_vars/all.yml",
        status=404,
    )
    from osism_drift.source import read_at_ref

    assert (
        read_at_ref(
            "kolla_ansible",
            "ansible/group_vars/all.yml",
            "stable/2025.2",
            cfg,
            optional=True,
        )
        is None
    )


@responses.activate
def test_read_at_ref_404_raises_when_not_optional(tmp_path):
    cfg = load_config(
        _make_cfg(tmp_path, sources={"kolla_ansible": {"owner": "openstack"}})
    )
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/openstack/kolla-ansible/"
        "stable/2025.2/ansible/group_vars/all/x.yml",
        status=404,
    )
    from osism_drift.source import read_at_ref, SourceError

    with pytest.raises(SourceError):
        read_at_ref(
            "kolla_ansible", "ansible/group_vars/all/x.yml", "stable/2025.2", cfg
        )


def test_describe_resolution_local_and_remote(tmp_path):
    from osism_drift.source import describe_resolution

    (tmp_path / "defaults").mkdir()
    # kolla is pinned but has no local git clone -> remote_fallback lets it read remotely
    cfg = dataclasses.replace(
        _cfg(tmp_path, base_dirs=(tmp_path,), remote_fallback=True),
        sources={"kolla": SourceCfg(owner="openstack", branch="stable/2025.2")},
    )
    lines = describe_resolution(["kolla", "defaults"], cfg)
    joined = "\n".join(lines)
    assert "defaults" in joined and "local" in joined and "working tree" in joined
    assert "kolla" in joined and "remote" in joined and "stable/2025.2" in joined


def test_describe_resolution_mode_b_raises(tmp_path):
    from osism_drift.source import describe_resolution, SourceError

    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))  # defaults/ absent, no fallback
    with pytest.raises(SourceError, match="not found under any --base-dir"):
        describe_resolution(["defaults"], cfg)


def test_describe_resolution_lists_all_missing(tmp_path):
    from osism_drift.source import describe_resolution, SourceError

    # None of the three repos exist under base_dir -> all reported at once.
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))
    with pytest.raises(SourceError) as exc:
        describe_resolution(["defaults", "kolla", "release"], cfg)
    msg = str(exc.value)
    assert "defaults" in msg and "kolla" in msg and "release" in msg


def _git_init(path):
    path.mkdir(parents=True, exist_ok=True)
    import os as _os

    # Strip agent env vars so the OSISM commit-msg hook does not fire on
    # throwaway fixture repos (hook only enforces when CLAUDECODE/AI_AGENT is set).
    _env = {k: v for k, v in _os.environ.items() if k not in ("CLAUDECODE", "AI_AGENT")}

    def g(*a):
        _sub.run(
            ["git", "-C", str(path), *a], check=True, capture_output=True, env=_env
        )

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "T")
    return g


def _make_repo(path, refs):
    # refs: list of (ref_name, kind 'branch'|'tag', {relpath: content}); commit
    # each snapshot in order and label it. We read objects, not the work tree.
    g = _git_init(path)
    for i, (ref, kind, files) in enumerate(refs):
        g(
            "rm", "-r", "--quiet", "--ignore-unmatch", "."
        )  # clear prior snapshot -> each ref's tree is exactly `files`
        for rel, content in files.items():
            f = path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
            g("add", rel)
        g("commit", "-q", "-m", ref)
        if i == 0:
            g(
                "checkout", "-q", "--detach"
            )  # no current branch, so `branch -f <name>` works for any name (incl. the default)
        g("branch" if kind == "branch" else "tag", "-f", ref)
    return path


def test_git_show_multi_ref_from_one_clone(tmp_path):
    from osism_drift.source import _git_show

    repo = _make_repo(
        tmp_path / "kolla-ansible",
        [
            (
                "stable/2025.1",
                "branch",
                {"ansible/group_vars/all.yml": 'enable_redis: "no"\n'},
            ),
            (
                "stable/2025.2",
                "branch",
                {"ansible/group_vars/all.yml": 'enable_valkey: "no"\n'},
            ),
        ],
    )
    assert (
        _git_show(repo, "stable/2025.1", "ansible/group_vars/all.yml")
        == b'enable_redis: "no"\n'
    )
    assert (
        _git_show(repo, "stable/2025.2", "ansible/group_vars/all.yml")
        == b'enable_valkey: "no"\n'
    )


def test_git_show_optional_absent_path(tmp_path):
    from osism_drift.source import _git_show, SourceError

    repo = _make_repo(
        tmp_path / "kolla-ansible",
        [
            (
                "stable/2025.2",
                "branch",
                {"ansible/group_vars/all/valkey.yml": 'enable_valkey: "no"\n'},
            ),
        ],
    )
    # ref exists, path absent (monolithic all.yml not present at 2025.2):
    assert (
        _git_show(repo, "stable/2025.2", "ansible/group_vars/all.yml", optional=True)
        is None
    )
    with pytest.raises(SourceError):
        _git_show(repo, "stable/2025.2", "ansible/group_vars/all.yml")


def test_git_ls_tree_returns_basenames_and_dirs_only(tmp_path):
    from osism_drift.source import _git_ls_tree

    repo = _make_repo(
        tmp_path / "kolla",
        [
            (
                "stable/2025.2",
                "branch",
                {
                    "docker/nova/Dockerfile.j2": "x",
                    "docker/base/Dockerfile.j2": "y",
                    "docker/macros.j2": "{# jinja #}",
                },
            ),
        ],
    )
    assert sorted(_git_ls_tree(repo, "stable/2025.2", "docker")) == [
        "base",
        "macros.j2",
        "nova",
    ]
    assert sorted(_git_ls_tree(repo, "stable/2025.2", "docker", dirs_only=True)) == [
        "base",
        "nova",
    ]


def test_resolve_local_ref_finds_non_origin_remote(tmp_path):
    from osism_drift.source import _resolve_local_ref, _git_show

    # upstream clone holds the canonical ref; main clone has it only as a
    # remote-tracking ref under a remote named "gerrit".
    up = _make_repo(
        tmp_path / "up",
        [
            ("unmaintained/2024.1", "branch", {"f.txt": "hi"}),
        ],
    )
    main = tmp_path / "kolla-ansible"
    _git_init(main)
    _sub.run(
        ["git", "-C", str(main), "remote", "add", "gerrit", str(up)],
        check=True,
        capture_output=True,
    )
    _sub.run(
        ["git", "-C", str(main), "fetch", "-q", "gerrit"],
        check=True,
        capture_output=True,
    )
    # a bare "unmaintained/2024.1" is not a local branch here, only gerrit/unmaintained/2024.1:
    assert (
        _resolve_local_ref(main, "unmaintained/2024.1") == "gerrit/unmaintained/2024.1"
    )
    assert _git_show(main, "unmaintained/2024.1", "f.txt") == b"hi"


def test_git_show_unresolvable_ref_raises(tmp_path):
    from osism_drift.source import _git_show, SourceError

    repo = _make_repo(tmp_path / "kolla", [("main", "branch", {"f": "x"})])
    with pytest.raises(SourceError, match="not found"):
        _git_show(repo, "stable/2099.1", "f")


def test_git_ref_exists(tmp_path):
    from osism_drift.source import _git_ref_exists

    repo = _make_repo(tmp_path / "kolla", [("stable/2025.2", "branch", {"f": "x"})])
    assert _git_ref_exists(repo, "stable/2025.2") is True
    assert _git_ref_exists(repo, "stable/2099.1") is False


def test_read_at_ref_local_git(tmp_path):
    from osism_drift.source import read_at_ref, list_dir_at_ref, ref_exists

    _make_repo(
        tmp_path / "kolla-ansible",
        [
            (
                "stable/2025.1",
                "branch",
                {"ansible/group_vars/all.yml": 'enable_redis: "no"\n'},
            ),
            (
                "stable/2025.2",
                "branch",
                {"ansible/group_vars/all/valkey.yml": 'enable_valkey: "no"\n'},
            ),
        ],
    )
    cfg = dataclasses.replace(
        _cfg(tmp_path, base_dirs=(tmp_path,)),
        sources={"kolla_ansible": SourceCfg(owner="openstack", branch="main")},
    )
    # explicit ref, ignoring the pin:
    assert (
        read_at_ref("kolla_ansible", "ansible/group_vars/all.yml", "stable/2025.1", cfg)
        == b'enable_redis: "no"\n'
    )
    # optional miss at a ref where the path is absent:
    assert (
        read_at_ref(
            "kolla_ansible",
            "ansible/group_vars/all.yml",
            "stable/2025.2",
            cfg,
            optional=True,
        )
        is None
    )
    assert list_dir_at_ref(
        "kolla_ansible", "ansible/group_vars/all", "stable/2025.2", cfg
    ) == ["valkey.yml"]
    assert ref_exists("kolla_ansible", "stable/2025.1", cfg) is True
    assert ref_exists("kolla_ansible", "stable/2099.1", cfg) is False


def test_describe_resolution_pinned_local_tag(tmp_path):
    from osism_drift.source import describe_resolution

    _make_repo(
        tmp_path / "kolla",
        [("stable/2025.2", "branch", {"docker/x/Dockerfile.j2": "x"})],
    )
    cfg = dataclasses.replace(
        _cfg(tmp_path, base_dirs=(tmp_path,)),
        sources={"kolla": SourceCfg(owner="openstack", branch="stable/2025.2")},
    )
    line = "\n".join(describe_resolution(["kolla"], cfg))
    assert "kolla" in line and "local" in line and "git refs, must be current" in line


@responses.activate
def test_read_at_ref_unpinned_local_dir_is_remote(tmp_path):
    # An UNPINNED plain dir under --base-dir must not be git-read by read_at_ref
    # (it has no .git); it reads remotely at the explicit ref instead of crashing git.
    from osism_drift.source import read_at_ref

    (tmp_path / "acs").mkdir()
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/acs/stable/2025.2/x.yml",
        body="remote",
        status=200,
    )
    cfg = _cfg(tmp_path, base_dirs=(tmp_path,))  # acs unpinned (no sources)
    assert read_at_ref("acs", "x.yml", "stable/2025.2", cfg) == b"remote"


def test_release_to_ref_memoizes_probes(monkeypatch):
    """A second resolve of the same (repo, release) does not re-probe refs."""
    from osism_drift import source
    from osism_drift.config import Config, Remote

    calls = []

    def fake_ref_exists(repo, ref, config):
        calls.append((repo, ref))
        return ref == "stable/X"

    monkeypatch.setattr(source, "ref_exists", fake_ref_exists)
    cfg = Config(
        remote=Remote("https://raw/", "https://api/", "main", "osism"),
        release_version="latest",
        plugins={},
    )
    assert source.release_to_ref("kolla", "X", cfg) == "stable/X"
    first = len(calls)
    assert first >= 1
    assert source.release_to_ref("kolla", "X", cfg) == "stable/X"
    assert len(calls) == first  # served from cache, no new probes
    assert cfg.ref_cache[("kolla", "X")] == "stable/X"


@responses.activate
def test_read_remote_rate_limit_403_gives_helpful_error(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/release/main/latest/base.yml",
        status=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "60"},
    )
    cfg = _cfg(tmp_path)
    with pytest.raises(SourceError) as exc:
        read("release", "latest/base.yml", cfg)
    msg = str(exc.value)
    assert "HTTP 403" in msg
    assert "rate limit" in msg
    assert "60/hr" in msg
    assert "GITHUB_TOKEN" in msg  # unauthenticated -> advise setting a token


@responses.activate
def test_rate_limit_error_when_authenticated_omits_token_advice(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/release/main/latest/base.yml",
        status=429,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "5000"},
    )
    cfg = _cfg(tmp_path)
    with pytest.raises(SourceError) as exc:
        read("release", "latest/base.yml", cfg)
    msg = str(exc.value)
    assert "rate limit" in msg and "5000/hr" in msg
    assert "authenticated" in msg
    assert "GITHUB_TOKEN" not in msg  # already have one; don't tell them to set it


@responses.activate
def test_secondary_rate_limit_retry_after_gives_hint(tmp_path, monkeypatch):
    # Secondary limit: 403 with Retry-After but no X-RateLimit-Remaining marker.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    responses.add(
        responses.GET,
        "https://api.github.com/repos/osism/acs/contents/roles?ref=main",
        status=403,
        headers={"Retry-After": "42"},
    )
    cfg = _cfg(tmp_path)
    with pytest.raises(SourceError, match="Retry after 42s"):
        list_dir("acs", "roles", cfg)


@responses.activate
def test_plain_403_is_not_mistaken_for_rate_limiting(tmp_path):
    # A 403 without any rate-limit marker (e.g. a permission refusal) must stay a
    # plain HTTP error with no misleading rate-limit hint.
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/release/main/latest/base.yml",
        status=403,
    )
    cfg = _cfg(tmp_path)
    with pytest.raises(SourceError) as exc:
        read("release", "latest/base.yml", cfg)
    msg = str(exc.value)
    assert "HTTP 403" in msg
    assert "rate limit" not in msg


def test_auth_headers_adds_bearer_for_github_host(monkeypatch):
    from osism_drift import source

    monkeypatch.setenv("GITHUB_TOKEN", "secrettoken")
    h = source._auth_headers("https://api.github.com/repos/x", {"Accept": "x"})
    assert h["Authorization"] == "Bearer secrettoken"
    assert h["Accept"] == "x"


def test_auth_headers_absent_when_no_token(monkeypatch):
    from osism_drift import source

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert "Authorization" not in source._auth_headers("https://api.github.com/x")


def test_auth_headers_not_leaked_to_non_github_host(monkeypatch):
    # A token in the environment must NOT be attached when github_raw/github_api
    # have been pointed at a mirror/proxy/test server (non-GitHub host).
    from osism_drift import source

    monkeypatch.setenv("GITHUB_TOKEN", "secrettoken")
    assert "Authorization" not in source._auth_headers("https://mirror.internal/x")
    assert "Authorization" not in source._auth_headers("http://localhost:8080/x")


@responses.activate
def test_get_sends_auth_header_to_github(monkeypatch):
    from osism_drift import source
    from osism_drift.config import Config, Remote

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    responses.add(
        responses.GET,
        "https://raw.githubusercontent.com/osism/release/main/x.yml",
        body=b"k: v",
        status=200,
    )
    cfg = Config(
        remote=Remote(
            "https://raw.githubusercontent.com/",
            "https://api.github.com/repos/",
            "main",
            "osism",
        ),
        release_version="latest",
        plugins={},
    )
    source.read("release", "x.yml", cfg)
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_get_does_not_send_auth_to_non_github_mirror(monkeypatch):
    from osism_drift import source
    from osism_drift.config import Config, Remote

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    responses.add(
        responses.GET,
        "https://mirror.internal/osism/release/main/x.yml",
        body=b"k: v",
        status=200,
    )
    cfg = Config(
        remote=Remote(
            "https://mirror.internal/",
            "https://mirror.internal/repos/",
            "main",
            "osism",
        ),
        release_version="latest",
        plugins={},
    )
    source.read("release", "x.yml", cfg)
    assert "Authorization" not in responses.calls[0].request.headers
