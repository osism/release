"""Parser for osism/release/<version>/base.yml."""

import yaml


def parse_release(body: bytes) -> dict:
    """Return docker_images mapping (image_name -> tag) from base.yml bytes."""
    data = yaml.safe_load(body)
    if not isinstance(data, dict) or "docker_images" not in data:
        raise ValueError("base.yml has no docker_images section")
    return {k: str(v) for k, v in data["docker_images"].items()}
