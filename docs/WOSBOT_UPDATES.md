# WOSBOT updater — fork-only release channel

**Source of truth:** `GeekDev11/WOSBOT`  
**Pinned channel:** `development`  
**Never:** `main`, `whiteout-project/bot`, or an alternate repository.

## How versions are identified

The existing updater in `main.py` queries the selected branch's latest commit
through GitHub, then downloads the archive of that **exact 40-character commit SHA**.
The installed `version` file contains `wosbot-<12-character-sha>`. Therefore
the downloaded archive cannot change between the check and installation.

## Enable the existing updater

On the paid host, use an application startup that executes:

```bash
python3 main.py --autoupdate
```

If the host has a fixed `PY_FILE=main.py` startup template with no way to append
arguments, configure the hosting startup command or a supported startup wrapper
rather than putting `--autoupdate` in the file name.

**Important:** The built-in updater checks **when the Python process starts**.
Pushing a commit to GitHub does not force an already-running bot to restart.
After a successfully tested push, restart the bot using the hosting panel to
check and install the new version. The updater then calls its restart routine.

For Wispbyte-like panels, set the *host's separate* `AUTO_UPDATE=0` if an old
`.git` origin might point to another repository. The Python flag
`--autoupdate` is independent of that setting.

## Safe release workflow

1. Commit changes to `development`.
2. Wait for GitHub Actions **Whiteout Stats integration tests** to finish green.
3. Back up the persistent `db/` directory and application secrets to off-host
   storage; confirm backups can be restored.
4. Restart the host application with `--autoupdate`.
5. Check console output for `GeekDev11/WOSBOT@development`
   and an installed `wosbot-` commit version.
6. Run a test Discord command and check the Player Sync logs.

The updater preserves `db/`, `bot_token.txt`, `.env`, `.env.local` and
`version` inside downloaded archives. It aborts if its database backup fails.

## Limitations

- The existing updater is an in-place file installer. It is **not** a transactionally
  atomic deployment with automatic rollback. Save verified backups before updating.
- It does not automatically wait for CI checks or verify GitHub Actions results.
  Approve only commits whose checks succeeded.
- The updater keeps the production bot running the **specified development branch**
  on purpose; do not switch it to `main` unless you explicitly decide to.
- `--repair` forces a reinstall from the same selected WOSBOT branch.
- To disable startup updates, omit `--autoupdate` or use `--no-update`.
