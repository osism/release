import pytest

import release_tags

DATE = "2026-10-08T12:00:00+00:00"
FILES = {
    "latest/openstack-2026.1.yml": "openstack_version: '2026.1'\n",
    "latest/openstack.yml": ("symlink", "openstack-2026.1.yml"),
}
TAG = "kolla-v0.20261008.0"


@pytest.fixture(autouse=True)
def tags_root(release_repo, monkeypatch):
    monkeypatch.setattr(release_tags, "REPO_ROOT", release_repo.checkout)


def test_lightweight_tag(release_repo):
    commit = release_repo.commit(FILES, date=DATE)
    release_repo.tag(TAG)
    found, committed = release_tags.release_commit(TAG)
    assert found == commit
    assert committed.isoformat() == DATE


def test_annotated_tag_is_peeled(release_repo):
    commit = release_repo.commit(FILES)
    release_repo.tag(TAG, annotated=True)
    assert release_tags.remote_commit(TAG) == commit
    assert release_tags.release_commit(TAG)[0] == commit


def test_tag_moved_on_origin(release_repo):
    first = release_repo.commit(FILES)
    release_repo.tag(TAG)
    second = release_repo.commit({"latest/other.yml": "other: 1\n"})
    release_repo.move_upstream_tag(TAG, second)
    with pytest.raises(release_tags.ReleaseError) as error:
        release_tags.release_commit(TAG)
    assert str(error.value) == (
        f"the local tag {TAG} points to {first[:7]}, origin to {second[:7]} "
        "(git fetch --tags --force)"
    )


def test_tag_missing_on_origin(release_repo):
    release_repo.commit(FILES)
    release_repo.tag(TAG, push=False)
    with pytest.raises(release_tags.ReleaseError, match="does not exist on origin"):
        release_tags.release_commit(TAG)


def test_tag_missing_locally(release_repo):
    release_repo.commit(FILES)
    release_repo.tag(TAG)
    release_repo.delete_local_tag(TAG)
    with pytest.raises(
        release_tags.ReleaseError, match="does not exist in this repository"
    ):
        release_tags.release_commit(TAG)


def test_origin_unreachable(release_repo, tmp_path):
    release_repo.commit(FILES)
    release_repo.tag(TAG)
    release_repo.set_origin(tmp_path / "gone.git")
    with pytest.raises(
        release_tags.ReleaseError, match="could not be looked up on origin"
    ) as error:
        release_tags.release_commit(TAG)
    # the reason that git gives follows the colon
    assert str(error.value).split(": ", 1)[1]


def test_release_series_follows_the_symlink(release_repo):
    release_repo.commit(FILES)
    release_repo.tag(TAG)
    assert (
        release_tags.release_series(TAG, ["openstack.yml"], "openstack_version")
        == "2026.1"
    )
    with pytest.raises(release_tags.ReleaseError):
        release_tags.release_series(TAG, ["missing.yml"], "openstack_version")
