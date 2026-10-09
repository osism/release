"""Shared test config: makes src/ importable, plus repo and config factories."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from osism_drift.config import Config, PluginCfg, Remote, SourceCfg  # noqa: E402


def _git(path, *args):
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path):
    """Factory: git_repo(name, {tag: {path: text}}) -> Path under tmp_path.

    One commit and tag per item, in order; each version replaces the whole
    tree, so the working tree ends up at the last version.
    """

    def make(name, versions):
        path = tmp_path / name
        path.mkdir(parents=True)
        _git(path, "init", "-q", "-b", "main")
        for tag, files in versions.items():
            for entry in path.iterdir():
                if entry.name == ".git":
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            for rel, text in files.items():
                p = path / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
            _git(path, "add", "--all")
            _git(path, "commit", "-q", "--allow-empty", "-m", tag)
            _git(path, "tag", tag)
        return path

    return make


@pytest.fixture
def make_cfg():
    """Factory: make_cfg(base, pinned={repo: branch}, remote_fallback=False)."""

    def make(base, pinned=None, remote_fallback=False):
        return Config(
            remote=Remote("https://x/", "https://y/", "main", "osism"),
            base_dirs=(str(base),),
            remote_fallback=remote_fallback,
            release_version="latest",
            plugins={"defaults_orphan": PluginCfg(enabled=True)},
            sources={r: SourceCfg(branch=b) for r, b in (pinned or {}).items()},
        )

    return make
