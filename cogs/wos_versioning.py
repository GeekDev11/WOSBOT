"""WOSBOT's approved Modified release channel.

Only published, non-draft GitHub releases tagged v2.3.0.N (or a later
compatible four-part version) are installable. Branch commits are NOT updates.
Every source archive is pinned to the exact SHA validated by the release.
"""
import re

REPOSITORY = "GeekDev11/WOSBOT"
BRANCH = "modified"
DISPLAY_CHANNEL = "Modified"
FIRST_RELEASE = (2, 3, 0, 0)
_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)\.(\d+)$")
_SHA = re.compile(r"^[a-f0-9]{40}$")


def parse_version(value):
    if not isinstance(value, str):
        return None
    match = _TAG.fullmatch(value.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate, current):
    next_version = parse_version(candidate)
    previous = parse_version(current)
    if not next_version or next_version < FIRST_RELEASE:
        return False
    # Old 2.2.### installations are intentionally upgraded to first 2.3.0.0.
    if previous is None:
        old = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(current).strip())
        if old:
            previous = (*map(int, old.groups()), 0)
        else:
            return False
    return next_version > previous


def get_latest_info(get, *, repo=REPOSITORY, branch=BRANCH, timeout=30):
    """Use only an approved GitHub release with a pinned commit SHA.

    A release's target_commitish can be a moving branch name: resolve the
    immutable tag ref instead. Publisher creates lightweight tags at exact
    tested commits. Annotated tags are rejected rather than guessed.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    response = get(url, timeout=timeout)
    response.raise_for_status()
    release = response.json()
    tag = release.get("tag_name")
    version = parse_version(tag)
    if (not version or version < FIRST_RELEASE
            or release.get("draft") or release.get("prerelease")):
        raise ValueError("No approved WOSBOT 2.3 release is available")
    tag_response = get(f"https://api.github.com/repos/{repo}/git/ref/tags/{tag}", timeout=timeout)
    tag_response.raise_for_status()
    ref = tag_response.json()
    obj = ref.get("object") or {}
    sha = obj.get("sha")
    if obj.get("type") != "commit" or not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ValueError("Published release does not point to a verified commit")
    # Reject a release from any other branch or fork.
    compare = get(f"https://api.github.com/repos/{repo}/compare/{sha}...{branch}", timeout=timeout)
    compare.raise_for_status()
    if compare.json().get("status") not in ("identical", "ahead"):
        raise ValueError("Published release is not an ancestor of Modified")
    return {
        "version": ".".join(map(str, version)),
        "sha": sha,
        "message": str(release.get("body") or release.get("name") or "Approved WOSBOT release"),
        "source": DISPLAY_CHANNEL,
        "branch": branch,
        "repo": repo,
    }
