"""Deployment guard: the custom fork must never download the upstream bot.

These tests work without importing main.py (which has startup side effects).
"""
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "main.py"


def test_bootstrap_pinned_to_our_repo():
    source = SOURCE.read_text()
    assert "WOSBOT_REPO = 'GeekDev11/WOSBOT'" in source
    assert "WOSBOT_BRANCH = 'modified'" in source
    assert "_bootstrap_from_main_branch(WOSBOT_ARCHIVE)" in source
    assert "BOT_BOOTSTRAP_URL" not in source
    assert "whiteout-project/bot" not in source
    assert "gitlab.whiteout-bot.com" not in source


def test_no_automatic_source_overwrite_on_container_start():
    source = SOURCE.read_text()
    assert 'if "--autoupdate" in sys.argv or "--repair" in sys.argv:' in source
    assert 'startup.phase_ok("Updates available on startup with --autoupdate")' in source
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
    assert "'tag_name': info['version']" in source
    assert "'source': 'Modified'" in source
    assert "return {'tag_name': f'beta-" not in source
    assert "whiteout-project/bot" not in source


def test_updater_preserves_database_and_secrets():
    source = SOURCE.read_text()
    assert 'Database backup failed:' in source
    assert 'rel.startswith(("db/", ".git/"))' in source
    assert 'rel in ("version", "bot_token.txt", ".env", ".env.local")' in source
    assert '"files": files + ["version"]' in source
    assert '_restore_previous_update("installation failed")' in source


def test_maintenance_update_menu_uses_only_wosbot_branch():
    source = (SOURCE.parent / 'cogs' / 'bot_operations.py').read_text()
    assert "from .wos_versioning import get_latest_info" in source
    assert "latest_version = info['version']" in source
    assert 'whiteout-project/bot/releases/latest' not in source
    assert 'Cannot verify WOSBOT update source. No restart.' in source


def test_maintenance_restart_enables_existing_startup_updater():
    source = (SOURCE.parent / 'cogs' / 'bot_restart.py').read_text()
    assert 'if allow_update and "--autoupdate" not in relaunch_args:' in source
    assert 'relaunch_args.append("--autoupdate")' in source


def test_modified_release_contract():
    versioning = (SOURCE.parent / 'cogs' / 'wos_versioning.py').read_text()
    assert 'BRANCH = "modified"' in versioning
    assert 'version_for_commits(commits_ahead)' in versioning
    assert 'compare/{baseline}...{sha}' in versioning
    assert 'DISPLAY_CHANNEL = "Modified"' in versioning


def test_startup_updater_does_not_require_new_cogs_before_install():
    source = SOURCE.read_text()
    updater = source.split('def get_latest_release_info(', 1)[1].split('def download_requirements_from_release(', 1)[0]
    assert 'from cogs.wos_versioning import' not in updater
    assert "WOSBOT_BRANCH = 'modified'" in source
    assert "'source': 'Modified'" in updater
    assert 'compare/{baseline}...{sha}' in updater


def _load_recovery_helpers():
    """Load only stdlib updater helpers: importing main.py would start the bot."""
    import ast
    import json
    import os
    import shutil
    import sys
    tree = ast.parse(SOURCE.read_text())
    wanted = {
        "_update_recovery_dir", "_update_recovery_state",
        "_restore_previous_update", "_pending_update_start",
        "_confirm_successful_update", "_validate_staged_release",
        "_install_verified_release",
    }
    node_list = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert len(node_list) == len(wanted)
    ns = {"os": os, "shutil": shutil, "sys": sys}
    exec(compile(ast.Module(body=node_list, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


def _make_staged_release(root, main_text="print('working')\n"):
    required = ("main.py", "requirements.txt", "cogs/bot_restart.py",
                "cogs/bot_startup_display.py", "cogs/wos_versioning.py")
    for rel in required:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(main_text if rel == "main.py" else "pass\n")
    return root


def test_rejects_incomplete_and_invalid_staged_code(tmp_path):
    import pytest
    h = _load_recovery_helpers()
    root = tmp_path / "release"
    root.mkdir()
    with pytest.raises(RuntimeError, match="Missing required update file"):
        h["_validate_staged_release"](root)
    _make_staged_release(root, "def bad(:\n")
    with pytest.raises(SyntaxError):
        h["_validate_staged_release"](root)


def test_update_keeps_old_files_until_success_and_can_restore(tmp_path, monkeypatch):
    h = _load_recovery_helpers()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text("print('old')\n")
    (tmp_path / "version").write_text("2.2.10")
    stage = _make_staged_release(tmp_path / "release")
    h["_install_verified_release"](stage, "2.2.11")
    assert (tmp_path / "version").read_text() == "2.2.11"
    assert (tmp_path / "main.py").read_text() == "print('working')\n"
    assert h["_update_recovery_state"]()["attempted"] is False
    assert h["_restore_previous_update"]("test failure")
    assert (tmp_path / "main.py").read_text() == "print('old')\n"
    assert (tmp_path / "version").read_text() == "2.2.10"
    assert not (tmp_path / "cogs" / "wos_versioning.py").exists()
    assert not (tmp_path / ".wos_update_recovery").exists()


def test_successful_startup_clears_recovery_snapshot(tmp_path, monkeypatch):
    h = _load_recovery_helpers()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text("print('old')\n")
    stage = _make_staged_release(tmp_path / "release")
    h["_install_verified_release"](stage, "2.2.11")
    h["_confirm_successful_update"]()
    assert not (tmp_path / ".wos_update_recovery").exists()
    assert (tmp_path / "main.py").read_text() == "print('working')\n"


def test_update_never_replaces_main_before_syntax_validation():
    source = SOURCE.read_text()
    assert 'compile(path.read_bytes(), str(path), "exec")' in source
    assert source.index("_validate_staged_release(update_dir)") < source.index("install_packages(requirements_path,")
    assert source.index("_install_verified_release(update_dir, latest_tag)") < source.index('startup.phase_ok(f"Update completed')


def test_startup_banner_has_no_old_support_invite():
    source = (SOURCE.parent / "cogs" / "bot_startup_display.py").read_text()
    assert 'Need help?' not in source
    assert 'discord.gg/apYByj6K2m' not in source
