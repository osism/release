"""Plugin protocol for drift checks."""

from typing import Protocol


class Plugin(Protocol):  # pylint: disable=too-few-public-methods  # interface
    """Structural type a drift-check plugin module must satisfy.

    One attribute is optional and so not declared below: EXTERNAL_HOSTS, a tuple
    of hosts the plugin reads that are not OSISM repos and that no --base-dir can
    stand in for. Declaring it lets the driver refuse a local-only run up front
    (see driver._network_blocked) instead of the plugin quietly reaching the
    network mid-run. Plugins that only read repos omit it.
    """

    NAME: str
    DESCRIPTION: str
    INPUT_FILES: list  # list of (repo, rel_path) tuples

    @staticmethod
    def run(config, allowlist, verbose: bool = False) -> list:
        """Return list[DriftEntry] for all drift this plugin finds.

        verbose=True authorizes plugins to emit advisory messages to
        stderr (e.g. unresolved Jinja warnings).
        """
