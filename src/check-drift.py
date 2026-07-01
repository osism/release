#!/usr/bin/env python3
"""Unified drift detector for OSISM image and kolla checks."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from osism_drift import driver  # noqa: E402
from osism_drift.drift import PLUGIN_GROUPS, REPORT_HEADERS  # noqa: E402

here = Path(__file__).resolve().parent


def main(argv=None):
    return driver.run(
        argv if argv is not None else sys.argv[1:],
        description="Check OSISM image + kolla drift across repos.",
        default_config=here / "drift-config.yml",
        default_allowlist=here / "drift-allowlist.yml",
        plugin_groups=PLUGIN_GROUPS,
        report_headers=REPORT_HEADERS,
    )


if __name__ == "__main__":
    sys.exit(main())
