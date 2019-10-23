import logging
import os
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
    if name.startswith("osism"):
        continue

    logging.info("Checking %s" % name)
    r = requests.get("https://api.github.com/repos/%s/git/refs/heads/master" % repository)

    if r.status_code != 200:
        logging.warning("Status code %d != 200 (%s)" % (r.status_code, r.json()['message']))
        continue

    rj = r.json()
    if base['ansible_roles'][name] != rj['object']['sha']:
        logging.info("%s: %s -> %s" % (name, base['ansible_roles'][name], rj['object']['sha']))
        print("%s: %s -> %s" % (name, base['ansible_roles'][name], rj['object']['sha']))
        base['ansible_roles'][name] = rj['object']['sha']

with open("%s/base.yml" % OSISM_VERSION, "w") as fp:
    fp.write(ruamel.yaml.dump(base, Dumper=ruamel.yaml.RoundTripDumper, explicit_start=True))
