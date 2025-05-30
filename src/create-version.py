#!/usr/bin/env python3

import argparse
import os
import shutil
import sys
import yaml


def process_base_yaml(input_file, output_file, version):
    """Process base.yml: remove comments and update manager_version"""
    with open(input_file, "r") as f:
        data = yaml.safe_load(f)

    # Replace manager_version with the provided version
    data["manager_version"] = version

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

    # Copy and process base.yml
    try:
        process_base_yaml(source_file, dest_file, version_dir)
        print(
            f"Copied {source_file} to {dest_file} (comments removed, manager_version set to {version_dir})"
        )
    except Exception as e:
        print(f"Error processing file: {e}")
        # Clean up created directory on error
        shutil.rmtree(version_dir)
        sys.exit(1)


if __name__ == "__main__":
    main()
