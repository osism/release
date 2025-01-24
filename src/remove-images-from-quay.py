import yaml
import typer
import sys
from typing_extensions import Annotated
from typing import Any, Dict, List
import requests
from loguru import logger
import time
import os

level = "INFO"
log_fmt = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<level>{message}</level>"
)

logger.remove()
logger.add(sys.stderr, format=log_fmt, level=level, colorize=True)


def warning_or_error(message: str, force: bool) -> None:
    """
    Shows a warning or exits with an error depending on --force flag
    """
    f = logger.warning if force else logger.error
    f(message)
    if not force:
        logger.info("Use --force to suppress this error")
        sys.exit(1)


def error_and_fail(message: str) -> None:
    logger.error(message)
    exit(1)


IMAGE_PREFIX = {
    "4.0.0": "quay.io/osism/",
    "5.0.0": "harbor.services.osism.tech/kolla/release/",
    "6.0.0": "osism.harbor.regio.digital/kolla/release/",
    "7.0.0": "osism.harbor.regio.digital/kolla/release/",
}
QUAY_PREFIX = "quay.io/osism"
QUAY_BASE_API = "https://quay.io/api/v1/repository/osism"
ADDITIONAL_REPOS = [
    "inventory-reconciler",
    "osism-ansible",
    "ceph-ansible",
    "kolla-ansible",
]


def main(
    version: Annotated[str, typer.Option("--version", "-v", help="Version to remove")],
    force: Annotated[
        bool,
        typer.Option(
            "--force", "-f", help="Force even if script would error otherwise"
        ),
    ] = False,
    no_confirm: Annotated[
        bool,
        typer.Option(
            "--no-confirm", "-c", help="Disable explicit confirmation of every removal"
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-d", help="Dry run. Do nothing."),
    ] = False,
    tag: Annotated[
        str,
        typer.Option(
            "--tag",
            help="Overwrite or set the image tag that should be removed for an image",
        ),
    ] = None,
    token: Annotated[
        str,
        typer.Option(
            "--token",
            "-t",
            help="Provide Quay.io bearer token via CLI. If not specified, use $QUAY_BEARER_TOKEN environment variable.",
        ),
    ] = None,
) -> None:

    logger.info(f"Specified version is '{version}'")

    if not dry_run:
        token = processToken(token)

    precheck(version, force)

    if not no_confirm:
        logger.info("Running WITH confirmation!")
    else:
        logger.warning("Running WITHOUT confirmation! (wait 5 seconds)")
        time.sleep(5)

    if dry_run:
        logger.info("Dry run. Nothing will be removed.")

    imageList = getImageList(version, tag, token)

    logger.info(f"Found {len(imageList)} image(s) for version '{version}'")

    logger.info("Removing kolla images...")

    for imageObject in imageList:
        imageName, imageVersion = getImageMeta(imageObject, version, force)

        if tag:
            imageVersion = tag

        imageRemoveURL = f"{QUAY_PREFIX}/{imageName}:{imageVersion}"

        if not getImageDecision(imageRemoveURL, no_confirm):
            logger.info(f"Skipping removal of '{imageRemoveURL}'")
            continue

        logger.info(f"Removing '{imageRemoveURL}'...")
        if not dry_run:
            removeImage(token, imageName, imageVersion)

    logger.info("Done removing kolla images")

    if version != "all":
        logger.info("Removing other images...")

        for imageName in ADDITIONAL_REPOS:
            imageRemoveURL = f"{QUAY_PREFIX}/{imageName}:{version}"

            if not getImageDecision(imageRemoveURL, no_confirm):
                logger.info(f"Skipping removal of '{imageRemoveURL}'")
                continue

            logger.info(f"Removing '{imageRemoveURL}'...")

            if not dry_run:
                removeImage(token, imageName, version)

        logger.info("Done removing other images")


def processToken(token: str) -> str:
    """
    Parses the quay.io bearer token either via CLI or ENV
    """
    if token is not None:
        logger.info("Using bearer token provided via CLI")
    else:
        logger.info(
            "Using bearer token provided via environment variable $QUAY_BEARER_TOKEN"
        )
        token = os.environ.get("QUAY_BEARER_TOKEN", "")

    if token is None or token == "":
        error_and_fail("Bearer token not provided or empty!")

    return token


def precheck(version: str, force: bool) -> None:
    """
    Pre check the version string for known good patterns
    """

    if version == "all":
        return

    versionComponents = version.split(".")

    if len(versionComponents) != 3:
        warning_or_error(
            "Version does not have 3 expected components [major].[minor].[build]", force
        )

    lastComponent = versionComponents[-1]
    lastCharacter = lastComponent[-1]

    if not lastCharacter.islower():
        warning_or_error(
            f"Pre-releases are expected to have a letter at the end of the last version component. Got '{lastComponent}'",
            force,
        )


def getImageListFromQuay(
    tag: str, token: str, next_page: str = None
) -> List[Dict[str, Any]]:
    apiURL = "https://quay.io/api/v1/repository"
    headers = {"content-type": "application/json", "Authorization": f"Bearer {token}"}

    if next_page:
        response = requests.get(
            f"{apiURL}?namespace=osism&next_page={next_page}", headers=headers
        )
    else:
        response = requests.get(f"{apiURL}?namespace=osism", headers=headers)

    if response.status_code not in {200}:
        error_and_fail(
            f"Get image list failed with: {response.status_code} '{response.text}'"
        )
    else:
        if "next_page" in response.json():
            next_page = response.json()["next_page"]
            result = getImageListFromQuay(tag, token, next_page)
        else:
            result = []

        for repository in response.json()["repositories"]:
            result.append({"image": f"quay.io/osism/{repository['name']}:{tag}"})

    return result


def getImageList(version: str, tag: str, token: str) -> List[Dict[str, Any]]:
    """
    Returns the yaml file containing image definitions for the given version
    """

    if version == "all":
        result = getImageListFromQuay(tag, token)

    else:
        requestURL = (
            f"https://raw.githubusercontent.com/osism/sbom/main/{version}/openstack.yml"
        )
        response = requests.get(requestURL)

        if response.status_code != 200:
            error_and_fail(
                f"Request {requestURL} returned with status code {response.status_code}"
            )

        y = yaml.load(response.text, Loader=yaml.SafeLoader)

        if not isinstance(y, dict):
            error_and_fail("Response yaml is not a dictionary")

        if "images" not in y:
            error_and_fail("Response yaml is missing the 'images' list")

        result = y["images"]

    return result


def getImageMeta(imageObject: Dict[str, Any], version: str, force: bool) -> str:
    """
    Parses the image meta data
    """
    if "image" not in imageObject:
        error_and_fail("Image object has no attribute 'image'")

    image = imageObject["image"]

    if version != "all":
        checkPrefix = IMAGE_PREFIX[version[:-1]]

        if not image.startswith(checkPrefix):
            warning_or_error(
                f"Image '{image}' does not start with known image prefix '{checkPrefix}'",
                force,
            )

    imageMeta = image.split("/")[-1]

    if ":" not in imageMeta:
        error_and_fail(f"{imageMeta} does not contain a version string")

    return imageMeta.split(":")


def getImageDecision(imageRemoveURL: str, no_confirm: bool) -> bool:
    """
    Gets a user confirmation (yes) depending on --confirm flag
    """
    if no_confirm:
        return True

    decision = None

    while decision is None:
        i = input(f"Confirm removal of image '{imageRemoveURL}': [yes/no] ")
        if i == "yes":
            decision = True
        elif i == "no":
            decision = False
        else:
            decision = None

    return decision


def removeImage(token: str, imageName: str, imageVersion: str) -> None:
    """
    Uses an API call to quay.io to remove the given image tag
    """
    apiURL = f"{QUAY_BASE_API}/{imageName}/tag/{imageVersion}"
    headers = {"content-type": "application/json", "Authorization": f"Bearer {token}"}

    response = requests.delete(apiURL, headers=headers)

    if response.status_code not in {204, 404}:
        error_and_fail(
            f"Removal request failed with: {response.status_code} '{response.text}'"
        )

    if response.status_code == 404:
        logger.warning("Image already gone...")
    else:
        logger.info("Removal completed")


if __name__ == "__main__":
    typer.run(main)
