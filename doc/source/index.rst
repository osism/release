==============
OSISM releases
==============

The latest available stable release is 6.0.1.

Release 6.1.0 is currently under development.

Release notes
=============

.. toctree::
   :maxdepth: 1

   notes/4.0.0
   notes/4.1.0
   notes/4.2.0
   notes/4.3.0
   notes/5.0.0
   notes/5.1.0
   notes/5.2.0
   notes/5.3.0
   notes/6.0.0
   notes/6.0.1


Release Series
==============

+--------------+----------------------+----------------------------+----------------------+--------------+
| Series       | Status               | Initial Release Date       | Next Phase           | EOL Date     |
+==============+======================+============================+======================+==============+
| 7            | Development          | 20. April 2024 (estimated) | Maintained           |              |
+--------------+----------------------+----------------------------+----------------------+--------------+
| 6            | Maintained           | 20. September 2023         | Extended Maintenance | 20. May 2024 |
+--------------+----------------------+----------------------------+----------------------+--------------+
| 5            | End of Life          |                            |                      |              |
+--------------+----------------------+----------------------------+----------------------+--------------+
| 4            | End of Life          |                            |                      |              |
+--------------+----------------------+----------------------------+----------------------+--------------+

Atom Feeds
==========

There is the possibility to subscribe to the GitHub releases or Git tags for
done releases via an Atom feed.

* https://github.com/osism/release/releases.atom
* https://github.com/osism/release/tags.atom

Use of a specific release in the configuration repository
=========================================================

The documentation has been moved: https://osism.github.io/docs/guides/upgrade-guides/manager

How do we release?
==================

The documentation has been moved: https://osism.github.io/docs/advanced-guides/developer-guide/#how-do-we-release

How do you do a release?
========================

The documentation has been moved: https://osism.github.io/docs/advanced-guides/developer-guide/#how-do-you-do-a-release

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
