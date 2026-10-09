# Install Trip Split on this Mac

This installs an app snapshot. It does not start a server, register a login item,
create a boot service, change DNS, or configure a paid Cloudflare product.

From the repository root, as your normal user:

```sh
python3 installation/install.py
```

Use an existing Python 3.11+ installation with pip. Python 3.13 is the CI version;
the installer uses whichever supported Python you invoke. No sudo is needed.

The installer:

1. Refuses to update while either managed job is loaded.
2. Creates a private runtime at `~/Library/Application Support/Trip Split`.
3. Builds a wheel from this checkout and installs it non-editably with the `web`
   dependencies into a dedicated virtual environment. Build/runtime dependencies
   are downloaded with pip; no development or test dependencies are installed.
4. Copies the serving/control scripts, creates non-secret settings and checks
   the installed app and its frontend assets without listening on a port.
5. Records the source commit, whether the checkout was dirty, the wheel SHA-256
   and installed dependency versions in `installation.json`.

The runtime contains:

```text
Trip Split/
├── venv/                 Installed Python app and dependencies
├── manage.py             Manual control
├── serve.py              Loopback server configuration
├── settings.json         Hostname and port; no credentials
├── installation.json     Installation receipt
├── tunnel-token          Added later with a hidden prompt
├── jobs/                 Manually loaded plist files, not login items
└── logs/                 Private operational logs
```

The runtime directory is owner-only (0700); generated settings, receipts, job
files and tokens are owner-only (0600). Nothing sensitive belongs in the repo.
Editing or switching the Git checkout does not change the running app snapshot.
There is no FX cache or state directory in a new installation. Old cache files
from an earlier version are ignored and left untouched when updating.

For an update, run `./deployment/trip-split stop`, check out the intended code,
and run the installer again. The same runtime and token are retained. Then start
explicitly. Failed installation checks leave the service stopped: fix the issue
and rerun the installer before restarting. Updates are not atomic rollbacks.

Continue with the [deployment plan](../deployment/README.md).
