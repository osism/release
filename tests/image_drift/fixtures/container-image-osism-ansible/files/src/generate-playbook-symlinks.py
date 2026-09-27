# Miniature of container-image-osism-ansible's real script (fixture content).
# playbooks.osism_files() reads only these two constants; manager-netbox.yml is
# skipped exactly as in the real SKIP list.
ENVIRONMENTS = [
    "infrastructure",
    "manager",
]

SKIP = [
    "manager-netbox.yml",
]
