"""Static parser for the SBOM_IMAGE_TO_VERSION map in
container-images-kolla/src/tag-images-with-the-version.py.

Read via ast.literal_eval — the producer script is never imported or executed.
"""

import ast


def parse_sbom_keys(body: bytes) -> set[str]:
    """Return the set of keys of the SBOM_IMAGE_TO_VERSION dict literal."""
    tree = ast.parse(body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "SBOM_IMAGE_TO_VERSION"
                ):
                    return set(ast.literal_eval(node.value).keys())
    raise ValueError("SBOM_IMAGE_TO_VERSION assignment not found")
