#!/usr/bin/env python

import os

import github
from ruamel.yaml import YAML

yaml = YAML(typ="rt")
yaml.default_flow_style = False
yaml.explicit_start = True
yaml.preserve_quotes = True

gh = github.Github()
release = os.environ.get("RELEASE")

# required mapping files

mapping_files = [
    "collections",
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

# base: operations

repository = gh.get_repo(mappings["operations"])
if data["operations_version"] in ["main", "master"]:
    try:
        branch = repository.get_branch(data["operations_version"])
        data["operations_version"] = branch.commit.sha
    except:
        print("branch %s for repository %s not found" % (data["operations_version"], mappings["operations"]))

# base: prepare roles

for role in data["ansible_roles"]:
    if role not in mappings or not mappings[role] or data["ansible_roles"][role] not in ["main", "master"]:
        continue

    repository = gh.get_repo(mappings[role])

    try:
        branch = repository.get_branch(data["ansible_roles"][role])
        data["ansible_roles"][role] = branch.commit.sha
    except:
        print("branch %s for repository %s not found" % (data["ansible_roles"][role], mappings[role]))

# base: prepare collections

for collection in data["ansible_collections"]:
    if collection not in mappings or not mappings[collection] or data["ansible_collections"][collection] not in ["main", "master"]:
        continue

    repository = gh.get_repo(mappings[collection])

    try:
        branch = repository.get_branch(data["ansible_collections"][collection])
        data["ansible_collections"][collection] = branch.commit.sha
    except:
        print("branch %s for repository %s not found" % (data["ansible_collections"][collection], mappings[collection]))

# base: other

data["manager_version"] = release

# save base

with open(f"{release}/base.yml", "w") as fp:
    yaml.dump(data, fp)
