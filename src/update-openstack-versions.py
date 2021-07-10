#!/usr/bin/python3
#
# CI-Script to update latest/openstack-*.yml with the latest Versions for:
#   - elasticsearch
#   - gnocchi
#   - kibana
#
###################################################################################################
from collections import OrderedDict
import json
import urllib.parse
import urllib.request

import yaml


###################################################################################################
# Variables
###################################################################################################

api = "https://api.github.com/repos/"


###################################################################################################
# Functions
###################################################################################################

def get_gnocchi_latest_tag():
    with urllib.request.urlopen(api + "gnocchixyz/gnocchi/tags") as url:
        data = json.loads(url.read().decode())
        return data[0]['name']


def get_elasticsearch_latest_tag():
    with urllib.request.urlopen(api + "elastic/elasticsearch/tags?per_page=100", ) as url:
        data = json.loads(url.read().decode())
        for release in data:
            if release['name'].startswith("v6"):
                return release['name'][1:]


def get_kibana_latest_tag():
    with urllib.request.urlopen(api + "elastic/kibana/tags?per_page=100", ) as url:
        data = json.loads(url.read().decode())
        for release in data:
            if release['name'].startswith("v6"):
                return release['name'][1:]


def edit_openstack_latest(latest_elasticsearch_version, latest_gnocchi_version, latest_kibana_version):
    # load
    with open(file) as stream:
        try:
            loaded = OrderedDict()
            loaded = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)

    # modify
    if loaded['infrastructure_projects']['elasticsearch'] is not None:
        loaded['infrastructure_projects']['elasticsearch'] = latest_elasticsearch_version
    if loaded['openstack_projects']['gnocchi'] is not None:
        loaded['openstack_projects']['gnocchi'] = latest_gnocchi_version
    if loaded['infrastructure_projects']['kibana'] is not None:
        loaded['infrastructure_projects']['kibana'] = latest_kibana_version

    # replace null with empty strings:
    for i in loaded:
        if isinstance(loaded[i], dict):
            for j in loaded[i]:
                if loaded[i][j] is None:
                    loaded[i][j] = ""

    # save
    with open(file, 'w') as stream:
        try:
            yaml.dump(loaded, stream, default_flow_style=False, explicit_start=True, sort_keys=False)
        except yaml.YAMLError as exc:
            print(exc)


def restyle_openstack_latest():
    # replace <dummy: ''> with only <dummy:> for better readability

    with open(file, "r") as stream:
        buf = stream.read().replace(" ''", "")
    with open(file, "w") as stream:
        stream.write(buf)

    # insert blank lines for better readability
    with open(file, "r") as stream:
        buf = stream.readlines()
    with open(file, "w") as stream:
        for line in buf:
            if line in ["docker_images:\n", "infrastructure_projects:\n", "openstack_projects:\n"]:
                line = "\n" + line
            stream.write(line)


###################################################################################################
# Main
###################################################################################################

file = "latest/openstack-latest.yml"
edit_openstack_latest(get_elasticsearch_latest_tag(), get_gnocchi_latest_tag(), get_kibana_latest_tag())
restyle_openstack_latest()

file = "latest/openstack-victoria.yml"
edit_openstack_latest(get_elasticsearch_latest_tag(), get_gnocchi_latest_tag(), get_kibana_latest_tag())
restyle_openstack_latest()

file = "latest/openstack-wallaby.yml"
edit_openstack_latest(get_elasticsearch_latest_tag(), get_gnocchi_latest_tag(), get_kibana_latest_tag())
restyle_openstack_latest()
