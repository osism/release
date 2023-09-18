#!/usr/bin/env python3

from datetime import datetime
from loguru import logger
from pydriller import Repository
import sys
import tabulate

# FIXME: Get from the YAML files.
REPOSITORIES = {
    "openstack/aodh": "origin/stable/2023.1",
    "openstack/barbican": "origin/stable/2023.1",
    "openstack/bifrost": "origin/stable/2023.1",
    "openstack/ceilometer": "origin/stable/2023.1",
    "openstack/cinder": "origin/stable/2023.1",
    "openstack/cloudkitty": "origin/stable/2023.1",
    "openstack/designate": "origin/stable/2023.1",
    "openstack/glance": "origin/stable/2023.1",
    "openstack/heat": "origin/stable/2023.1",
    "openstack/horizon": "origin/stable/2023.1",
    "openstack/ironic": "origin/stable/2023.1",
    "openstack/keystone": "origin/stable/2023.1",
    "openstack/kuryr": "origin/stable/2023.1",
    "openstack/magnum": "origin/stable/2023.1",
    "openstack/manila": "origin/stable/2023.1",
    "openstack/mistral": "origin/stable/2023.1",
    "openstack/neutron": "origin/stable/2023.1",
    "openstack/neutron-vpnaas": "origin/stable/2023.1",
    "openstack/nova": "origin/stable/2023.1",
    "openstack/octavia": "origin/stable/2023.1",
    "openstack/placement": "origin/stable/2023.1",
    "openstack/senlin": "origin/stable/2023.1",
    "openstack/skyline-apiserver": "origin/stable/2023.1",
    "openstack/skyline-console": "origin/stable/2023.1",
    "openstack/swift": "origin/stable/2023.1",
    "openstack/trove": "origin/stable/2023.1",
}
SINCE = "2023-09-02"
TABLEFMT = "rst"

since_dt = datetime.strptime(SINCE, "%Y-%m-%d")

level = "INFO"
log_fmt = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<level>{message}</level>"
)

logger.remove()
logger.add(sys.stderr, format=log_fmt, level=level, colorize=True)

logger.info(f"Starting @ {since_dt}")

with open(f"{SINCE}.md", "w+") as fp:
    for repository, branch in REPOSITORIES.items():
        logger.info(f"Analyzing repository {repository} @ {branch}")

        commits = Repository(
            f"https://github.com/{repository}",
            only_in_branch=branch,
            since=since_dt,
            only_no_merge=True,
        ).traverse_commits()
        data = []

        for commit in commits:
            committer_date = commit.committer_date
            committer_title = commit.msg.partition("\n")[0]
            committer_hash = commit.hash

            data.append(
                [
                    committer_date,
                    committer_title,
                    f"`{committer_hash} <https://github.com/{repository}/commit/{committer_hash}>`_",
                ]
            )
            logger.debug(f"{committer_date}: {committer_title}")

        if data:
            fp.write(f"{repository}\n")
            fp.write("-" * len(repository) + "\n\n")
            result = tabulate.tabulate(
                sorted(data), headers=["Date", "Title", "Commit"], tablefmt=TABLEFMT
            )

            fp.write(result)
            fp.write("\n\n")
