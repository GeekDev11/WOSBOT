# WOSBOT updater — fork-only release channel

**Source of truth:** `GeekDev11/WOSBOT`  
**Update channel:** `Modified` (`modified` branch)  
**Never:** `main`, `whiteout-project/bot`, or an alternate repository.

## How versions are identified

WOSBOT uses human-readable versions in the form `2.2.01`, `2.2.02`,
`2.2.03` and so on. The last component increments for every new commit
on `modified`, including bug fixes and feature changes.

The starting baseline is commit `04dfa0e4a174515a61ab537f05d68ed2f354719c`.
The updater asks GitHub how many commits the current release is ahead of
that baseline. No separate version-bump commit is required. If the branch
is rebased or the baseline stops being its ancestor, the updater refuses
to apply the release rather than guessing a version.

The exact 40-character latest commit SHA is still used internally to download
an immutable archive, while the console and Discord display the numeric version.

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

1. Commit changes to `modified`.
2. Wait for GitHub Actions **Whiteout Stats integration tests** to finish green.
3. Back up the persistent `db/` directory and application secrets to off-host
   storage; confirm backups can be restored.
4. Restart the host application with `--autoupdate`.
5. Check console output for `Modified`
   and an installed numeric `2.2.xx` version.
6. Run a test Discord command and check the Player Sync logs.

The updater preserves `db/`, `bot_token.txt`, `.env`, `.env.local` and
`version` inside downloaded archives. It aborts if its database backup fails.

## Limitations

- The existing updater is an in-place file installer. It is **not** a transactionally
  atomic deployment with automatic rollback. Save verified backups before updating.
- It does not automatically wait for CI checks or verify GitHub Actions results.
  Approve only commits whose checks succeeded.
- The updater keeps the production bot running the **specified modified branch**
  on purpose; do not switch it to `main` unless you explicitly decide to.
- `--repair` forces a reinstall from the same selected WOSBOT branch.
- To disable startup updates, omit `--autoupdate` or use `--no-update`.
