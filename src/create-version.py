#!/usr/bin/env python3

import argparse
import os
import re
import shutil
import sys
import yaml
from git import Repo


def get_latest_tag_version(prefix):
    """Get the latest tag version from git for a given prefix"""
    repo = Repo(".")

    # Get all tags that match the pattern prefix*
    matching_tags = []
    for tag in repo.tags:
        if tag.name.startswith(prefix):
            matching_tags.append(tag)

    if not matching_tags:
        return None

    # Sort tags by commit date to get the most recent
    latest_tag = sorted(matching_tags, key=lambda t: t.commit.committed_datetime)[-1]

    # Extract version from tag name (prefix + version)
    match = re.match(rf"{re.escape(prefix)}(.+)", latest_tag.name)
    if match:
        return match.group(1)
    return None


def get_latest_osism_ansible():
    """Get the latest osism-ansible tag version from git"""
    version = get_latest_tag_version("osism-ansible-v")
    return version if version else "FIXME"


def get_latest_osism_kubernetes():
    """Get the latest osism-kubernetes tag version from git"""
    version = get_latest_tag_version("osism-kubernetes-v")
    return version if version else "FIXME"


def get_latest_inventory_reconciler():
    """Get the latest inventory-reconciler tag version from git"""
    version = get_latest_tag_version("inventory-reconciler-v")
    return version if version else "FIXME"


def get_latest_kolla_ansible():
    """Get the latest kolla-ansible tag version from git"""
    version = get_latest_tag_version("kolla-ansible-v")
    return version if version else "FIXME"


def process_base_yaml(
    input_file,
    output_file,
    version,
    osism_ansible,
    osism_kubernetes,
    inventory_reconciler,
    kolla_ansible,
):
    """Process base.yml: remove comments and update versions"""
    with open(input_file, "r") as f:
        data = yaml.safe_load(f)

    # Replace manager_version with the provided version
    data["manager_version"] = version

    # Ensure docker_images section exists
    if "docker_images" not in data:
        data["docker_images"] = {}

    # Add or update versions in docker_images section (always set, uses FIXME as fallback)
    data["docker_images"]["osism_ansible"] = osism_ansible
    data["docker_images"]["osism_kubernetes"] = osism_kubernetes
    data["docker_images"]["inventory_reconciler"] = inventory_reconciler
    data["docker_images"]["kolla_ansible"] = kolla_ansible

    with open(output_file, "w") as f:
        yaml.dump(
            data, f, default_flow_style=False, sort_keys=False, explicit_start=True
        )


def main():
    parser = argparse.ArgumentParser(
        description="Create a new version directory and copy base.yml without comments"
    )
    parser.add_argument("version", help="Version directory name to create")
    args = parser.parse_args()

    version_dir = args.version
    source_file = "latest/base.yml"
    dest_file = os.path.join(version_dir, "base.yml")

    # Check if source file exists
    if not os.path.exists(source_file):
        print(f"Error: Source file {source_file} not found")
        sys.exit(1)

    # Check if version directory already exists
    if os.path.exists(version_dir):
        print(f"Error: Directory {version_dir} already exists")
        sys.exit(1)

    # Create version directory
    try:
        os.makedirs(version_dir)
        print(f"Created directory: {version_dir}")
    except Exception as e:
        print(f"Error creating directory: {e}")
        sys.exit(1)

    # Get latest osism-ansible version from git tags
    osism_ansible = get_latest_osism_ansible()
    if osism_ansible == "FIXME":
        print("Warning: Could not find osism-ansible version tag, using FIXME")

    # Get latest osism-kubernetes version from git tags
    osism_kubernetes = get_latest_osism_kubernetes()
    if osism_kubernetes == "FIXME":
        print("Warning: Could not find osism-kubernetes version tag, using FIXME")

    # Get latest inventory-reconciler version from git tags
    inventory_reconciler = get_latest_inventory_reconciler()
    if inventory_reconciler == "FIXME":
        print("Warning: Could not find inventory-reconciler version tag, using FIXME")

    # Get latest kolla-ansible version from git tags
    kolla_ansible = get_latest_kolla_ansible()
    if kolla_ansible == "FIXME":
        print("Warning: Could not find kolla-ansible version tag, using FIXME")

    # Copy and process base.yml
    try:
        process_base_yaml(
            source_file,
            dest_file,
            version_dir,
            osism_ansible,
            osism_kubernetes,
            inventory_reconciler,
            kolla_ansible,
        )
        print(f"Copied {source_file} to {dest_file}")
        print(f"  - manager_version: {version_dir}")
        print(f"  - docker_images.osism_ansible: {osism_ansible}")
        print(f"  - docker_images.osism_kubernetes: {osism_kubernetes}")
        print(f"  - docker_images.inventory_reconciler: {inventory_reconciler}")
        print(f"  - docker_images.kolla_ansible: {kolla_ansible}")
    except Exception as e:
        print(f"Error processing file: {e}")
        # Clean up created directory on error
        shutil.rmtree(version_dir)
        sys.exit(1)


if __name__ == "__main__":
    main()
