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

import logging
import os
import sys
import warnings

import requests
import ruamel.yaml

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO, datefmt='%Y-%m-%d %H:%M:%S')
warnings.simplefilter('ignore', ruamel.yaml.error.UnsafeLoaderWarning)

OSISM_VERSION = os.environ.get("OSISM_VERSION", "latest")

with open("etc/roles.yml", "rb") as fp:
    roles = ruamel.yaml.load(fp)

with open("%s/base.yml" % OSISM_VERSION, "rb") as fp:
    base = ruamel.yaml.load(fp, Loader=ruamel.yaml.RoundTripLoader)

changed = False
for name, repository in roles.items():

    logging.info("Checking %s" % name)
    r = requests.get("https://api.github.com/repos/osism/ansible-%s/git/refs/heads/master" % name[6:])

    if r.status_code != 200:
        continue

    rj = r.json()
    if base['ansible_roles'][name] != rj['object']['sha']:
        print("%s: %s -> %s" % (name, base['ansible_roles'][name], rj['object']['sha']))
        base['ansible_roles'][name] = rj['object']['sha']

with open("%s/base.yml" % OSISM_VERSION, "wb") as fp:
    fp.write(ruamel.yaml.dump(base, Dumper=ruamel.yaml.RoundTripDumper, explicit_start=True))
