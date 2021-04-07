#!/usr/bin/python3
#
# CI-Script to update latest/openstack-latest.yml with the latest Versions for:
#   - elasticsearch
#   - gnocchi
#
###################################################################################################
import json
import yaml
import urllib.request
import urllib.parse

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


def edit_openstack_latest(latest_elasticsearch_version, latest_gnocchi_version):
    # load
    with open("latest/openstack-latest.yml") as stream:
        try:
            loaded = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)

    # modify
    loaded['infrastructure_projects']['elasticsearch'] = latest_elasticsearch_version
    loaded['openstack_projects']['gnocchi'] = latest_gnocchi_version

    # save
    with open("latest/openstack-latest.yml", 'w') as stream:
        try:
            yaml.dump(loaded, stream, default_flow_style=False, explicit_start=True)
        except yaml.YAMLError as exc:
            print(exc)


###################################################################################################
# Main
###################################################################################################

edit_openstack_latest(get_elasticsearch_latest_tag(), get_gnocchi_latest_tag())
