"""Publish the latest fully-tested Modified commit as a stable WOSBOT release.

Executed by the GitHub Actions publisher, never by the running Discord bot.
Only the most recent branch HEAD can be released; earlier green builds cannot
publish an outdated build. The 10-minute quiet period bundles nearby pushes.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = "GeekDev11/WOSBOT"
BRANCH = "modified"
WORKFLOW = "chief-stalker-tests.yml"
FIRST = (2, 3, 0, 0)
COOLDOWN_SECONDS = 600
VERSION_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def api(path, *, method="GET", payload=None):
    token = os.environ["GITHUB_TOKEN"]
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPO + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "WOSBOT-approved-release-publisher",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404 and method == "GET":
            return None
        raise


def next_version(previous):
    if previous is None:
        return FIRST
    tag = previous.get("tag_name", "")
    match = VERSION_PATTERN.fullmatch(tag)
    if not match:
        raise ValueError("Unexpected existing stable release tag; refusing to overwrite it")
    version = tuple(int(i) for i in match.groups())
    if version < FIRST or version[:3] != FIRST[:3]:
        raise ValueError("Release series mismatch; manual migration required")
    return (*version[:3], version[3] + 1)


def eligible_run(runs, sha):
    """Require success for a push workflow at exactly the candidate commit."""
    return any(
        run.get("head_sha") == sha
        and run.get("event") == "push"
        and run.get("head_branch") == BRANCH
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        for run in runs
    )


def main():
    branch = api("/branches/" + BRANCH)
    sha = branch["commit"]["sha"]
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError("Invalid branch commit SHA")
    commit = api("/commits/" + sha)
    last_commit = dt.datetime.fromisoformat(
        commit["commit"]["committer"]["date"].replace("Z", "+00:00"))
    age = (dt.datetime.now(dt.timezone.utc) - last_commit).total_seconds()
    if age < COOLDOWN_SECONDS:
        print("Recent changes detected; waiting for the quiet period before release.")
        return

    previous = api("/releases/latest")
    if previous is not None:
        previous_tag = previous["tag_name"]
        previous_ref = api("/git/ref/tags/" + urllib.parse.quote(previous_tag, safe=""))
        if not previous_ref or previous_ref["object"]["type"] != "commit":
            raise ValueError("Last release is missing an immutable commit tag")
        if previous_ref["object"]["sha"] == sha:
            print("Latest branch commit has already been released.")
            return

    # This publisher is a job INSIDE the required workflow. The workflow
    # cannot have overall conclusion=success until this job finishes, so
    # validate both prerequisite jobs on this exact push run instead.
    run_id = os.environ["GITHUB_RUN_ID"]
    run = api("/actions/runs/" + run_id)
    jobs = api("/actions/runs/" + run_id + "/jobs?per_page=100")
    passed = {job["name"] for job in jobs.get("jobs", [])
              if job.get("conclusion") == "success"}
    if (run.get("head_sha") != sha or run.get("event") != "push"
            or run.get("head_branch") != BRANCH
            or not {"whiteout-stats", "SimCord UI and callback regression"}.issubset(passed)):
        print("Both required jobs have not passed at the current Modified HEAD. No release.")
        return

    # A later push can happen during requests. Never tag an untested/stale SHA.
    if api("/branches/" + BRANCH)["commit"]["sha"] != sha:
        print("Branch advanced while checking tests. No release.")
        return

    version = ".".join(map(str, next_version(previous)))
    tag = "v" + version
    if api("/git/ref/tags/" + urllib.parse.quote(tag, safe="")) is not None:
        raise ValueError("Release tag already exists; refusing to overwrite it")

    # Summarize the commits since last approved release.
    notes = ["Automated stable release, approved by the full WOSBOT test workflow."]
    if previous is not None:
        old_sha = previous_ref["object"]["sha"]
        comparison = api("/compare/" + old_sha + "..." + sha)
        if comparison["status"] != "ahead":
            raise ValueError("New release is not ahead of previous release")
        commits = comparison.get("commits", [])
        notes.extend("- " + c["commit"]["message"].splitlines()[0][:160]
                     for c in commits[-25:])
    else:
        notes.append("- Initial 2.3.0.0 major release, including approved Modified changes.")

    # The release's tag is created directly at a validated immutable SHA.
    api("/git/refs", method="POST", payload={"ref": "refs/tags/" + tag, "sha": sha})
    api("/releases", method="POST", payload={
        "tag_name": tag,
        "target_commitish": sha,
        "name": "WOSBOT " + version,
        "body": "\n".join(notes),
        "draft": False,
        "prerelease": False,
        "make_latest": "true",
    })
    print("Published approved WOSBOT release", tag, sha)


if __name__ == "__main__":
    main()
