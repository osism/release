# OSISM release 3.0.0

## Features

* OpenStack client container image in version Yoga is available
* Ansible >=2.10.0,<2.11.0 required by all Ansible collections
* Homer is now available as an initial dashboard
* For Keycloak the available MariaDB Galera cluster can now be used as database
  backend
* Zuul is now available as a new service for future deployment management
* OpenStack images for Kubernetes Cluster API (CAPI) version 1.22 are available
* Tailscale (https://tailscale.com) is available as an alternative for Wireguard
  on the testbed
* For Nova, SPICE is now supported as a console in addition to NoVNC
* A prepared machine image for the installation of the manager node is available
* Workers were switched to Celery with Redis as broker and backend
* Use of own NetBox image with pre-installed plugins
* Flower, a dashboard for Celery, was added as a service on the manager
* In the testbed, all hostnames were changed to publicly resolvable entries (``testbed.osism.xyz``)
* Grafana dashboards from osism/kolla-operations are now automatically imported
  into Grafana
* The docker-compose CLI plugin for the Docker CLI was introduced as a
  replacement for the standalone docker-compose CLI
* The configuration of the testbed was minimized and the deployment was made
  more production-oriented
* Nexus OSS (https://github.com/sonatype/nexus-public) is available as a service
* tang (https://github.com/latchset/tang) is available as a service
* A virtual BMC for controlling virtual machines using IPMI commands on the
  testbed is now usable
* An enhanced Nexus OSS image has been introduced to enable automation via the
  ``osism.services.nexus`` role.
* Vault is now usable as a service in the testbed
* Various plugins are now activated by default in the NetBox
* The network configuration of the testbed was minified
* The Neutron Port Forwarding extension, required by the Kubernetes Cluster API,
  is now enabled by default
* Rolling upgrades of Glance enabled by default

## Removals

* Support for Zabbix has been removed, Prometheus will be used as the only
  monitoring stack in the future
* Heimdall as a service was removed, as an alternative Homer is now available

## Deprecations

* Cockpit is deprecated in favor of Boundary by HashiCorp
* Playbook ``generic-configuration.yml`` (``osism-generic configuration``) was
  deprecated

## Conformance

* Tests for OpenStack Powered Compute 2020.11 successful for Wallaby (https://refstack.openstack.org/#/results/054e85a0-857e-49c5-906c-3e124a1fdd03)
* OSISM is officially OpenStack Powered and listed in the marketplace (https://www.openstack.org/marketplace/distros/distribution/osism/osism)
* Designate and Heat are now also tested for Wallaby

## Fixes

* In the inventory reconciler the import into the NetBox was fixed
* The image of patchman was changed to Ubuntu to fix problems when using libapt
* In Patchman, Ubuntu 20.04 was added as a distribution
* For Wallaby a missing backport for Fluentd has been added which now also adds
  the possibility to enable the watch timer for Wallaby. This solves issues with
  delivering logs from local Fluentd process to Elasticsearch. The Watch Timer is
  activated by default. This is not the case in the upstream of kolla-ansible.
* The resources provided by Cadvisor were limited to solve high load problems

## Infrastructure

* An Elasticsearch service is now available for integration into the CI
* A Kibana service is now available for the evaluation of the logs from the CI
* A new Minio service is available for binary artifacts like machine images
* Plusserver provides resources on the Pluscloudopen for daily deployments
* It is now possible to set the permissions of all repositories in the osism
  organisation via the github-permissions repository
* Most of the container images have been consolidated in the central
  ``container-images`` repository

## Security

* Log4j 2.x mitigation implemented in Ansible defaults for Elasticsearch

## To be considered for upgrades

* Playbook ``generic-configuration.yml`` was deprecated. From now on, please
  use the playbook of the same name in the manager environment (``manager-configuration.yml``).
  All configuration parameters from ``environments/configuration.yml`` should be moved
  to ``environments/manager/configuration.yml``.
