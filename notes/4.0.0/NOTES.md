# OSISM release 4.0.0

## Features

* Renovate is now used to keep the versions in the defaults of the Ansible
  collections and within the container images up to date.
* OpenStack client container image in version Zed is available
* The configuration repository can now also be accessed via PAT and HTTP
  proxy
* The cookiecutter can now also be used via a container image
* Seeding of a manager node can now also be done via a container image
* The reduction of the sizes of the container images was continued everywhere
* The playbooks for the manager, which were previously duplicated in each
  configuration repository, have been bundled into a separate Ansible collection
  (osism.manager @ https://github.com/osism/ansible-playbooks-manager)
* ARA is now available in version 1.5.8 (latest image is now also available)
* Where possible, the Python version used was updated to 3.10

## Deprecations

* The ``cleanup-elasticsearch`` playbook is deprecated. In the future,
  the ``elasticsearch-curator`` service (part of Kolla) has to be used
  for Elasticsearch cleanup.

## Infrastructure

* It is available as of now https://release.osism.tech as an overview.
