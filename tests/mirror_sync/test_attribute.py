import subprocess

from osism_drift.mirror_sync import attribute


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


def _rev(d):
    return subprocess.run(
        ["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path):
    """A kolla-ansible stand-in whose group_vars change over three commits."""
    d = tmp_path / "kolla-ansible"
    (d / "ansible/group_vars/all").mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    gv = d / "ansible/group_vars/all"
    (gv / "common.yml").write_text('om_rabbitmq_qos_prefetch_count: "1"\n')
    (gv / "rabbitmq.yml").write_text("rabbitmq_monitoring_user: ''\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    lo = _rev(d)
    (gv / "common.yml").write_text('om_rabbitmq_qos_prefetch_count: "50"\n')
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "rabbitmq: Bump prefetch 1 to 50")
    (gv / "rabbitmq.yml").write_text("rabbitmq_monitoring_user: 'openstack'\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "haproxy: fix missing user in healthcheck")
    hi = _rev(d)
    return d, lo, hi


def _local(monkeypatch, d):
    monkeypatch.setattr(attribute.source, "local_checkout", lambda repo, cfg: d)


def test_finds_the_commit_that_changed_the_key(tmp_path, monkeypatch):
    d, lo, hi = _repo(tmp_path)
    _local(monkeypatch, d)
    lines, note = attribute.commits_for(
        None, "om_rabbitmq_qos_prefetch_count", ["common.yml"], lo, hi
    )
    assert note == ""
    assert any("Bump prefetch" in line for line in lines)
    assert not any("healthcheck" in line for line in lines)


def test_searches_both_old_and_new_homes(tmp_path, monkeypatch):
    d, lo, hi = _repo(tmp_path)
    _local(monkeypatch, d)
    # A key that moved files: the union must cover both, since searching only the
    # post-sync home can miss the commit that made the change.
    lines, _ = attribute.commits_for(
        None, "rabbitmq_monitoring_user", ["common.yml", "rabbitmq.yml"], lo, hi
    )
    assert any("healthcheck" in line for line in lines)


def test_a_missing_home_does_not_break_the_union(tmp_path, monkeypatch):
    d, lo, hi = _repo(tmp_path)
    _local(monkeypatch, d)
    lines, _ = attribute.commits_for(
        None, "om_rabbitmq_qos_prefetch_count", ["gone.yml", "common.yml"], lo, hi
    )
    assert any("Bump prefetch" in line for line in lines)


def test_key_is_regex_escaped(tmp_path, monkeypatch):
    d, lo, hi = _repo(tmp_path)
    _local(monkeypatch, d)
    # git log -G takes a PATTERN; unescaped, "a.*b" would match unrelated diffs.
    lines, note = attribute.commits_for(None, "a.*b", ["common.yml"], lo, hi)
    assert lines == ()


def test_range_is_bounded_below(tmp_path, monkeypatch):
    d, lo, hi = _repo(tmp_path)
    _local(monkeypatch, d)
    # lo..hi excludes the base commit that first introduced the key, so an
    # unbounded search would report a commit predating this transition.
    lines, _ = attribute.commits_for(
        None, "om_rabbitmq_qos_prefetch_count", ["common.yml"], lo, hi
    )
    assert not any("base" in line for line in lines)


def test_cap_is_globally_newest_first_across_homes(tmp_path, monkeypatch):
    """The cap must respect global order, not per-home order.

    Running one log per home and concatenating yields the first home's two newest
    commits and drops a newer commit in the second home -- while the note claims
    to show the newest two. Verified against real git: concatenated gives
    (a2, a1); one invocation gives (b2, a2).
    """
    d = tmp_path / "kolla-ansible"
    gv = d / "ansible/group_vars/all"
    gv.mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (gv / "a.yml").write_text("churn: 0\n")
    (gv / "b.yml").write_text("churn: 0\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    lo = _rev(d)
    for i in (1, 2):
        (gv / "a.yml").write_text(f"churn: a{i}\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", f"a change {i}")
        (gv / "b.yml").write_text(f"churn: b{i}\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", f"b change {i}")
    hi = _rev(d)
    _local(monkeypatch, d)

    lines, note = attribute.commits_for(
        None, "churn", ["a.yml", "b.yml"], lo, hi, cap=2
    )
    assert "truncated" in note
    subjects = [line.split(" ", 1)[1] for line in lines]
    assert subjects == ["b change 2", "a change 2"]


def test_a_commit_touching_both_homes_is_not_listed_twice(tmp_path, monkeypatch):
    d = tmp_path / "kolla-ansible"
    gv = d / "ansible/group_vars/all"
    gv.mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (gv / "a.yml").write_text("churn: 0\n")
    (gv / "b.yml").write_text("churn: 0\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    lo = _rev(d)
    (gv / "a.yml").write_text("churn: 1\n")
    (gv / "b.yml").write_text("churn: 1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "both at once")
    hi = _rev(d)
    _local(monkeypatch, d)
    lines, _ = attribute.commits_for(None, "churn", ["a.yml", "b.yml"], lo, hi)
    assert len(lines) == 1


def test_a_git_failure_is_reported_not_silently_empty(tmp_path, monkeypatch):
    # An empty list with no note reads as "nothing changed this key", which is a
    # different answer from "attribution could not be computed".
    d, lo, hi = _repo(tmp_path)
    _local(monkeypatch, d)
    lines, note = attribute.commits_for(
        None, "om_rabbitmq_qos_prefetch_count", ["common.yml"], "not-a-ref", hi
    )
    assert lines == ()
    assert "attribution failed" in note


def test_no_known_home_is_reported_not_silently_empty(tmp_path, monkeypatch):
    d, lo, hi = _repo(tmp_path)
    _local(monkeypatch, d)
    lines, note = attribute.commits_for(None, "k", [], lo, hi)
    assert lines == ()
    assert "no upstream path" in note


def test_remote_only_run_skips_with_a_stated_reason(monkeypatch):
    monkeypatch.setattr(attribute.source, "local_checkout", lambda repo, cfg: None)
    lines, note = attribute.commits_for(None, "k", ["common.yml"], "a" * 40, "b" * 40)
    assert lines == ()
    assert "local checkout" in note


def test_missing_range_skips_with_a_stated_reason(tmp_path, monkeypatch):
    d, _, hi = _repo(tmp_path)
    _local(monkeypatch, d)
    lines, note = attribute.commits_for(None, "k", ["common.yml"], None, hi)
    assert lines == ()
    assert "range" in note


def test_results_are_capped_and_say_so(tmp_path, monkeypatch):
    d = tmp_path / "kolla-ansible"
    (d / "ansible/group_vars/all").mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    gv = d / "ansible/group_vars/all"
    (gv / "common.yml").write_text("churn: 0\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    lo = _rev(d)
    for i in range(1, 8):
        (gv / "common.yml").write_text(f"churn: {i}\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", f"change {i}")
    hi = _rev(d)
    _local(monkeypatch, d)
    lines, note = attribute.commits_for(None, "churn", ["common.yml"], lo, hi, cap=3)
    assert len(lines) == 3
    assert "truncated" in note
