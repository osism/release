# OSISM release 4.0.0

## Features

* Renovate is now used to keep the versions in the defaults of the Ansible
  collections and within the container images up to date.
* OpenStack client container image in version Zed is available

## Deprecations

* The ``cleanup-elasticsearch`` playbook is deprecated. In the future,
  the ``elasticsearch-curator`` service (part of Kolla) has to be used
  for Elasticsearch cleanup.
