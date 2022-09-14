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
  addresses. Therefore security is improved.
* All Ansible roles, collections and playbooks are now checked with Ansible
  Lint and Yaml Lint
* The Elasticsearch Curator is now enabled by default (soft retention period: 5 days,
  hard retention period: 7 days)
* Ansible v6 is now used for all playbooks and collections maintained by OSISM.
* Ansible playbooks and collections maintained by OSISM are additionally tested
  on Ubuntu 22.04.
* OpenStack Yoga is available and the new default release of OpenStack
* Ceph Quincy is available, the default release of Ceph is still Pacific
* Documentation is available for all roles within the Ansible Collections
  maintained by OSISM
* The SBOM now lists SHA256 checksums for the container images

## Deprecations

* The ``cleanup-elasticsearch`` playbook is deprecated. In the future,
  the ``elasticsearch-curator`` service (part of Kolla) has to be used
  for Elasticsearch cleanup.
* Linux bridge support has been discontinued by kolla-ansible
* Debian dropped ``hddtemp`` (https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1002484),
  therefore we will remove hddtemp in the future and replace it with drivetemp

## Removals

* The service ``cockpit`` was removed.
* In favor of ``ansible.builtin.service_facts`` the Ansible plugin
  ``scan_services`` was removed.

## Notes

* Distributed Virtual Routing (DVR) is not officially supported by us,
  not tested and not recommended
* Even though Ubuntu 22.04 is already being tested, it is not yet
  officially supported with this release
* Regular rebuilds of kolla-ansible and kolla images for OpenStack Wallaby
  have been disabled.
* Regular rebuilds of ceph-ansible and ceph images for Ceph Octopus
  have been disabled.

## Infrastructure

* It is available as of now https://release.osism.tech as an overview.

## To be considered for upgrades

* In ``environments/kolla/secrets.yml`` the parameter ``neutron_ssh_key`` must be
  added.

  ```
  neutron_ssh_key:
    private_key:
    public_key:
  ```

  The ssh key can be generated as follows: ``ssh-keygen -t rsa -b 4096 -N "" -f id_rsa.neutron -C "" -m PEM``

## References

### Release notes

Ceph Pacific:

* Overview: https://docs.ceph.com/en/latest/releases/pacific/

OpenStack Yoga:

* Press announcement: https://www.openstack.org/software/yoga/
* Overview: https://releases.openstack.org/yoga/index.html
* Aodh: https://docs.openstack.org/releasenotes/aodh/yoga.html
* Barbican: https://docs.openstack.org/releasenotes/barbican/yoga.html
* Ceilometer: https://docs.openstack.org/releasenotes/ceilometer/yoga.html
* Cinder: https://docs.openstack.org/releasenotes/cinder/yoga.html
* Cloudkitty: https://docs.openstack.org/releasenotes/cloudkitty/yoga.html
* Designate: https://docs.openstack.org/releasenotes/designate/yoga.html
* Glance: https://docs.openstack.org/releasenotes/glance/yoga.html
* Heat: https://docs.openstack.org/releasenotes/heat/yoga.html
* Horizon: https://docs.openstack.org/releasenotes/horizon/yoga.html
* Ironic: https://docs.openstack.org/releasenotes/ironic/yoga.html
* Keystone: https://docs.openstack.org/releasenotes/keystone/yoga.html
* Manila: https://docs.openstack.org/releasenotes/manila/yoga.html
* Neutron: https://docs.openstack.org/releasenotes/neutron/yoga.html
* Nova: https://docs.openstack.org/releasenotes/nova/yoga.html
* Octavia: https://docs.openstack.org/releasenotes/octavia/yoga.html
* Placement: https://docs.openstack.org/releasenotes/placement/yoga.html
* Senlin: https://docs.openstack.org/releasenotes/senlin/yoga.html
