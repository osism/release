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


def get_latest_osism_ansible_version():
    """Get the latest osism-ansible tag version from git"""
    version = get_latest_tag_version("osism-ansible-v")
    return version if version else "FIXME"


def get_latest_osism_kubernetes_version():
    """Get the latest osism-kubernetes tag version from git"""
    version = get_latest_tag_version("osism-kubernetes-v")
    return version if version else "FIXME"


def get_latest_inventory_reconciler_version():
    """Get the latest inventory-reconciler tag version from git"""
    version = get_latest_tag_version("inventory-reconciler-v")
    return version if version else "FIXME"


def get_latest_kolla_ansible_version():
    """Get the latest kolla-ansible tag version from git"""
    version = get_latest_tag_version("kolla-ansible-v")
    return version if version else "FIXME"


def get_latest_kolla_version():
    """Get the latest kolla tag version from git"""
    version = get_latest_tag_version("kolla-v")
    return version if version else "FIXME"


def process_base_yaml(
    input_file,
    output_file,
    version,
    osism_ansible_version,
    osism_kubernetes_version,
    inventory_reconciler_version,
    kolla_ansible_version,
    kolla_version,
):
    """Process base.yml: remove comments and update versions"""
    with open(input_file, "r") as f:
        data = yaml.safe_load(f)

    # Replace manager_version with the provided version
    data["manager_version"] = version

    # Add or update osism_ansible_version (always set, uses FIXME as fallback)
    data["osism_ansible_version"] = osism_ansible_version

    # Add or update osism_kubernetes_version (always set, uses FIXME as fallback)
    data["osism_kubernetes_version"] = osism_kubernetes_version

    # Add or update inventory_reconciler_version (always set, uses FIXME as fallback)
    data["inventory_reconciler_version"] = inventory_reconciler_version

    # Add or update kolla_ansible_version (always set, uses FIXME as fallback)
    data["kolla_ansible_version"] = kolla_ansible_version

    # Add or update kolla_version (always set, uses FIXME as fallback)
    data["kolla_version"] = kolla_version

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
    osism_ansible_version = get_latest_osism_ansible_version()
    if osism_ansible_version == "FIXME":
        print("Warning: Could not find osism-ansible version tag, using FIXME")

    # Get latest osism-kubernetes version from git tags
    osism_kubernetes_version = get_latest_osism_kubernetes_version()
    if osism_kubernetes_version == "FIXME":
        print("Warning: Could not find osism-kubernetes version tag, using FIXME")

    # Get latest inventory-reconciler version from git tags
    inventory_reconciler_version = get_latest_inventory_reconciler_version()
    if inventory_reconciler_version == "FIXME":
        print("Warning: Could not find inventory-reconciler version tag, using FIXME")

    # Get latest kolla-ansible version from git tags
    kolla_ansible_version = get_latest_kolla_ansible_version()
    if kolla_ansible_version == "FIXME":
        print("Warning: Could not find kolla-ansible version tag, using FIXME")

    # Get latest kolla version from git tags
    kolla_version = get_latest_kolla_version()
    if kolla_version == "FIXME":
        print("Warning: Could not find kolla version tag, using FIXME")

    # Copy and process base.yml
    try:
        process_base_yaml(
            source_file,
            dest_file,
            version_dir,
            osism_ansible_version,
            osism_kubernetes_version,
            inventory_reconciler_version,
            kolla_ansible_version,
            kolla_version,
        )
        print(f"Copied {source_file} to {dest_file}")
        print(f"  - manager_version: {version_dir}")
        print(f"  - osism_ansible_version: {osism_ansible_version}")
        print(f"  - osism_kubernetes_version: {osism_kubernetes_version}")
        print(f"  - inventory_reconciler_version: {inventory_reconciler_version}")
        print(f"  - kolla_ansible_version: {kolla_ansible_version}")
        print(f"  - kolla_version: {kolla_version}")
    except Exception as e:
        print(f"Error processing file: {e}")
        # Clean up created directory on error
        shutil.rmtree(version_dir)
        sys.exit(1)


if __name__ == "__main__":
    main()
