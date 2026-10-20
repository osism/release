import importlib.util
import pathlib
import subprocess

# release-notes.py is hyphenated -> not importable by name; load it by path.
_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "release-notes.py"
_spec = importlib.util.spec_from_file_location("release_notes", _SRC)
rn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rn)

NOTES = "releasenotes/notes"

PROXYSQL = """---
fixes:
  - |
    To fix, users are now required to create a file named
    ``proxysql-ca.pem`` at ``certificates_internal_dir``.
"""


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo, files, message):
    for path, content in files.items():
        target = repo / path
        if content is None:
            _git(repo, "rm", "-q", path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        _git(repo, "add", path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path):
    repo = tmp_path / "kolla-ansible"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def test_a_note_corrected_in_the_range_is_modified_not_new(tmp_path):
    # OSISM 11.0.2: the ProxySQL note was released in July and only its
    # file location was corrected in the range
    repo = _repo(tmp_path)
    _commit(repo, {f"{NOTES}/fix-proxysql.yaml": PROXYSQL}, "Split CA of ProxySQL")
    base = _commit(repo, {"README": "x"}, "unrelated")
    head = _commit(
        repo,
        {
            f"{NOTES}/fix-proxysql.yaml": PROXYSQL.replace(
                "certificates_internal_dir", "kolla_certificates_dir"
            ),
            f"{NOTES}/cloudkitty-timeouts.yaml": "---\nfeatures:\n  - New.\n",
        },
        "Fix proxysql-ca.pem location in release note",
    )

    notes = rn.collect_release_notes(repo, base, head)

    assert notes[0] == (
        "added",
        f"{NOTES}/cloudkitty-timeouts.yaml",
        "---\nfeatures:\n  - New.",
    )
    kind, path, text = notes[1]
    assert (kind, path) == ("modified", f"{NOTES}/fix-proxysql.yaml")
    assert text.startswith("@@")
    assert "-    ``proxysql-ca.pem`` at ``certificates_internal_dir``." in text
    assert "+    ``proxysql-ca.pem`` at ``kolla_certificates_dir``." in text
    assert "To fix, users are now required" in text  # context line
    assert len(notes) == 2


def test_renamed_unchanged_and_deleted_notes_are_skipped(tmp_path):
    repo = _repo(tmp_path)
    base = _commit(
        repo,
        {f"{NOTES}/a.yaml": PROXYSQL, f"{NOTES}/b.yaml": "---\nfixes:\n  - B.\n"},
        "notes",
    )
    _git(repo, "mv", f"{NOTES}/a.yaml", f"{NOTES}/a-renamed.yaml")
    head = _commit(repo, {f"{NOTES}/b.yaml": None}, "rename a, delete b")

    assert rn.collect_release_notes(repo, base, head) == []


def test_release_note_lines_separate_added_and_modified():
    lines = rn.release_note_lines(
        [("modified", f"{NOTES}/m.yaml", "@@ -1 +1 @@\n-a\n+b")]
    )
    text = "\n".join(lines)
    assert "### Upstream release notes (reno) added in this range\n\n(none)" in text
    assert (
        "### Upstream release notes (reno) modified in this range "
        "(released before; only the diff is new)\n\n"
        f"#### {NOTES}/m.yaml\n\n```diff\n@@ -1 +1 @@\n-a\n+b\n```"
    ) in text
