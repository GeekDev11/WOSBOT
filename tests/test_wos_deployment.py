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
    assert 'startup.phase_ok(f"Updates opt-in: {WOSBOT_REPO} · {WOSBOT_BRANCH}")' in source
    assert "WOSBOT_ARCHIVE" in source


def test_missing_requirements_are_from_our_fork():
    source = SOURCE.read_text()
    assert 'f"https://raw.githubusercontent.com/{WOSBOT_REPO}/{WOSBOT_BRANCH}/requirements.txt"' in source


def test_startup_reads_hosted_token():
    source = SOURCE.read_text()
    assert 'os.environ.get("BOT_TOKEN", "").strip()' in source


def test_update_archive_is_pinned_to_fork_commit():
    source = SOURCE.read_text()
    assert "f'https://github.com/{WOSBOT_REPO}/archive/{sha}.zip'" in source
    assert "'tag_name': f'wosbot-{sha[:12]}'" in source
    assert "'source': f'{WOSBOT_REPO}@{WOSBOT_BRANCH}'" in source
    assert "return {'tag_name': f'beta-" not in source
    assert "whiteout-project/bot" not in source


def test_updater_preserves_database_and_secrets():
    source = SOURCE.read_text()
    assert 'Database backup failed:' in source
    assert '"bot_token.txt", "version", ".env", ".env.local"' in source
    assert 'norm_path.startswith("./db/")' in source


def test_maintenance_update_menu_uses_only_wosbot_branch():
    source = (SOURCE.parent / 'cogs' / 'bot_operations.py').read_text()
    assert "repo = 'GeekDev11/WOSBOT'" in source
    assert "branch = 'feature/whiteoutstats-intelligence'" in source
    assert "latest_version = f'wosbot-{sha[:12]}'" in source
    assert 'whiteout-project/bot/releases/latest' not in source
    assert 'Cannot verify WOSBOT update source. No restart.' in source


def test_maintenance_restart_enables_existing_startup_updater():
    source = (SOURCE.parent / 'cogs' / 'bot_restart.py').read_text()
    assert 'if allow_update and "--autoupdate" not in relaunch_args:' in source
    assert 'relaunch_args.append("--autoupdate")' in source
