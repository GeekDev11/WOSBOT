"""WOSBOT release naming: human-readable incremental versions backed by immutable commits.

The number begins at 2.2.01 on the modified-branch baseline. Every additional
commit on that branch increases the last component by one. The commit SHA is
still used to download the exact tested source archive.
"""
from urllib.parse import quote

REPOSITORY = "GeekDev11/WOSBOT"
BRANCH = "modified"
DISPLAY_CHANNEL = "Modified"
BASELINE_SHA = "c651d257ed947ccd1a703f84f6b6b2cf3055d29b"
MAJOR = 2
MINOR = 2


def version_for_commits(commits_ahead):
    """2.2.01 at baseline, 2.2.02 after the first new commit, etc."""
    if isinstance(commits_ahead, bool) or not isinstance(commits_ahead, int) or commits_ahead < 0:
        raise ValueError("Invalid update count")
    return f"{MAJOR}.{MINOR}.{commits_ahead + 1:02d}"


def get_latest_info(get, *, repo=REPOSITORY, branch=BRANCH, baseline=BASELINE_SHA, timeout=30):
    """Fetch the latest exact commit and its monotonic version from GitHub.

    GitHub compare is pinned to the fetched SHA, so the displayed version and
    downloaded archive cannot disagree if a commit is pushed during the check.
    No other repository or branch is a fallback update source.
    """
    latest_url = f"https://api.github.com/repos/{repo}/commits?sha={quote(branch, safe='')}&per_page=1"
    latest_response = get(latest_url, timeout=timeout)
    latest_response.raise_for_status()
    commits = latest_response.json()
    if not isinstance(commits, list) or not commits or not isinstance(commits[0], dict):
        raise ValueError("No branch commits returned by GitHub")
    sha = commits[0].get("sha")
    if not isinstance(sha, str) or len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError("GitHub returned an invalid commit SHA")
    compare_url = f"https://api.github.com/repos/{repo}/compare/{baseline}...{sha}"
    compare_response = get(compare_url, timeout=timeout)
    compare_response.raise_for_status()
    compared = compare_response.json()
    if not isinstance(compared, dict) or compared.get("status") not in ("identical", "ahead"):
        raise ValueError("Update branch diverged from version baseline")
    ahead = compared.get("ahead_by")
    version = version_for_commits(ahead)
    message = commits[0].get("commit", {}).get("message", "")
    return {"version": version, "sha": sha, "message": message,
            "source": DISPLAY_CHANNEL, "branch": branch, "repo": repo}
