"""Deployment guard: the custom fork must never download the upstream bot.

These tests work without importing main.py (which has startup side effects).
"""
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "main.py"


def test_bootstrap_pinned_to_our_repo():
    source = SOURCE.read_text()
    assert "WOSBOT_REPO = 'GeekDev11/WOSBOT'" in source
    assert "WOSBOT_BRANCH = 'feature/whiteoutstats-intelligence'" in source
    assert "_bootstrap_from_main_branch(WOSBOT_ARCHIVE)" in source
    assert "BOT_BOOTSTRAP_URL" not in source
    assert "whiteout-project/bot" not in source
    assert "gitlab.whiteout-bot.com" not in source


def test_no_automatic_source_overwrite_on_container_start():
    source = SOURCE.read_text()
    assert 'if "--autoupdate" in sys.argv or "--repair" in sys.argv:' in source
    assert "Automatic updates disabled (WOSBOT fork)" in source
    assert "WOSBOT_ARCHIVE" in source


def test_missing_requirements_are_from_our_fork():
    source = SOURCE.read_text()
    assert 'f"https://raw.githubusercontent.com/{WOSBOT_REPO}/{WOSBOT_BRANCH}/requirements.txt"' in source


def test_startup_reads_hosted_token():
    source = SOURCE.read_text()
    assert 'os.environ.get("BOT_TOKEN", "").strip()' in source
