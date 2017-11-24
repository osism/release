#!/usr/bin/env python

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import subprocess

import dictdiffer
import git
import yaml

OSISM_VERSION = os.environ.get("OSISM_VERSION", "latest")

if OSISM_VERSION == "latest":
    OSISM_VERSION = os.readlink("latest").strip("/")
    #print "latest == %s" % OSISM_VERSION

#print "checking %s" % OSISM_VERSION

repo = git.Repo('.')
commits_list = list(repo.iter_commits())

a_commit = commits_list[0]
b_commit = commits_list[1]

trigger = {
    "docker-ceph-ansible": False,
    "docker-images": False,
    "docker-kolla-ansible": False,
    "docker-kolla-docker": False,
    "docker-osism-ansible": False
}

path = "%s/base.yml" % OSISM_VERSION
if a_commit.diff(b_commit, paths=path):
    #print "%s changed" % path

    a_contents  = repo.git.show('{}:{}'.format(a_commit.hexsha, path))
    b_contents  = repo.git.show('{}:{}'.format(b_commit.hexsha, path))

    a_data = yaml.load(a_contents)
    b_data = yaml.load(b_contents)

    for change in list(dictdiffer.diff(a_data, b_data)):
        _, path, diff = change

        # change in ansible_roles --> rebuild docker-osism-ansible
        if path.startswith("ansible_roles"):
            #print "ansible_roles changed"
            trigger["docker-osism-ansible"] = True

        # osism projects --> rebuild docker-{osism,ceph,kolla}-ansible
        if path.startswith("osism_projects"):
            #print "osism_projects changed"
            trigger["docker-ceph-ansible"] = True
            trigger["docker-kolla-ansible"] = True
            trigger["docker-osism-ansible"] = True

        # change in docker_images --> ignore
        if path.startswith("docker_images"):
            #print "docker_images changed"
            trigger["docker-images"] = True

        # change in repository_version --> rebuild all
        if path.startswith("repository_version"):
            #print "repository_version changed"
            trigger["docker-ceph-ansible"] = True
            trigger["docker-kolla-ansible"] = True
            trigger["docker-kolla-docker"] = True
            trigger["docker-osism-ansible"] = True

path = "%s/openstack-ocata.yml" % OSISM_VERSION
if a_commit.diff(b_commit, paths=path):
    #print "%s changed" % path

    a_contents  = repo.git.show('{}:{}'.format(a_commit.hexsha, path))
    b_contents  = repo.git.show('{}:{}'.format(b_commit.hexsha, path))

    a_data = yaml.load(a_contents)
    b_data = yaml.load(b_contents)

    for change in list(dictdiffer.diff(a_data, b_data)):
        _, path, diff = change

        # change in docker_images --> ignore
        if path.startswith("docker_images"):
            #print "docker_images changed"
            trigger["docker-images"] = True

        # change in oenstack_version else --> rebuild docker-kolla-docker + docker-kolla-ansible
        if path.startswith("openstack_version"):
            #print "openstack_version changed"
            trigger["docker-kolla-ansible"] = True
            trigger["docker-kolla-docker"] = True

        # change in everything else --> rebuild docker-kolla-docker
        for key in ["infrastructure_projects",
                    "openstack_projects",
                    "horizon_plugins",
                    "neutron_base_plugins",
                    "neutron_server_plugins",
                    "integrated_projects"]:
            if path.startswith(key):
                #print "%s changed" % key
                trigger["docker-kolla-docker"] = True

for ceph_version in ["kraken", "luminous"]:
    path = "%s/ceph-%s.yml" % (OSISM_VERSION, ceph_version)
    if a_commit.diff(b_commit, paths=path):
        #print "%s changed" % path

        a_contents  = repo.git.show('{}:{}'.format(a_commit.hexsha, path))
        b_contents  = repo.git.show('{}:{}'.format(b_commit.hexsha, path))

        a_data = yaml.load(a_contents)
        b_data = yaml.load(b_contents)

        for change in list(dictdiffer.diff(a_data, b_data)):
            _, path, diff = change

            # change in docker_images --> ignore
            if path.startswith("docker_images"):
                #print "docker_images changed"
                trigger["docker-images"] = True

        # change in everything else --> rebuild docker-kolla-ansible
        for key in ["ceph_ansible_version",
                    "ceph_version"]:
            if path.startswith(key):
                #print "%s changed" % key
                trigger["docker-kolla-ansible"] = True

print json.dumps(trigger)
