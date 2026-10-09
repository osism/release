"""Galaxy role trees: an exact GitHub owner/repo, read remotely at a pin."""

import io
import tarfile

from osism_drift import archive, source


def _targz():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"x: 1\n"
        info = tarfile.TarInfo("owner-repo-abc/tasks/main.yml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_snapshot_dir_slug_is_used_verbatim_in_url_and_memo(
    tmp_path, make_cfg, monkeypatch
):
    urls = []

    def fetch(repo, ref, url):
        urls.append(url)
        return _targz()

    monkeypatch.setattr(archive, "_fetch_archive_bytes", fetch)
    cfg = make_cfg(tmp_path)
    root = archive.snapshot_dir(
        "PowerDNS", "pdns_recursor", "v1.8.1", cfg, slug="pdns_recursor-ansible"
    )
    assert urls == [
        f"{cfg.remote.github_api}PowerDNS/pdns_recursor-ansible/tarball/v1.8.1"
    ]
    assert (root / "tasks" / "main.yml").is_file()
    assert (
        archive.snapshot_dir(
            "PowerDNS", "pdns_recursor", "v1.8.1", cfg, slug="pdns_recursor-ansible"
        )
        == root
    )
    assert len(urls) == 1


def test_snapshot_dir_without_slug_still_hyphenates(tmp_path, make_cfg, monkeypatch):
    urls = []
    monkeypatch.setattr(
        archive,
        "_fetch_archive_bytes",
        lambda r, ref, url: urls.append(url) or _targz(),
    )
    cfg = make_cfg(tmp_path)
    archive.snapshot_dir("osism", "a_b", "main", cfg)
    assert urls == [f"{cfg.remote.github_api}osism/a-b/tarball/main"]


def test_github_tree_dir_passes_the_exact_slug_and_ignores_base_dir(
    tmp_path, make_cfg, monkeypatch
):
    (tmp_path / "ansible-hardening").mkdir()
    (tmp_path / "ansible-hardening" / "local.yml").write_text("x: 1\n")
    cfg = make_cfg(tmp_path)
    calls = []

    def fake(owner, repo, ref, config, slug=None):
        calls.append((owner, repo, ref, config, slug))
        return tmp_path / "remote"

    monkeypatch.setattr(archive, "snapshot_dir", fake)
    got = source.github_tree_dir("openstack", "ansible-hardening", "e77c311", cfg)
    assert got == tmp_path / "remote"
    assert len(calls) == 1
    owner, _repo, ref, config, slug = calls[0]
    assert (owner, ref, config, slug) == (
        "openstack",
        "e77c311",
        cfg,
        "ansible-hardening",
    )
