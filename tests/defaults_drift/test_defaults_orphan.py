"""defaults_orphan: variables osism/defaults sets that nothing reads."""

import pytest

from osism_drift import defaults_corpus as dc, report
from osism_drift.config import AllowEntry, Allowlist
from osism_drift.drift import defaults_orphan as plugin

_DEFAULTS = {
    "all/099-generic.yml": (
        "---\n"
        "dead_var: 1\n"
        "read_by_role: 2\n"
        "read_by_value: 3\n"
        "declared_only: 4\n"
        "commented_only: 5\n"
        'uses_other: "{{ read_by_value }}"\n'
        "dup_var: 6\n"
        "read_by_galaxy_role: 9\n"
    ),
    "all/099-ceph.yml": "dup_var: 7\nold_ceph_var: 8\n",
    "all/001-common.yml": "kolla_mirror_var: 1\n",
    "all/010-2025.1.yml": "kolla_compat_var: 1\n",
    "all/002-images-kolla.yml": "kolla_image_var: 1\n",
    "all/099-kolla.yml": "kolla_opinion_var: 1\n",
    "monitoring/000-defaults.yml": "k3s_add_labels_monitoring:\n  - a=b\n",
    "network/000-defaults.yml": "k3s_add_labels__network:\n  - a=b\n",
    ".github/workflows/x.yml": "github_var: 1\n",
    "CHANGELOG.md": "dead_var was added\n",
}

_CONSUMER = {
    "roles/r/tasks/main.yml": '- debug: msg="{{ read_by_role }}"\n',
    "roles/r/defaults/main.yml": "declared_only: 0\n",
    "roles/r/templates/x.j2": "nothing here\n",
    "playbooks/labels.yml": (
        "x: \"{{ lookup('community.general.merge_variables', '^k3s_add_labels__.+$') }}\"\n"
        "# commented_only\n"
    ),
}


@pytest.fixture
def repos(tmp_path, git_repo, make_cfg, monkeypatch):
    def write(name, files):
        for rel, text in files.items():
            p = tmp_path / name / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    write("defaults", _DEFAULTS)
    write("widget", _CONSUMER)
    git_repo(
        "ceph-ansible",
        {
            "stable-7.0": {"roles/facts/tasks/main.yml": "x: '{{ old_ceph_var }}'\n"},
            "stable-8.0": {"roles/facts/tasks/main.yml": "x: 1\n"},
        },
    )
    srcs = [
        dc.Source("defaults", None),
        dc.Source("widget", None),
        dc.Source("ceph_ansible", "stable-7.0", upstream=True),
        dc.Source("ceph_ansible", "stable-8.0", upstream=True),
        dc.Source("acme/ansible-role-x", "v1", upstream=True, external=True),
    ]
    role = tmp_path / "role-x"
    (role / "tasks").mkdir(parents=True)
    (role / "tasks" / "main.yml").write_text("x: '{{ read_by_galaxy_role }}'\n")
    monkeypatch.setattr(dc.source, "github_tree_dir", lambda o, s, r, c: role)
    monkeypatch.setattr(dc, "sources", lambda config: srcs)
    return make_cfg(tmp_path, pinned={"ceph_ansible": "main"})


def _by(drifts):
    return {(d.image, d.found_src): d for d in drifts}


def test_is_checked():
    assert plugin.is_checked("all/099-generic.yml")
    assert plugin.is_checked("monitoring/000-defaults.yml")
    assert plugin.is_checked("all/002-images-ceph.yaml")
    assert not plugin.is_checked("all/001-common.yml")
    assert not plugin.is_checked("all/010-2025.1.yml")
    assert not plugin.is_checked("all/002-images-kolla.yml")
    assert not plugin.is_checked("all/099-kolla.yml")
    assert not plugin.is_checked(".github/workflows/x.yml")
    assert not plugin.is_checked("all/README.md")
    assert not plugin.is_checked("ansible.cfg")
    assert not plugin.is_checked("contrib/sub/x.yml")


def test_unread_variable_is_flagged_with_its_file(repos):
    d = _by(plugin.run(repos, Allowlist(())))[
        ("dead_var", "defaults/all/099-generic.yml")
    ]
    assert d.severity == "actionable"
    assert d.summary == plugin.SUMMARY
    assert d.remediation == plugin.REMEDIATION


def test_read_variables_are_not_flagged(repos):
    images = {d.image for d in plugin.run(repos, Allowlist(()))}
    assert "read_by_role" not in images
    assert "read_by_value" not in images  # read inside another default's value
    assert "k3s_add_labels__network" not in images  # read by merge pattern


def test_declaration_and_comment_are_not_reads(repos):
    images = {d.image for d in plugin.run(repos, Allowlist(()))}
    assert "declared_only" in images
    assert "commented_only" in images
    assert "uses_other" in images  # nothing reads it


def test_prose_is_not_a_read(repos):
    assert "dead_var" in {d.image for d in plugin.run(repos, Allowlist(()))}


def test_kolla_owned_layers_and_dotfiles_are_not_checked(repos):
    images = {d.image for d in plugin.run(repos, Allowlist(()))}
    for name in (
        "kolla_mirror_var",
        "kolla_compat_var",
        "kolla_image_var",
        "kolla_opinion_var",
        "github_var",
    ):
        assert name not in images


def test_variable_read_only_at_an_older_upstream_ref_is_not_flagged(repos):
    assert "old_ceph_var" not in {d.image for d in plugin.run(repos, Allowlist(()))}


def test_variable_set_in_two_files_gives_two_findings(repos):
    found = {
        src
        for (image, src) in _by(plugin.run(repos, Allowlist(())))
        if image == "dup_var"
    }
    assert found == {"defaults/all/099-generic.yml", "defaults/all/099-ceph.yml"}


def test_near_miss_is_reported_as_a_rename(repos):
    d = _by(plugin.run(repos, Allowlist(())))[
        ("k3s_add_labels_monitoring", "defaults/monitoring/000-defaults.yml")
    ]
    assert d.severity == "actionable"
    assert d.summary != plugin.SUMMARY
    assert "k3s_add_labels__monitoring" in d.remediation
    assert "^k3s_add_labels__.+$" in d.remediation
    assert "widget@main:playbooks/labels.yml" in d.remediation


def test_unresolved_merge_call_makes_every_unread_finding_advisory(repos, tmp_path):
    (tmp_path / "widget" / "roles" / "r" / "tasks" / "dyn.yml").write_text(
        "x: \"{{ lookup('community.general.merge_variables', prefix_var) }}\"\n"
    )
    drifts = plugin.run(repos, Allowlist(()))
    by = _by(drifts)
    call = by[
        ("widget@main:roles/r/tasks/dyn.yml", "widget@main:roles/r/tasks/dyn.yml")
    ]
    # The call itself is something to fix, so it fails the run; the variable
    # findings it makes uncertain stay advisory and carry no deletion advice.
    assert call.severity == "actionable"
    for key in (
        ("dead_var", "defaults/all/099-generic.yml"),
        ("k3s_add_labels_monitoring", "defaults/monitoring/000-defaults.yml"),
    ):
        d = by[key]
        assert d.severity == "advisory"
        assert "widget@main:roles/r/tasks/dyn.yml" in d.remediation
    rendered = "\n".join(
        report.format_text([d for d in drifts if d.image != call.image], [plugin])
    )
    assert "remove" not in rendered.lower()
    assert "rename" not in rendered.lower()


def test_invalid_merge_regex_does_not_crash_the_run(repos, tmp_path):
    (tmp_path / "widget" / "roles" / "r" / "tasks" / "bad.yml").write_text(
        "x: \"{{ lookup('community.general.merge_variables', '^dead_(') }}\"\n"
    )
    by = _by(plugin.run(repos, Allowlist(())))
    assert (
        by[
            ("widget@main:roles/r/tasks/bad.yml", "widget@main:roles/r/tasks/bad.yml")
        ].severity
        == "actionable"
    )
    assert by[("dead_var", "defaults/all/099-generic.yml")].severity == "advisory"


def test_allowlist_marks_and_stale_entry_is_reported(repos):
    allow = Allowlist(
        (
            AllowEntry(
                plugin="defaults_orphan",
                image="dead_var",
                reason="read by some.collection role x",
            ),
            AllowEntry(plugin="defaults_orphan", image="no_such_var", reason="r"),
        )
    )
    drifts = plugin.run(repos, allow)
    assert _by(drifts)[("dead_var", "defaults/all/099-generic.yml")].allowlisted
    assert [e.image for e in allow.stale(drifts, {"defaults_orphan"})] == [
        "no_such_var"
    ]


def test_entries_carry_the_corpus_in_expected_src(repos):
    d = _by(plugin.run(repos, Allowlist(())))[
        ("dead_var", "defaults/all/099-generic.yml")
    ]
    assert d.expected_src == (
        "5 sources: defaults@main, widget@main, ceph-ansible@stable-7.0, "
        "ceph-ansible@stable-8.0, acme/ansible-role-x@v1"
    )


def test_variable_read_only_by_a_galaxy_role_is_not_flagged(repos):
    assert "read_by_galaxy_role" not in {
        d.image for d in plugin.run(repos, Allowlist(()))
    }


def test_external_hosts_declared():
    assert plugin.EXTERNAL_HOSTS == ("api.github.com",)
