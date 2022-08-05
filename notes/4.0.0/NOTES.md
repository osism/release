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
* LUKS encryption is now documented and enabled in the testbed by default
* Network bound disk encryption (NBDE) is now usable
* Wireguard VPN added. Makes it possible to access the Control Plane from the
  Manager through a secured connection. It is also enabled in the testbed by
  default.
* Sosreport is now available and enabled by default in the testbed. This tool 
  collects configuration and diagnostic informations from Linux based
  distributions.
* Squid proxy is now available. Allows other services to access only allowed
  addresses. Therefore security get's improved.
* All Ansible roles, collections and playbooks are now checked with Ansible
  Lint and Yaml Lint
* The Elasticsearch Curator is now enabled by default (soft retention period: 5 days,
  hard retention period: 7 days)
* Ansible v6 is now used for all playbooks and collections maintained by OSISM.
* Ansible playbooks and collections maintained by OSISM are additionally tested
  on Ubuntu 22.04.
* OpenStack Yoga is available and the new default release of OpenStack

## Deprecations

* The ``cleanup-elasticsearch`` playbook is deprecated. In the future,
  the ``elasticsearch-curator`` service (part of Kolla) has to be used
  for Elasticsearch cleanup.

## Removals

* The service ``cockpit`` was removed.
* In favor of ``ansible.builtin.service_facts`` the Ansible plugin
  ``scan_services`` was removed.

## Infrastructure

* It is available as of now https://release.osism.tech as an overview.
