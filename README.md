# OSISM release repository

[![Build Status](https://travis-ci.org/osism/release.svg?branch=master)](https://travis-ci.org/osism/release)

**NOTE**: The release with the highest number is always the latest/master release and can/will be changed in the future. It is recommended not to use the latest/master release in productive environments.

## Workflow

![Workflow](https://raw.githubusercontent.com/osism/release/master/images/workflow.png)

## How to ..

### .. add a new Ansible role

**NOTE**: New roles can only be added in the last release.

Add an entry to the ``etc/roles.yml`` file.

In the release directory ``latest`` add an entry to the ``ansible_roles`` parameter in the ``base.yml`` file.

### .. add a new Docker image

**NOTE**: New images can only be added in the last release.

Add an entry to the ``etc/images.yml`` file.

In the ``latest`` release directory add an entry to the ``docker_images`` parameter in the ``base.yml`` file.

Push a snapshot of the new image with ``IMAGES=name_of_the_new_image OSISM_VERSION=YYYY.X.0 python src/images.py``.

### .. prepare the next release

**NOTE**: The name of a new release follows the scheme ``YYYY.X.0``.

* Copy the release directory ``latest`` to the new release directory (e.g. ``2020.1.0``)
* Set the ``repository_version`` parameter in the ``base.yml`` file to the appropriate value.
* Check the plugins.

  ```
  ceilometer_base_plugins: grep ceilometer-base-plugin kolla/common/config.py
  horizon_plugins: grep horizon-plugin kolla/common/config.py
  neutron_base_plugins: grep neutron-base-plugin kolla/common/config.py
  neutron_server_plugins: grep neutron-server kolla/common/config.py | grep -v opendaylight
  ```
* Commit the prepared release with the message ``New release: YYYY.X.0``. Make further changes in subsequent commits.
* Push snapshots of all required images with ``OSISM_VERSION=2020.1.0 python src/images.py``. The ``src/images.py`` script is part of the ``release`` repository.

### .. push images with Travis CI

* trigger a custom build with a custom config

   ```
   env:
     global:
       - OSISM_VERSION=2020.1.0
   ```

## Scripts

### ansible-roles-latest-tag.py

This script displays the last tag of all Ansible roles. This allows you to check whether all
the necessary tags are available when you create a release.

```
$ GH_ACCESS_TOKEN=abc.. python src/ansible-roles-latest-tag.py
osism/ansible-common                     v2020.1.0
osism/ansible-docker                     v2020.1.0
osism/ansible-proxy                      v2020.1.0
osism/ansible-manager                    v2020.1.0
osism/ansible-configuration              v2020.1.0
[...]
```
