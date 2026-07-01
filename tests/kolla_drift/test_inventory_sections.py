from osism_drift.inventory_sections import parse_groups

SAMPLE = b"""\
[control]
ctl01
ctl02

[nova:children]
control
compute

[empty:children]
"""


def test_returns_groups_with_sorted_members():
    groups = parse_groups(SAMPLE)
    assert set(groups) == {"control", "nova:children", "empty:children"}
    assert groups["nova:children"] == ["compute", "control"]
    assert groups["control"] == ["ctl01", "ctl02"]


def test_empty_group_has_no_members():
    assert parse_groups(SAMPLE)["empty:children"] == []


def test_duplicate_member_in_group_does_not_raise():
    """A host listed twice under one group is a common editing mistake; it must
    parse (last wins), not crash the run with configparser.DuplicateOptionError."""
    body = b"[control]\nctl01\nctl02\nctl01\n"
    groups = parse_groups(body)
    assert groups["control"] == ["ctl01", "ctl02"]
