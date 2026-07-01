"""Synthetic drift entries for the ``--demo`` report preview.

Builds a few illustrative DriftEntry objects per plugin so a user can see what
a complete report looks like without reading any external repo. Each entry
carries the plugin's own live SUMMARY/REMEDIATION text, so wording changes made
in a plugin module are reflected in the demo automatically.
"""

from osism_drift.model import DriftEntry

# Three fixed sample images; deterministic so demo output and tests are stable.
_SAMPLES = [
    ("keystone", "1.2.3", "1.2.2"),
    ("nova", "2024.1", "2023.2"),
    ("glance", "9.0.1", "9.0.0"),
]


def build_demo_drifts(plugins) -> list:
    """Return synthetic actionable DriftEntry objects, three per plugin."""
    drifts = []
    for plugin in plugins:
        first_repo, first_path = plugin.INPUT_FILES[0]
        last_repo, last_path = plugin.INPUT_FILES[-1]
        expected_src = f"{first_repo}/{first_path}"
        found_src = f"{last_repo}/{last_path}"
        show_values = getattr(plugin, "SHOW_VALUES", False)
        for i, (name, expected, found) in enumerate(_SAMPLES):
            # For SHOW_VALUES plugins, blank one entry's expected so the demo
            # also shows the "(found, no release pin)" render branch.
            if show_values and i == len(_SAMPLES) - 1:
                expected = ""
            drifts.append(
                DriftEntry(
                    plugin=plugin.NAME,
                    image=name,
                    alias=name,
                    expected=expected,
                    found=found,
                    expected_src=expected_src,
                    found_src=found_src,
                    summary=plugin.SUMMARY,
                    remediation=plugin.REMEDIATION,
                )
            )
    return drifts
