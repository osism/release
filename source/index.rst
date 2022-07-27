==============
OSISM releases
==============

The latest available release is 3.2.0.

Release 4.0.0 is currently under development.

Use of a specific release in the configuration repository
=========================================================

* read the release notes to learn what has changed and what adjustments are necessary
* sync the image versions in the configuration repository

  .. code-block:: console

     MANAGER_VERSION=3.2.0 gilt overlay  # you have to do this 2x
     MANAGER_VERSION=3.2.0 gilt overlay

* set the new manager version in the configuration repository

  .. code-block:: console

     yq -i '.manager_version = "3.2.0"' environments/manager/configuration.yml

If ``openstack_version`` or ``ceph_version`` are set in ``environments/manager/configuration.yml``:
these parameters are removed when using a stable release

* update the manager services on the manager

  .. code-block:: console

     osism apply configuration
     osism-update-manager

With Release 3.0.0, a manual update of the environment is required afterwards. As
of Release 4.0.0, this will no longer be necessary.

How do we release?
==================

Currently we do a major release every 6 months. Minor releases we do when needed and
about every 2 weeks.

In a minor release, only updates, bug fixes, etc. take place. There are also no major
upgrades of included components such as OpenStack, Keycloak or Ceph in a minor release.

It is possible to jump from any minor version within a major version to higher minor
versions without any intervention.

Deprecations, removals, etc. take place in a major release. New mandatory features are
also added in a major release. Upgrades of the included components can also take place
during a major release (e.g. OpenStack Xena -> OpenStack Yoga).

It is possible to jump from the previous major version to the next major version. It may
be that manual intervention is necessary. For example, configuration parameters may need
to be added or services that no longer exist may need to be removed.

Questions & Answers
===================

What all is included in the osism/release repository?
-----------------------------------------------------

The osism/release repository (this repository) contains one directory per release. In this
directory files are available for the individual environments in which the versions or
hashes of all used components are located.

Why is there an osism/sbom repository?
--------------------------------------

The osism/sbom repository contains a file for each available environment for each release.
These files contain the versions of the components in each image that was published.

At the moment, only the versions of the OpenStack environment are covered there.

The format of the files is currently still YAML. In the future SPDX files will be provided
there.
