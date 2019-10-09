from distutils.version import LooseVersion
import os

import github
 
gh = github.Github(os.environ.get("GH_ACCESS_TOKEN"))
 
for repository in [x for x in gh.get_organization("osism").get_repos() if x.full_name.startswith("osism/ansible") and not x.archived and not "template" in x.full_name]:
    tags = [LooseVersion(t.name) for t in repository.get_tags()]
    tags.sort()
    print("%s %s" % (repository.full_name.ljust(40), tags[-1]))
