# SPDX-License-Identifier: Apache-2.0

import os

import jinja2
import yaml

# get environment parameters

VERSION = os.environ.get("VERSION", "latest")
OPENSTACK_VERSION = os.environ.get("OPENSTACK_VERSION", "latest")

# load versions files from release repository

with open("/release/latest/base.yml", "rb") as fp:
    versions = yaml.load(fp, Loader=yaml.FullLoader)

# prepare jinja2 environment

loader = jinja2.FileSystemLoader(searchpath="/src/templates/")
environment = jinja2.Environment(loader=loader)

# render versions.yml

template = environment.get_template("versions.yml.j2")

with open(f"/release/latest/openstack-{OPENSTACK_VERSION}.yml", "rb") as fp:
    versions_openstack = yaml.load(fp, Loader=yaml.FullLoader)

try:
    with open("/sbom.yml") as fp:
        release_versions = yaml.load(fp, Loader=yaml.FullLoader)
except FileNotFoundError:
    release_versions = {"versions": {}}

result = template.render(
    {
        "openstack_version": OPENSTACK_VERSION,
        "openstack_previous_version": versions_openstack["openstack_previous_version"],
        "openstackclient_version": versions_openstack["docker_images"][
            "openstackclient"
        ],
        "versions": release_versions["versions"],
    }
)

with open("/ansible/group_vars/all/versions.yml", "w+") as fp:
    fp.write(result)

# render motd

template = environment.get_template("motd.j2")
result = template.render({"manager_version": versions["manager_version"]})
with open("/etc/motd", "w+") as fp:
    fp.write(result)
