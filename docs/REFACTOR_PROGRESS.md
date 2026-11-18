# WOSBOT modified-branch refactor tracker

Source baseline: `91bbaa2c6d872caf48d2545ee9582ac3a76f81b9` (2026-11-17).
This is a preservation-first refactor. Keep existing commands, SQLite data, API quotas, scheduling, OCR, redemption and alliance operations.

## Six phases

- [ ] **Phase 1: correctness & security** — Entry path consolidated for `/settings` and `/center` (both confirmed working in Discord on 2.2.219); first-use bootstrap now requires the Discord application owner; both menus are private; Intelligence routes call shared feature handlers instead of raw slash-command callbacks. **New:** Manage Alliances now resolves the highest personal/Discord-role tier within the current guild, with focused regression cases for global/server/alliance role tiers, owner precedence, and cross-guild isolation. **Open:** run the full auth-tier matrix and verify API quotas/entitlements and CI.
- [ ] **Phase 2: navigation consistency** — Player Sync Back returns to Administration; both command aliases are recognized by stale-menu recovery; Attendance retains navigation when no alliance exists. Regression tests added for Player Sync Back and empty Attendance route. The Alliance History picker now returns to Rankings & History; Self-Registration and Member States return to Manage Alliances; Permissions returns to Administration. Corresponding regression tests were added. These new navigation tests still need execution. Additional Phase 2 changes: Alliance Channel Setup Back now returns to Alliance Configuration, Bot Profile and Themes Back return to Appearance, the Themes Back button is relabeled accordingly, and Alliance Configuration's Back label now matches its Alliance Overview destination. Added focused regression coverage for Channel Setup, Bot Profile and Themes navigation. Also corrected tests to call the actual decorated Discord button callbacks. User-confirmed /settings and /center work, but new nested routes and updated tests are NOT live-verified. Player Sync remains under Administration & Settings. **New:** Member States Back now acknowledges the interaction with an ephemeral recovery message if the MainMenu cog is missing, rather than silently leaving the click unanswered; both normal and unavailable-parent routes have regression coverage. **Open:** audit remaining nested back buttons, expired components and per-guild/alliance navigation context.
- [ ] **Phase 3: deduplicate UI components** — Removed redundant top-level Export Members, Event Reports and Gift Channels entries (the features remain under Members & Imports, Attendance, and Gift Settings). Removed invisible duplicate button definitions in Alliance Overview, Alliance Settings and Maintenance. **New:** Removed two further dead, runtime-hidden Manage Alliances button definitions (Export Members and Player Sync) and their unused permission gates, retaining the feature handlers and their canonical destinations. **New:** Legacy `GiftOperations.show_gift_menu()` now opens the canonical Gift Code Center, replacing its second visible dashboard; all existing redemption, code CRUD, results, settings and channel handlers remain. `GiftCodeManagementView` now explains their real locations. Gift add/delete permission gates use `PermissionManager` (including role grants) instead of raw SQL. Added regression cases for the old menu entry, role-granted code creation, and Global-Admin-only deletion. **New:** Active Gift Code listing now paginates 15 codes per embed instead of attempting unbounded embed fields (Discord's limit is 25). Previous/Next and Back to Gift Center are included, and tests cover 42-code and 18-code cases. **Open:** review redemption and delete code selector pagination (both still limit visible options), reusable selectors and confirmation views, and run tests before declaring this complete.
- [ ] **Phase 4: separate business logic** — Extracted explicit shared handlers for Scout, SvS, Player History and Alliance Intelligence, consumed by slash commands and Command Center. **New:** Introduced `cogs/member_state_service.py` as the Alliance Member States entry and orchestration boundary. Manage Alliances no longer routes its state-management button through the `GiftOperations` menu method. Single/bulk state persistence plus missed-code catch-up moved out of modal callbacks into a service, preserving the same save-then-enqueue order and the existing scan/redemption engine. `GiftOperations.show_state_management()` remains as a legacy compatibility alias. Added scoped tests for entry routing, unavailable engine, legacy method, and single/bulk catch-up ordering. **Open:** execute the tests; continue separating additional view logic only when parity can be shown. Do not modify working Gift Code redemption or its menus.
- [ ] **Phase 5: remove obsolete code** — Removed known invisible button definitions and duplicate /settings implementation. **Open:** complete reference/reachability audit before removing other legacy views; retain compatibility handlers where needed.
- [ ] **Phase 6: regression testing** — Updated `test_settings_installation.py`, `test_command_center.py`, and `tests/sim/test_hubs.py`; added owner-bootstrap and shared-service regression tests. **New:** scanned all 70 cog Python files for invalid backtick escapes; fixed all eight observed occurrences across the two troop-quality modules, added a guard test, and added role-tier tests. **New:** Extended the existing `.github/workflows/chief-stalker-tests.yml` syntax-validation and focused-test commands to cover `cogs/member_state_service.py`, `cogs/alliance_member_states.py`, and the new `tests/test_member_states_menu.py` cases, plus gift-validation persistence regressions. Pull requests touching these files now also match the workflow's path filter. **Open:** execute tests against this head; no successful run has yet been verified (latest observed Actions run remains on a 2026-11-16 commit).

## First successful execution of latest-branch focused suite (2026-11-18)

- GitHub Actions run [#124](https://github.com/GeekDev11/WOSBOT/actions/runs/44311437828) targeted `modified` commit `c2cb1c9`. Syntax validation and dependency installation completed; focused pytest reported **596 passed, 2 failed, 1 warning**.
- Both failures were stale source-string test expectations, not asserted business-operation mismatches:
  - `tests/test_bot_profile.py::test_profile_menu_is_global_admin_only` expected a removed Bot Profile button in `bot_main_menu.py`. Updated test to verify Appearance routes to `BotProfile.show_profile` and to exercise the actual global-admin gate.
  - `tests/test_settings_installation.py::test_settings_and_center_use_same_private_entry` expected a direct `PermissionManager.add_admin(...)` call. Updated to assert the existing owner registration remains inside `asyncio.to_thread`.
- **New test changes still need a fresh CI run on `modified`**. The 596 passing tests apply to commit `c2cb1c9`, not later test-only corrections.
- No Gift Code feature, API, queue, database schema or user-facing menus were changed to address these failures.

## Regression and role-guild isolation audit (2026-11-18)

- **Confirmed security issue fixed:** guild-aware `PermissionManager.get_admin_alliance_ids()`, `get_admin_alliances()`, and `get_admin_users()` previously accepted a cached Global or Alliance role grant from another Discord guild. Guild-aware access now accepts only the cache whose guild matches the request. Personal Global Admin retains its existing global scope.
- Added regression tests in `tests/test_permission_role_grants.py` for cross-server Global/Alliance grants, permitted current-server role access, and unchanged personal global access.
- Extended the existing CI workflow to include syntax checks for `permission_handler.py` and run the role-permission tests. The workflow already includes Member States and Command Center tests.
- **Pending verification:** the connected GitHub interface cannot dispatch a new Actions workflow; the model's container cannot resolve github.com to clone the repository. No passing CI run has been observed at the current `modified` head. Do not consider Phase 1, Phase 4, or Phase 6 verified until tests run.
- **Further security review:** guild-blind `is_admin()` is used in other legacy callback gates; audit each guild-sensitive action before any blanket change, preserving personal Global Admin capabilities and role permission semantics.
- Working Gift Code redemption logic and UI have not been changed during this pass.

## Phase 4 follow-up — Alliance SQLite UI reads (2026-11-18)

- Moved Manage Alliances' effective-tier lookup, scoped-alliance lookup, member counts, and missing/wrong-state counts into `asyncio.to_thread`, avoiding synchronous SQLite operations on the Discord event loop. Preserved its query predicates and displayed totals.
- Extracted a read-only `MainMenu._alliance_settings_snapshot` helper for Alliance Settings. Alliance configuration, auto-removal settings, and COUNT/AVG/MAX member aggregates now load off-thread with fresh SQLite connections.
- Added `tests/test_command_center.py` fixtures for empty/multi-alliance summary counts, normal/empty/missing configuration records, and explicit off-thread regression guards.
- No database writes, permission grants, Gift Code engine, redemption scheduler, or Gift Code menus were changed in this follow-up.
- **Verification pending:** CI still has not reported a run for the latest modified-branch commit; verify the focused tests and live Discord behavior before closing Phase 4.

## Gift-code expiration and history audit (2026-11-18)

- Confirmed: `GiftOperations.periodic_validation_loop` runs every two hours and checks up to 20 pending/validated codes per run using the game API. Conclusive code-level invalid responses mark codes `invalid`.
- Confirmed: per-member redemption and manual Gift Code selection exclude `validation_status = 'invalid'`. Pending codes may still be presented; this is intentional existing behavior while validation finishes.
- Fixed data-loss bug: legacy daily `cleanup_old_invalid_codes` deleted invalid code rows older than seven days **and** deleted their `user_giftcodes` outcomes. It is now non-destructive and retains both invalid code status and historical outcomes. Existing records already deleted cannot be recovered by this change.
- Added tests in `tests/test_gift_validation_persistence.py` to guard the preserved rows and invalid-code exclusion.
- Out of scope/unchanged: manual administrator deletion, periodic API validation pacing and quotas, redemption priority/queue, and the gift-history UI's 25-code selection limit. No age-only invalidation or new UI pagination was added.

**Verification gate:** run `python -m pytest -q tests/test_gift_validation_persistence.py`, plus gift redemption and history suites, before marking the retention fix verified. GitHub Actions has not yet shown a run at this revision.

## Required validation

1. `python -m compileall -q cogs`
2. `python -m pytest -q tests/test_settings_installation.py tests/test_command_center.py tests/test_player_sync_menu.py tests/test_wos_leaderboards.py tests/test_wos_player_history.py tests/test_wos_svs.py tests/test_wos_alliance_intel.py`
3. `python -m pytest -q tests`
4. `python -m pytest tests/sim --sim` (requires SimCord)
5. Manually validate all five centers, every retained button, Back/Home, owner/global/server/alliance admin tiers, empty databases, /check and /w, scheduled tasks, and backup/restore.

Do **not** mark a phase complete until its tests and behavior have passed. No production deployment has been verified from this document.
