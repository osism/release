# OSISM release 3.0.0

## Features

* Ceph client container image in version Quincy is available
* OpenStack client container image in version Yoga is available
* Homer is now available as an initial dashboard
* For Keycloak the available MariaDB Galera cluster can now be used as database
  backend
* Zuul is now available as a new service for future deployment management
* OpenStack images for Kubernetes Cluster API (CAPI) version 1.22 are available
* OpenStack images for Kubernetes Cluster API (CAPI) version 1.23 are available
* For Nova, SPICE is now supported as a console in addition to NoVNC
* A prepared machine image for the installation of the manager node is available
* Workers were switched to Celery with Redis as broker and backend
* Use of own NetBox image with pre-installed plugins
* Flower, a dashboard for Celery, was added as a service on the manager
* In the testbed, all hostnames were changed to publicly resolvable entries (``testbed.osism.xyz``)
* Grafana dashboards from osism/kolla-operations are now automatically imported
  into Grafana
* The docker compose CLI plugin for the Docker CLI was introduced as a
  replacement for the standalone docker compose CLI
* The configuration of the testbed was minimized and the deployment was made
  more production-oriented
* Nexus OSS (https://github.com/sonatype/nexus-public) is available as a service
* tang (https://github.com/latchset/tang) is available as a service
* A virtual BMC for controlling virtual machines using IPMI commands on the
  testbed is now usable
* An enhanced Nexus OSS image has been introduced to enable automation via the
  ``osism.services.nexus`` role.
* Various plugins are now activated by default in the NetBox
* The network configuration of the testbed was minified
* The Neutron Port Forwarding extension, required by the Kubernetes Cluster API,
  is now enabled by default
* Rolling upgrades of Glance enabled by default
* Traefik is available as a new service on the manager
* Bootstrap from Nexus was fully automated
* Various integrations of manager services for Traefik: Nexus, Nexbox, Phpmyadmin,
  Homer, Flower, ARA, Cgit
* Clamav is available as a new service
* Dnsdist is available as a new service
* Cgit is available as a new service
* FRRouting is availalbe as a new service
* OpenStack Xena is available and the new default release of OpenStack
* mod_oauth2 can be used as another plugin in the Keystone image
* Renovate is now used in most places to keep the versions up to date.
* Ansible Core 2.12 as well as Ansible Base 5.4.0 is now used for all OSISM Ansible
  collections
* Docker 20.10.13 is now used by default
* An appliance is available for the initial installation of a manager
* Bifrost can be used for intial provisioning of the Control Plane
* Ironic can be used for the provisioning of the Data Plane
* The testbed now works with TLS by default

## Removals

* Support for Zabbix has been removed, Prometheus will be used as the only
  monitoring stack in the future
* Heimdall as a service was removed, as an alternative Homer is now available
* AWX was introduced as a technical preview and possible API layer for Ansible.
  In the meantime, python-osism is used for this purpose. Accordingly, AWX is
  no longer needed. All components of the technical preview have been removed.
* Following the Kolla upstream, the panko service was removed.

## Deprecations

* Cockpit is deprecated in favor of Boundary by HashiCorp or Teleport
* Playbook ``generic-configuration.yml`` (``osism-generic configuration``) was
  deprecated
* ceph-ansible is deprecated in preparation for cephadm
* All osism- scripts on the manager are deprecated and will be replaced by
  the new OSISM CLI. The scripts will be removed in the next release.
* The following services are currently not used and are deprecated and scheduled
  for removal as of now: Falco, Jenkins, Rundeck, Lynis, Trivy
* Following the Kolla upstream, the haproxy group is deprecated as of now and will
  be removed in the future. The loadbalancer group is to be used for this
  purpose in the future.

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
* An so-called overlord service (``osism/github-actions-overlord``) is now
  available that can trigger defined reactions to commits on various repositories
* The Harbor service has moved to a much larger storage system so that Docker
  images of PRs etc. can be built in the future.
* Improve Renovate Configuration by introducing a central configuration in
  ``renovate-config``

## Security

* Log4j 2.x mitigation implemented in Ansible defaults for Elasticsearch

## To be considered for upgrades

* Playbook ``generic-configuration.yml`` was deprecated. From now on, please
  use the playbook of the same name in the manager environment (``manager-configuration.yml``).
  All configuration parameters from ``environments/configuration.yml`` should be moved
  to ``environments/manager/configuration.yml``.
