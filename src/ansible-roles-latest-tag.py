from distutils.version import LooseVersion
import os

import github

gh = github.Github(os.environ.get("GH_ACCESS_TOKEN"))

for repository in [x for x in gh.get_organization("osism").get_repos()
                   if (x.full_name.startswith("osism/ansible") or x.full_name == "osism/osism-ansible") and not x.archived and "template" not in x.full_name]:
    tags = [LooseVersion(t.name) for t in repository.get_tags()]
    tags.sort()

    try:
        latest = tags[-1]
    except IndexError:
        latest = "none"

    print("%s %s" % (repository.full_name.ljust(40), latest))
