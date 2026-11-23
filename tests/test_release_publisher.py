"""Publisher must start at 2.3.0.0 and reject stale workflow runs."""
from scripts.publish_approved_release import next_version, eligible_run


def test_version_sequence():
    assert next_version(None) == (2, 3, 0, 0)
    assert next_version({"tag_name": "v2.3.0.0"}) == (2, 3, 0, 1)
    assert next_version({"tag_name": "v2.3.0.12"}) == (2, 3, 0, 13)


def test_only_successful_matching_push_qualifies():
    sha = "b" * 40
    run = {"head_sha": sha, "event": "push", "head_branch": "modified",
           "status": "completed", "conclusion": "success"}
    assert eligible_run([run], sha)
    assert not eligible_run([run], "a" * 40)
    assert not eligible_run([{**run, "conclusion": "failure"}], sha)
    assert not eligible_run([{**run, "event": "pull_request"}], sha)
