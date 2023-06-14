#!/usr/bin/env python3

from datetime import datetime
from loguru import logger
from pydriller import Repository
import sys
import tabulate

# FIXME: Get from the YAML files.
REPOSITORIES = {
    "gnocchixyz/gnocchi": "4.5.0",
    "openstack/aodh": "origin/stable/zed",
    "openstack/barbican": "origin/stable/zed",
    "openstack/bifrost": "origin/stable/zed",
    "openstack/ceilometer": "origin/stable/zed",
    "openstack/cinder": "origin/stable/zed",
    "openstack/cloudkitty": "origin/stable/zed",
    "openstack/designate": "origin/stable/zed",
    "openstack/glance": "origin/stable/zed",
    "openstack/heat": "origin/stable/zed",
    "openstack/horizon": "origin/stable/zed",
    "openstack/ironic": "origin/stable/zed",
    "openstack/keystone": "origin/stable/zed",
    "openstack/kuryr": "origin/stable/zed",
    "openstack/magnum": "origin/stable/zed",
    "openstack/manila": "origin/stable/zed",
    "openstack/mistral": "origin/stable/zed",
    "openstack/neutron": "origin/stable/zed",
    "openstack/neutron-vpnaas": "origin/stable/zed",
    "openstack/nova": "origin/stable/zed",
    "openstack/octavia": "origin/stable/zed",
    "openstack/placement": "origin/stable/zed",
    "openstack/senlin": "origin/stable/zed",
    "openstack/skyline-apiserver": "origin/stable/zed",
    "openstack/skyline-console": "origin/stable/zed",
    "openstack/swift": "origin/stable/zed",
    "openstack/trove": "origin/stable/zed",
}
SINCE = "2023-04-07"
TABLEFMT = "github"

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
                    f"[{committer_hash}](https://github.com/{repository}/commit/{committer_hash})",
                ]
            )
            logger.debug(f"{committer_date}: {committer_title}")

        if data:
            fp.write(f"## {repository}\n")
            result = tabulate.tabulate(
                sorted(data), headers=["Date", "Title", "Commit"], tablefmt=TABLEFMT
            )

            fp.write(result)
            fp.write("\n\n")
