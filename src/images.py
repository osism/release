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

import glob
import logging
import os
import sys

import docker
import json
import yaml

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')

DOCKER_CLIENT = docker.APIClient(base_url='unix:///var/run/docker.sock')

IMAGES = os.environ.get("IMAGES", None)
if IMAGES:
    IMAGES = IMAGES.split(",")

def process(version):

    logging.info("processing version %s" % version)

    with open("etc/images.yml", "rb") as fp:
        images = yaml.load(fp)

    all_docker_images = []
    repository_version = version
    for filename in glob.glob("%s/*.yml" % version):
        with open(filename, "rb") as fp:
            versions = yaml.load(fp)
            all_docker_images.append(versions.get('docker_images', {}))
            if os.path.basename(filename) == 'base.yml' and version != 'latest':
                repository_version = versions['repository_version']

    for docker_images in all_docker_images:
        for image in docker_images:
            if IMAGES and image not in IMAGES:
                logging.info("skipping %s" % image)
                continue

            if image in ['rally', 'kolla-ansible', 'ceph-ansible', 'osism-ansible']:
                continue

            if not images[image][:5] == 'osism':
                if image == 'ceph':
                    target = 'osism/ceph-daemon'
                else:
                    target = "osism/" + images[image][images[image].find('/') + 1:]
            else:
                target = images[image]

            target_tag = repository_version
            source_tag = docker_images[image]

            if image in ['cephclient', 'openstackclient']:
                target_tag = docker_images[image] + '-' + target_tag

            if image == 'ceph':
                target_tag = "%s-%s" % (source_tag.split("-")[-1], target_tag)
                source_tag = "%s-ubuntu-16.04-x86_64" % source_tag

            logging.info("pulling - %s:%s" % (images[image], source_tag))
            DOCKER_CLIENT.pull(images[image], source_tag)

            logging.info("tagging - %s:%s" % (target, target_tag))
            DOCKER_CLIENT.tag("%s:%s" % (images[image], source_tag), target, target_tag)

            logging.info("pushing - %s:%s" % (target, target_tag))
            DOCKER_CLIENT.push(target, target_tag)

            logging.info("removing - %s:%s" % (images[image], source_tag))
            DOCKER_CLIENT.remove_image("%s:%s" % (images[image], source_tag))

            logging.info("removing - %s:%s" % (target, target_tag))
            DOCKER_CLIENT.remove_image("%s:%s" % (target, target_tag))


OSISM_VERSION = os.environ.get("OSISM_VERSION", "latest")
process(OSISM_VERSION)
