"""Approved release selection is strict and cannot install arbitrary branch commits."""
from unittest.mock import Mock

import pytest

from cogs import wos_versioning as v

SHA = "a" * 40


def response(value):
    obj = Mock()
    obj.json.return_value = value
    return obj


def test_first_release_and_legacy_upgrade():
    assert v.parse_version("v2.3.0.0") == (2, 3, 0, 0)
    assert v.is_newer_version("2.3.0.0", "2.2.444")
    assert v.is_newer_version("2.3.0.1", "2.3.0.0")
    assert not v.is_newer_version("2.3.0.0", "2.3.0.1")
    assert not v.is_newer_version("2.3.0.0", "2.3.0.0")
    assert not v.is_newer_version("2.2.445", "2.2.444")


def test_approved_release_pinned_to_exact_sha():
    def get(url, **kwargs):
        if url.endswith("/releases/latest"):
            return response({"tag_name": "v2.3.0.0", "draft": False, "prerelease": False, "body": "Release"})
        if "/git/ref/tags/" in url:
            return response({"object": {"type": "commit", "sha": SHA}})
        if "/compare/" in url:
            return response({"status": "ahead"})
        raise AssertionError(url)
    info = v.get_latest_info(get)
    assert info["version"] == "2.3.0.0"
    assert info["sha"] == SHA
    assert info["message"] == "Release"


@pytest.mark.parametrize("release", [
    {"tag_name": "v2.3.0.0", "draft": True, "prerelease": False},
    {"tag_name": "v2.3.0.0", "draft": False, "prerelease": True},
    {"tag_name": "v2.2.450", "draft": False, "prerelease": False},
    {"tag_name": "v2.3.0", "draft": False, "prerelease": False},
])
def test_unapproved_releases_rejected(release):
    with pytest.raises(ValueError):
        v.get_latest_info(lambda url, **kw: response(release))


def test_moving_branch_tag_rejected():
    with pytest.raises(ValueError):
        v.get_latest_info(lambda url, **kw: (
            response({"tag_name": "v2.3.0.0", "draft": False, "prerelease": False})
            if url.endswith("/releases/latest") else
            response({"object": {"type": "tag", "sha": SHA}})
        ))
