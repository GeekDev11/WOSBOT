# WOSBOT-DEV on Wispbyte

**Source of truth:** https://github.com/GeekDev11/WOSBOT  
**Testing branch:** `feature/whiteoutstats-intelligence`

Never deploy the original `whiteout-project/bot` repository to this server.

## Replace an upstream installation

1. Stop WOSBOT-DEV and make a backup of the persistent `db/` directory and any existing `bot_token.txt` file. Keep the backup outside the web-accessible application folder. Never publish either to GitHub.
2. In Wispbyte, set `AUTO_UPDATE=0` if this variable is available, to prevent the panel's startup command from running `git pull` against an old origin.
3. Download and extract the complete WOSBOT test branch (not only `main.py`):
   https://github.com/GeekDev11/WOSBOT/archive/refs/heads/feature/whiteoutstats-intelligence.zip
4. Upload all extracted files into the Wispbyte application root. You should see `main.py`, `requirements.txt`, `cogs/`, and `tests/` side-by-side. Do not overwrite your existing `db/` files.
5. Set `PY_FILE=main.py` and `REQUIREMENTS_FILE=requirements.txt` in Wispbyte Startup, if those variables are available.
6. Add the secret environment variable `BOT_TOKEN` using a **separate test Discord application's** token. Never put a token into GitHub, screenshots, or chat.
7. Run `python3 main.py --no-update` if you can customize the launch command. Even if your host launches `python main.py` without arguments, the custom fork's auto-update check is disabled by default.
8. Check the logs for `Automatic updates disabled (WOSBOT fork)`. Do not continue if the console says `Updating to v2.2.0 (via GitHub)`: it is still executing the upstream version.
9. Use a test Discord server and validate commands only after the integration tests pass.

## When only main.py can be uploaded

The custom WOSBOT `main.py` bootstraps missing modules exclusively from
`GeekDev11/WOSBOT` on the configured test branch. Upload the fork's `main.py`,
**not** the original upstream file. This fallback will not replace existing stale
`cogs/` modules; a full-branch installation is safer.

## Upstream Git remotes

Wispbyte's own `AUTO_UPDATE` performs `git pull` when a `.git` directory exists.
That feature is separate from the Python bot's updater. If an old installation
was cloned from upstream, do not enable it. For later managed updates, use a fresh
clone of this fork with the correct development branch, and verify `git remote -v`.

## Storage and memory

Persist the `db/` folder between restarts. The entire WOSLand dependency set includes
OCR and ONNX packages and may exceed the free plan's RAM or disk. Monitor the panel's
resource usage; do not interpret `server marked as running` as proof of a successful
Discord login.
