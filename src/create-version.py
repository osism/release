#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "pyyaml",
# ]
# ///
#
# Helper for scripts/create-version.sh: freezes the current latest/ state
# into a named release directory. Must be run from the repository root
# (reads latest/base.yml and latest/openstack.yml of the checkout).
#
# The versions of the core container images are the highest versions of
# their tags <project>-v<version> on origin, not in the local checkout: a
# checkout can lack a newer tag or still have a moved tag at its old commit.

import argparse
import os
import re
import shutil
import subprocess
import sys
import yaml

# The date-based versions of the tags, e.g. 0.20261008.0
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")


def remote_tag_names():
    """The names of all tags on origin; exits if they cannot be listed."""
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", "origin"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error: Could not list the tags on origin: {result.stderr.strip()}")
        sys.exit(1)
    return [
        line.split("\t", 1)[1][len("refs/tags/") :]
        for line in result.stdout.splitlines()
    ]


def version_key(version):
    """0.20261008.10 sorts after 0.20261008.9"""
    return tuple(int(part) for part in version.split("."))


def get_latest_tag_version(tag_names, prefix):
    """The highest version of the tags <prefix><version>, None if there is none"""
    versions = [
        name[len(prefix) :]
        for name in tag_names
        if name.startswith(prefix) and VERSION_PATTERN.fullmatch(name[len(prefix) :])
    ]
    return max(versions, key=version_key) if versions else None


def get_latest_osism_ansible(tag_names):
    """Get the latest osism-ansible tag version from origin"""
    version = get_latest_tag_version(tag_names, "osism-ansible-v")
    return version if version else "FIXME"


def get_latest_osism_kubernetes(tag_names):
    """Get the latest osism-kubernetes tag version from origin"""
    version = get_latest_tag_version(tag_names, "osism-kubernetes-v")
    return version if version else "FIXME"


def get_latest_inventory_reconciler(tag_names):
    """Get the latest inventory-reconciler tag version from origin"""
    version = get_latest_tag_version(tag_names, "inventory-reconciler-v")
    return version if version else "FIXME"


def get_latest_kolla_ansible(tag_names):
    """Get the latest kolla-ansible tag version from origin"""
    version = get_latest_tag_version(tag_names, "kolla-ansible-v")
    return version if version else "FIXME"


def get_latest_ceph_ansible(tag_names):
    """Get the latest ceph-ansible tag version from origin"""
    version = get_latest_tag_version(tag_names, "ceph-ansible-v")
    return version if version else "FIXME"


def get_latest_kolla(tag_names):
    """Get the latest kolla tag version from origin"""
    version = get_latest_tag_version(tag_names, "kolla-v")
    return version if version else "FIXME"


def get_openstackclient_version(openstack_file):
    """Get the openstackclient version of the default OpenStack release"""
    with open(openstack_file, "r") as f:
        data = yaml.safe_load(f)
    return data["docker_images"]["openstackclient"]


def process_base_yaml(
    input_file,
    output_file,
    version,
    osism_ansible,
    osism_kubernetes,
    inventory_reconciler,
    kolla_ansible,
    ceph_ansible,
    kolla,
    openstackclient,
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
    data["docker_images"]["ceph_ansible"] = ceph_ansible
    data["docker_images"]["kolla"] = kolla
    data["docker_images"]["openstackclient"] = openstackclient

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
    openstack_file = "latest/openstack.yml"
    dest_file = os.path.join(version_dir, "base.yml")

    # Check if source file exists
    if not os.path.exists(source_file):
        print(f"Error: Source file {source_file} not found")
        sys.exit(1)

    # Check if version directory already exists
    if os.path.exists(version_dir):
        print(f"Error: Directory {version_dir} already exists")
        sys.exit(1)

    # List the tags before anything is written, so that a failed lookup
    # leaves no version directory behind
    tag_names = remote_tag_names()

    # Create version directory
    try:
        os.makedirs(version_dir)
        print(f"Created directory: {version_dir}")
    except Exception as e:
        print(f"Error creating directory: {e}")
        sys.exit(1)

    # Get latest osism-ansible version from the tags on origin
    osism_ansible = get_latest_osism_ansible(tag_names)
    if osism_ansible == "FIXME":
        print("Warning: Could not find osism-ansible version tag, using FIXME")

    # Get latest osism-kubernetes version from the tags on origin
    osism_kubernetes = get_latest_osism_kubernetes(tag_names)
    if osism_kubernetes == "FIXME":
        print("Warning: Could not find osism-kubernetes version tag, using FIXME")

    # Get latest inventory-reconciler version from the tags on origin
    inventory_reconciler = get_latest_inventory_reconciler(tag_names)
    if inventory_reconciler == "FIXME":
        print("Warning: Could not find inventory-reconciler version tag, using FIXME")

    # Get latest kolla-ansible version from the tags on origin
    kolla_ansible = get_latest_kolla_ansible(tag_names)
    if kolla_ansible == "FIXME":
        print("Warning: Could not find kolla-ansible version tag, using FIXME")

    # Get latest ceph-ansible version from the tags on origin
    ceph_ansible = get_latest_ceph_ansible(tag_names)
    if ceph_ansible == "FIXME":
        print("Warning: Could not find ceph-ansible version tag, using FIXME")

    # Get latest kolla version from the tags on origin
    kolla = get_latest_kolla(tag_names)
    if kolla == "FIXME":
        print("Warning: Could not find kolla version tag, using FIXME")

    # Copy and process base.yml
    try:
        # Get openstackclient version of the default OpenStack release
        openstackclient = get_openstackclient_version(openstack_file)

        process_base_yaml(
            source_file,
            dest_file,
            version_dir,
            osism_ansible,
            osism_kubernetes,
            inventory_reconciler,
            kolla_ansible,
            ceph_ansible,
            kolla,
            openstackclient,
        )
        print(f"Copied {source_file} to {dest_file}")
        print(f"  - manager_version: {version_dir}")
        print(f"  - docker_images.osism_ansible: {osism_ansible}")
        print(f"  - docker_images.osism_kubernetes: {osism_kubernetes}")
        print(f"  - docker_images.inventory_reconciler: {inventory_reconciler}")
        print(f"  - docker_images.kolla_ansible: {kolla_ansible}")
        print(f"  - docker_images.ceph_ansible: {ceph_ansible}")
        print(f"  - docker_images.kolla: {kolla}")
        print(f"  - docker_images.openstackclient: {openstackclient}")
    except Exception as e:
        print(f"Error processing file: {e}")
        # Clean up created directory on error
        shutil.rmtree(version_dir)
        sys.exit(1)


if __name__ == "__main__":
    main()
