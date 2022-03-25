# OSISM release repository

## Use of a specific release in the configuration repository

* read the release notes to learn what has changed and what adjustments are necessary

* sync the image versions in the configuration repository

```
MANAGER_VERSION=3.0.0 gilt overlay  # you have to do this 2x
MANAGER_VERSION=3.0.0 gilt overlay
```

* set the new manager version in the configuration repository

```
yq -i '.manager_version = "3.0.0"' environments/manager/configuration.yml
```

If ``openstack_version`` or ``ceph_version`` are set in ``environments/manager/configuration.yml``: these parameters are removed when using a stable release

* update the manager services on the manager

```
osism apply configuration
osism-update-manager
```

With Release 3.0.0, a manual update of the environment is required afterwards. As of Release 4.0.0, this will no longer be necessary.
