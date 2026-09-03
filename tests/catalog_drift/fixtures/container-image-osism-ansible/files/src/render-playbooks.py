# Miniature of container-image-osism-ansible's real script (fixture content).

PREFIXES = [
    "ceph",
    "generic",
    "manager",
]

KEEP_PREFIX = {
    "ceph": [],
    "generic": [],
    "manager": ["operator"],
}

HIDE = {
    "ceph": [],
    "generic": ["common"],
    "manager": [],
}
