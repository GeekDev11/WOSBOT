# WOSBOT modified-branch refactor tracker

Source baseline: `91bbaa2c6d872caf48d2545ee9582ac3a76f81b9` (2026-11-17).
This is a preservation-first refactor. Keep existing commands, SQLite data, API quotas, scheduling, OCR, redemption and alliance operations.

## Six phases

- [ ] **Phase 1: correctness & security** — Entry path consolidated for `/settings` and `/center` (both confirmed working in Discord on 2.2.219); first-use bootstrap now requires the Discord application owner; both menus are private; Intelligence routes call shared feature handlers instead of raw slash-command callbacks. **Open:** run the full auth-tier matrix, review role grants, verify quotas/entitlements and run CI.
- [ ] **Phase 2: navigation consistency** — Player Sync Back returns to Administration; both command aliases are recognized by stale-menu recovery; Attendance retains navigation when no alliance exists. Regression tests added for Player Sync Back and empty Attendance route. **Open:** audit all nested back buttons, expired components and per-guild/alliance navigation context.
- [ ] **Phase 3: deduplicate UI components** — Removed redundant top-level Export Members, Event Reports and Gift Channels entries (the features remain under Members & Imports, Attendance, and Gift Settings). Removed invisible duplicate button definitions in Alliance Overview, Alliance Settings and Maintenance. **Open:** consolidate gift-management UI and reusable selectors/pagination/confirmation views after test verification.
- [ ] **Phase 4: separate business logic** — Extracted explicit shared handlers for Scout, SvS, Player History and Alliance Intelligence, consumed by slash commands and Command Center. **Open:** split state-management ownership from GiftOperations and isolate remaining business/database logic from views.
- [ ] **Phase 5: remove obsolete code** — Removed known invisible button definitions and duplicate /settings implementation. **Open:** complete reference/reachability audit before removing other legacy views; retain compatibility handlers where needed.
- [ ] **Phase 6: regression testing** — Updated `test_settings_installation.py`, `test_command_center.py`, and `tests/sim/test_hubs.py`; added owner-bootstrap and shared-service regression tests. **Open:** execute tests against this head; no run has yet been verified.

## Required validation

1. `python -m compileall -q cogs`
2. `python -m pytest -q tests/test_settings_installation.py tests/test_command_center.py tests/test_player_sync_menu.py tests/test_wos_leaderboards.py tests/test_wos_player_history.py tests/test_wos_svs.py tests/test_wos_alliance_intel.py`
3. `python -m pytest -q tests`
4. `python -m pytest tests/sim --sim` (requires SimCord)
5. Manually validate all five centers, every retained button, Back/Home, owner/global/server/alliance admin tiers, empty databases, /check and /w, scheduled tasks, and backup/restore.

Do **not** mark a phase complete until its tests and behavior have passed. No production deployment has been verified from this document.
