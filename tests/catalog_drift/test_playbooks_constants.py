import pytest
from osism_drift import playbooks
from osism_drift.source import SourceError

BODY = b"""
PREFIX = "kolla"
HIDE = ["blazar", "rally"]
KEEP_PREFIX = ["facts", "site"]
"""


def test_load_const_reads_a_list():
    assert playbooks.load_const(BODY, "HIDE") == ["blazar", "rally"]


def test_load_const_reads_a_string():
    assert playbooks.load_const(BODY, "PREFIX") == "kolla"


def test_load_const_missing_name_raises():
    with pytest.raises(SourceError, match="MISSING"):
        playbooks.load_const(BODY, "MISSING")


def test_load_const_returns_last_assignment():
    body_with_override = b"""
HIDE = ["original"]
HIDE = ["blazar", "rally"]
"""
    assert playbooks.load_const(body_with_override, "HIDE") == ["blazar", "rally"]


def test_load_const_non_literal_raises():
    body_with_call = b"""
HIDE = compute_hide()
"""
    with pytest.raises(SourceError, match="no longer a literal"):
        playbooks.load_const(body_with_call, "HIDE")
