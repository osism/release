import pytest
from osism_drift.release import parse_release


def test_parse_release_returns_docker_images():
    body = b"""---
docker_images:
  ara_server: '1.7.5'
  redis: '7.5.0'
manager_version: latest
"""
    out = parse_release(body)
    assert out["ara_server"] == "1.7.5"
    assert out["redis"] == "7.5.0"
    assert "manager_version" not in out


def test_parse_release_missing_docker_images_errors():
    with pytest.raises(ValueError, match="docker_images"):
        parse_release(b"---\nfoo: bar\n")
