# WOSBOT — approved 2.3 release updates

**Repository:** `GeekDev11/WOSBOT`  
**Development branch:** `modified`  
**Update channel shown in Discord:** Modified  
**First new major version:** `2.3.0.0`

## How updates are published

Git commits on `modified` are development changes and do not affect the version seen in Discord. After each push, GitHub Actions runs both **whiteout-stats** (including the full standard suite) and **SimCord UI and callback regression**. A release job can run only if **both** succeeded.

The publisher waits for a 10-minute quiet period, verifies that the latest commit on `modified` is **the exact commit tested by that workflow**, and ensures it hasn't already been published. If another commit arrives, the older workflow cannot publish a stale release. The publisher creates an immutable lightweight tag pointing directly to the tested commit and a non-prerelease GitHub Release.

The first approved release will be `v2.3.0.0`. Subsequent approved publications increment the final component: `v2.3.0.1`, `v2.3.0.2`, etc. The version does **not** change merely because a commit was pushed. A new version is created only when a publish job successfully creates a GitHub release.

**Important:** If either test job fails, there is **no new release**. The developer needs to fix the failing checks and push again. If the publisher cannot write releases because of repository Actions permissions, it fails visibly rather than silently using unapproved commits.

## Bot-side behavior

Both **Check for Updates** and the existing **Update & Restart** action use the same `cogs/wos_versioning.py` release discovery. The bot reads `/releases/latest`, rejects draft, prerelease, invalid and legacy tag formats, resolves the release's immutable Git tag SHA and verifies that it belongs to `modified`.

The updater downloads `https://github.com/GeekDev11/WOSBOT/archive/<approved-commit-sha>.zip`. It never downloads the latest development branch when installing an update. Older `2.2.xxx` installations may upgrade directly to the first approved `2.3.0.0`; a lower or equal `2.3` release is ignored.

For production startup:

```bash
python3 main.py --autoupdate
```

The existing **Update & Restart** button continues to request the updater via restart. The updater checks when the process starts. It does not reboot the bot on every git push.

## Data safeguards

The installer retains its existing database backup before replacing code, staging and Python syntax validation, persistent data/file exclusions (`db/`, `version`, `bot_token.txt`, `.env`, `.env.local`), and rollback of changed application files if startup fails.

The current in-place installer is not a fully atomic deployment or complete database rollback. Back up the persistent `db/` directory and secrets off-host before major deployments. If no release is published yet, keep running the currently installed version rather than falling back to untested commits.

## Actions setup

The existing `.github/workflows/chief-stalker-tests.yml` now includes the release publishing job after the two required test jobs, with `contents: write` access and serialized publisher runs. Repository administrators must allow GitHub Actions to create releases. `GITHUB_TOKEN` is provided by GitHub Actions and is not stored in bot configuration.

The first new release is created **only after this workflow is green**. Check the Actions page to troubleshoot blocked tests or repository release permissions.
