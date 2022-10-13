import glob
import logging
import os

import docker
from tabulate import tabulate
import yaml

logging.basicConfig(
    format="%(asctime)s - %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S"
)

SKIP_IMAGES = ["ceph-ansible", "installer", "kolla-ansible", "osism-ansible", "rally"]

SKIP_LATEST_IMAGES = ["aptly", "ara_server", "ara_web", "cobbler"]

OSISM_VERSION = os.environ.get("OSISM_VERSION", "latest")
DRY_RUN = os.environ.get("DRY_RUN", False) == "True"

if not DRY_RUN:
    DOCKER_CLIENT = docker.APIClient(base_url="unix:///var/run/docker.sock")

IMAGES = os.environ.get("IMAGES", None)
if IMAGES:
    IMAGES = IMAGES.split(",")


def process(version):
    logging.info("processing version %s" % version)

    with open("etc/images.yml", "rb") as fp:
        images = yaml.load(fp, Loader=yaml.SafeLoader)

    result = []
    all_docker_images = []
    repository_version = version
    for filename in glob.glob("%s/*.yml" % version):
        with open(filename, "rb") as fp:
            versions = yaml.load(fp, Loader=yaml.SafeLoader)
            all_docker_images.append(versions.get("docker_images", {}))
            if os.path.basename(filename) == "base.yml" and version != "latest":
                repository_version = versions["repository_version"]

    for docker_images in all_docker_images:
        for image in docker_images:
            if IMAGES and image not in IMAGES:
                logging.info("skipping - %s" % image)
                continue

            logging.info("checking - %s" % image)

            if image in SKIP_IMAGES:
                logging.info("skipping - %s" % image)
                continue

            if image in SKIP_LATEST_IMAGES and repository_version == "latest":
                logging.info("skipping - %s" % image)
                continue

            if image not in images:
                logging.error(
                    "skipping - definiton of %s is missing in etc/images.yml" % image
                )
                continue

            if not images[image][:5] == "osism":
                if image == "ceph":
                    target = "osism/ceph-daemon"
                else:
                    target = "osism/" + images[image][images[image].find("/") + 1 :]
            else:
                target = images[image]

            source = images[image]

            target_tag = repository_version
            source_tag = docker_images[image]

            if image in ["cephclient", "openstackclient"]:
                target_tag = docker_images[image] + "-" + target_tag

            if image == "ceph" and "stable-3.1" in source_tag:
                target_tag = "%s-%s" % (source_tag.split("-")[-1], target_tag)
                source_tag = "%s-ubuntu-16.04-x86_64" % source_tag

            if image == "ceph" and (
                "stable-3.2" in source_tag or "stable-4.0" in source_tag
            ):
                target_tag = "%s-%s" % (source_tag.split("-")[-1], target_tag)
                source = "osism/ceph-daemon"
                source_tag = "%s-centos-7-x86_64" % source_tag

            if image == "ceph" and ("stable-5.0" in source_tag):
                target_tag = "%s-%s" % (source_tag.split("-")[-1], target_tag)
                source = "osism/ceph-daemon"
                source_tag = "%s-centos-8-x86_64" % source_tag

            if image == "ceph" and "latest" in source_tag:
                logging.info("skipping - %s (latest)" % image)
                continue

            if image == "cephclient" and "latest" in source_tag:
                logging.info("skipping - %s (latest)" % image)
                continue

            if image == "openstackclient" and "latest" in source_tag:
                logging.info("skipping - %s (latest)" % image)
                continue

            logging.info("pulling - %s:%s" % (source, source_tag))

            if not DRY_RUN:
                DOCKER_CLIENT.pull(source, source_tag)
                docker_image = DOCKER_CLIENT.inspect_image(
                    "%s:%s" % (source, source_tag)
                )
                result.append(
                    [source, source_tag, docker_image["Id"], docker_image["Created"]]
                )

            logging.info("tagging - %s:%s" % (target, target_tag))

            if not DRY_RUN:
                DOCKER_CLIENT.tag("%s:%s" % (source, source_tag), target, target_tag)

            logging.info("pushing - %s:%s" % (target, target_tag))

            if not DRY_RUN:
                DOCKER_CLIENT.push(target, target_tag)

            logging.info("removing - %s:%s" % (source, source_tag))

            if not DRY_RUN:
                DOCKER_CLIENT.remove_image("%s:%s" % (source, source_tag))

            logging.info("removing - %s:%s" % (target, target_tag))

            if not DRY_RUN:
                DOCKER_CLIENT.remove_image("%s:%s" % (target, target_tag))

    return result


result = process(OSISM_VERSION)

if not DRY_RUN:
    print(tabulate(result, headers=["Image", "Tag", "Hash", "Created"]))
