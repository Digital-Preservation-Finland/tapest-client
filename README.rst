tapest-ice-api-client
=====================

Packages the ``ice_api_client.py`` module from `fairdata/csc-ice
<https://gitlab.ci.csc.fi/fairdata/csc-ice>`_ as an RPM for use in DPRES
environments.

This client library is used for communication between csc-ice and
tapest-tape-worker, and can also be used by external clients.

How it works
------------

``ice_api_client.py`` is committed to ``include/rhel9/SOURCES/`` at the pinned
csc-ice version. The spec installs it as a Python package. No token is needed
to build the RPM locally or in CI.

Updating the upstream version
------------------------------

1. Copy the updated file from a local clone of ``fairdata/csc-ice``::

       cp ../csc-ice/core/ice_api_client.py include/rhel9/SOURCES/ice_api_client.py

2. Commit the updated ``include/rhel9/SOURCES/ice_api_client.py``.

Releasing
---------

After merging to master, tag the repository following semantic versioning::

    git tag -a v<major>.<minor>.<patch> -m 'Version <major>.<minor>.<patch>'
    git push -u origin v<major>.<minor>.<patch>

The tag drives the RPM ``Version`` field via the build scripts.
