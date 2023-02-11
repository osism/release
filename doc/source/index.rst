==============
OSISM releases
==============

The latest available stable release is 4.2.0.

Release 5.0.0 is currently under development.

The next available pre-release will be 5.0.0a.

Release notes
=============

.. toctree::
   :maxdepth: 1

   notes/4.0.0
   notes/4.1.0
   notes/4.2.0
   notes/5.0.0

Use of a specific release in the configuration repository
=========================================================

* read the release notes to learn what has changed and what adjustments are necessary
* sync the image versions in the configuration repository

  .. code-block:: console

     MANAGER_VERSION=4.2.0 gilt overlay  # you have to do this 2x
     MANAGER_VERSION=4.2.0 gilt overlay

* set the new manager version in the configuration repository

  .. code-block:: console

     yq -i '.manager_version = "4.2.0"' environments/manager/configuration.yml

* if ``openstack_version`` or ``ceph_version`` are set in ``environments/manager/configuration.yml``,
  they must be removed when using a stable release

* update the manager services on the manager

  .. code-block:: console

     osism apply configuration
     osism-update-manager

* synchronise the reconciler

  .. code-block:: console

     osism reconciler sync

* refresh the facts

  .. code-block:: console

     osism apply facts
     osism-generic facts  # old way

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


How do you do a release?
========================

Pre-release
-----------

Stable release
--------------

* Copy the directory of the last pre-release or the previous stable release.
  The release to be created is used as the new name.

  .. code-block:: none

     4.0.0b -> 4.0.0
     4.0.0  -> 4.1.0

* Change all necessary versions in the YAML files within the new directory.
  In any case, the version of the pre-release or the version of the stable
  release must be replaced by the release to be created.

* The release to be created is submitted as a pull request as usual and then
  merged.

* Add a tag with the name of the new release to the listed repositories.

  .. code-block:: none

     osism/cfg-cookiecutter
     osism/container-image-ceph-ansible
     osism/container-image-inventory-reconciler
     osism/container-image-osism-ansible
     osism/container-images-kolla

* After completing the creation of the images in repository ``container-images-kolla``,
  the file ``images.yml`` must be added to repository ``osism/sbom`` as
  ``4.0.0/openstack.yml`` (instead of ``4.0.0``, the corresponding release is used).
  The file is available as a build artefact of the ``Build container images`` action
  on the created tag.

  Before the file is added, it is enhanced with the checksums of the images. The script
  is available in the ``osism/sbom`` repository.

  .. code-block:: none

     VERSION=4.0.0 python3 scripts/add-image-checksum.py

* If ``4.0.0/openstack.yml`` is present in ``osism/sbom``, repository
  ``osism/container-image-kolla-ansible`` can be tagged like the other
  repositories before.

* Add the created SPDX files from the listed repositories to the ``osism/sbom`` repository.
  The file are available as build artefacts of the ``Build container image`` action
  on the created tags.

  .. code-block:: none

     osism/container-image-ceph-ansible
     osism/container-image-kolla-ansible
     osism/container-image-osism-ansible

* Test. Test. Test.

* Prepare a PR to change the stable version to the new stable version in the Zuul job
  ``testbed-deploy-stable`` in the ``osism/testbed`` repository. All tests there must
  pass successfully before the tag is set on this repository in the next step.

* After all known issues are documented, a corresponding tag, e.g. ``v4.2.0``, is set on the
  release repository.

* As the last of the release process, the previously prepared PR is merged on the
  ``osism/testbed`` repository to change the stable version.

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
