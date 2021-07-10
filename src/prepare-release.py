#!/usr/bin/env python

import os

import github
from ruamel.yaml import YAML

yaml = YAML(typ="rt")
yaml.default_flow_style = False
yaml.explicit_start = True

gh = github.Github(os.environ.get("GITHUB_TOKEN"))
release = os.environ.get("RELEASE")

# required mapping files

mapping_files = [
    "collections",
    "openstack",
    "other",
    "roles"
]

mappings = {}

for mapping in mapping_files:
    with open(f"etc/{mapping}.yml", "r") as fp:
        mappings = {**yaml.load(fp), **mappings}

# prepare base

with open(f"{release}/base.yml", "r") as fp:
    data = yaml.load(fp)

# base: defaults

repository = gh.get_repo(mappings["defaults"])
try:
    branch = repository.get_branch(data["defaults_version"])
    data["defaults_version"] = branch.commit.sha
except:
    print("branch %s for repository %s not found" % (data["defaults_version"], mappings["defaults"]))

# base: generics

repository = gh.get_repo(mappings["generics"])
try:
    branch = repository.get_branch(data["generics_version"])
    data["generics_version"] = branch.commit.sha
except:
    print("branch %s for repository %s not found" % (data["generics_version"], mappings["generics"]))

# base: playbooks

repository = gh.get_repo(mappings["playbooks"])
try:
    branch = repository.get_branch(data["playbooks_version"])
    data["playbooks_version"] = branch.commit.sha
except:
    print("branch %s for repository %s not found" % (data["playbooks_version"], mappings["playbooks"]))

# base: prepare roles

for role in data["ansible_roles"]:
    if role not in mappings or not mappings[role]:
        continue

    repository = gh.get_repo(mappings[role])

    try:
        branch = repository.get_branch(data["ansible_roles"][role])
        data["ansible_roles"][role] = branch.commit.sha
    except:
        print("branch %s for repository %s not found" % (data["ansible_roles"][role], mappings[role]))

# base: prepare collections

for collection in data["ansible_collections"]:
    if collection not in mappings or not mappings[collection]:
        continue

    repository = gh.get_repo(mappings[collection])

    try:
        branch = repository.get_branch(data["ansible_collections"][collection])
        data["ansible_collections"][collection] = branch.commit.sha
    except:
        print("branch %s for repository %s not found" % (data["ansible_collections"][collection], mappings[collection]))

# base: other

data["repository_version"] = release
data["manager_version"] = release

# save base

with open(f"{release}/base.yml", "w") as fp:
    yaml.dump(data, fp)

# prepare openstack

with open(f"{release}/openstack.yml", "r") as fp:
    data = yaml.load(fp)

# openstack: defaults

repository = gh.get_repo(mappings["defaults"])
try:
    branch = repository.get_branch(data["defaults_version"])
    data["defaults_version"] = branch.commit.sha
except:
    print("branch %s for repository %s not found" % (data["defaults_version"], mappings["defaults"]))

# openstack: generics

repository = gh.get_repo(mappings["generics"])
try:
    branch = repository.get_branch(data["generics_version"])
    data["generics_version"] = branch.commit.sha
except:
    print("branch %s for repository %s not found" % (data["generics_version"], mappings["generics"]))

# save openstack

with open(f"{release}/openstack.yml", "w") as fp:
    yaml.dump(data, fp)

# prepare ceph

with open(f"{release}/ceph.yml", "r") as fp:
    data = yaml.load(fp)

# ceph: defaults

repository = gh.get_repo(mappings["defaults"])
try:
    branch = repository.get_branch(data["defaults_version"])
    data["defaults_version"] = branch.commit.sha
except:
    print("branch %s for repository %s not found" % (data["defaults_version"], mappings["defaults"]))

# ceph: generics

repository = gh.get_repo(mappings["generics"])
try:
    branch = repository.get_branch(data["generics_version"])
    data["generics_version"] = branch.commit.sha
except:
    print("branch %s for repository %s not found" % (data["generics_version"], mappings["generics"]))

# save ceph

with open(f"{release}/ceph.yml", "w") as fp:
    yaml.dump(data, fp)
