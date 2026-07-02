"""Parser for ansible-style INI inventory files (kolla-ansible multinode and
the OSISM 50/51-kolla files).

Returns each group (INI section) mapped to its sorted members — child-group
names for a :children group, host names for a host group — via configparser,
the same parser the original check-kolla-inventory.py used. The group-name set
for the drift comparison is set(parse_groups(body)); the members make a flagged
group actionable. optionxform is overridden to preserve member-name case.
"""

import configparser


def parse_groups(body: bytes) -> dict[str, list[str]]:
    """Return {section: sorted(member names)} for every INI section."""
    # strict=False tolerates a host/group listed twice within one section (a
    # common editing mistake, harmless to ansible) instead of raising
    # DuplicateOptionError, which would crash the run outside the SourceError path.
    cp = configparser.ConfigParser(allow_no_value=True, strict=False)
    cp.optionxform = str  # preserve case of member names
    cp.read_string(body.decode("utf-8"))
    return {section: sorted(cp[section].keys()) for section in cp.sections()}
