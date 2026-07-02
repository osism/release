"""Plugin protocol for drift checks."""

from typing import Protocol


class Plugin(Protocol):  # pylint: disable=too-few-public-methods  # interface
    """Structural type a drift-check plugin module must satisfy."""

    NAME: str
    DESCRIPTION: str
    INPUT_FILES: list  # list of (repo, rel_path) tuples

    @staticmethod
    def run(config, allowlist, verbose: bool = False) -> list:
        """Return list[DriftEntry] for all drift this plugin finds.

        verbose=True authorizes plugins to emit advisory messages to
        stderr (e.g. unresolved Jinja warnings).
        """
