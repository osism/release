from osism_drift.model import DriftEntry


def test_drift_entry_has_all_fields():
    entry = DriftEntry(
        plugin="release_vs_manager",
        image="redis",
        alias="manager_redis",
        expected="7.5.0",
        found="7.4.7-alpine",
        expected_src="release/latest/base.yml",
        found_src="testbed/environments/manager/images.yml",
    )
    assert entry.plugin == "release_vs_manager"
    assert entry.image == "redis"
    assert entry.alias == "manager_redis"
    assert entry.expected == "7.5.0"
    assert entry.found == "7.4.7-alpine"
    assert entry.expected_src == "release/latest/base.yml"
    assert entry.found_src == "testbed/environments/manager/images.yml"


def test_drift_entry_to_dict_for_json():
    entry = DriftEntry(
        plugin="role_shadows",
        image="adminer",
        alias="adminer",
        expected="5.4.2",
        found="4.7",
        expected_src="release/latest/base.yml",
        found_src="ansible-collection-services/roles/adminer/defaults/main.yml",
    )
    d = entry.to_dict()
    assert d["plugin"] == "role_shadows"
    assert d["alias"] == "adminer"
    assert "expected_src" in d


def test_default_severity_is_actionable():
    d = DriftEntry(
        plugin="p",
        image="i",
        alias="a",
        expected="1",
        found="2",
        expected_src="s",
        found_src="f",
    )
    assert d.severity == "actionable"


def test_as_allowlisted_preserves_severity():
    d = DriftEntry(
        plugin="p",
        image="i",
        alias="a",
        expected="1",
        found="2",
        expected_src="s",
        found_src="f",
        severity="advisory",
    )
    assert d.as_allowlisted("because").severity == "advisory"
