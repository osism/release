# Miniature of container-image-osism-ansible's real script (fixture content,
# not copied verbatim -- osism_files() reads SKIP/ENVIRONMENTS from whatever
# body it is given, so the fixture only needs to exercise the same mechanics).

ENVIRONMENTS = [
    "ceph",
    "generic",
    "manager",
    "state",
    "kubernetes",
]

SKIP = [
    "manager-network.yml",
]

for environment in ENVIRONMENTS:
    pass
