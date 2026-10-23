# Manually serve Trip Split through Cloudflare

## Minimal design

Public access, no app accounts, no Cloudflare Access policy, no Docker, nginx,
database, paid hosting or custom process supervisor. Only two running processes:

```text
Browser → Cloudflare HTTPS → cloudflared → Waitress/Flask at 127.0.0.1:8000
```

macOS launchd supervises both only after you manually start them. The plist files
live in the private app runtime, NOT in any LaunchAgents/LaunchDaemons directory.
`start` explicitly bootstraps the jobs into your current GUI session; KeepAlive
restarts exited processes with a 10-second throttle. `stop` bootouts both jobs,
removing supervision rather than merely killing a process that would restart.
They remain off after stop, logout or reboot until your next manual start. Closing
the terminal does not stop them. There are no login items or boot services.

These are current-user services: the Mac must remain on, connected and awake,
and your login session must remain active. Cloudflare does not host the Python
app or make an offline Mac available.

## One-time preparation

1. [Install the app](../installation/README.md):

   ```sh
   python3 installation/install.py
   ```

2. Install the official connector:

   ```sh
   brew install cloudflared
   ```

   Use a current version; token-file support requires 2025.4.0 or later.
   Do NOT run `cloudflared service install` or `brew services start cloudflared`:
   those are not the manual-only supervision model used here.
   [Download instructions](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/),
   [token-file support](https://developers.cloudflare.com/tunnel/reference/run-parameters/).

3. In the Cloudflare dashboard, verify the zone is on **Free**, create a
   dashboard-managed named tunnel such as `boboji-mac`, and add a published
   application route:

   - Hostname: `split.boboji.fyi`
   - Service: `http://127.0.0.1:8000`
   - HTTP Host Header: `split.boboji.fyi`

   Map only this hostname, not the apex or a wildcard. Keep TLS at the Cloudflare
   edge and the encrypted tunnel; the last hop is HTTP over loopback on this Mac.
   Enable HTTPS redirection for this hostname using a free rule if needed; don't
   change site-wide behavior for other boboji.fyi apps without checking them.
   The dashboard creates the hostname's DNS route. It does not require router
   forwarding or opening the app's port to the LAN.
   [Tunnel setup](https://developers.cloudflare.com/tunnel/get-started/),
   [origin Host header](https://developers.cloudflare.com/tunnel/reference/origin-parameters/).

4. Copy only the tunnel token from the dashboard and store it through a hidden
   terminal prompt:

   ```sh
   ./deployment/trip-split tunnel
   ```

   This does not start the tunnel. Do not paste the installation command into
   the prompt, put a token in shell arguments/history, commit it, or send it in
   chat. The connector receives a private token-file path, never the secret in
   its arguments or plist. Anyone with the token can run that tunnel; rotate it
   in Cloudflare if exposed. Keep account-wide API credentials out of this app.
   [Tunnel-token security](https://developers.cloudflare.com/tunnel/reference/tunnel-tokens/).

## Your everyday commands

From the repo root:

```sh
./deployment/trip-split start
./deployment/trip-split status
./deployment/trip-split check
./deployment/trip-split check --public
./deployment/trip-split stop
```

`start` checks installation, token permissions and the port, loads the server,
waits for local health, then loads the connector. A failed local start unloads
both jobs. Tunnel registration is asynchronous: a loaded process is not proof
that the public route works. `status` distinguishes a stopped job from a loaded
job and shows its local process state, PID, executable name and configured server port:

```text
server: state = running; PID: 12345; process: Python; automatic restart enabled
tunnel: state = running; PID: 12346; process: cloudflared; automatic restart enabled
Server port: 8000 (configured; http://127.0.0.1:8000/)
```

`check` verifies the local page, frontend, health and a fixed example calculation
with manual FX rates. `check --public` runs the same checks through the configured
HTTPS hostname with normal TLS validation. Failures exit nonzero.

The launcher uses the installed `manage.py`, so repository edits alone do not
update its commands. For a full app update, stop, rerun the installer, then start.

PIDs change after a restart. A loaded job waiting to restart has no current PID;
an unloaded job shows `stopped (not loaded)`. The name may be `unavailable` if
the process exits during the check or cannot be inspected. Status never prints
command arguments or credentials, and makes no claim about public health.

The current foreground preview also uses port 8000. Stop that preview before
starting the installed app. The controller will not kill an unrelated process
or silently pick a different port. To change the port, stop the jobs, edit the
private `settings.json`, and update the tunnel's service URL to match.

To restart intentionally: run `stop`, then `start`. To change the token: stop,
run `tunnel`, then start. Ordinary shutdown keeps files and credentials for your
next manual start; it does not delete the tunnel or DNS record. The public URL
will be unavailable while the app is stopped. Updates are manual; no background
auto-updater or automatic billing upgrade is configured.

## Stay on the free tier

Use Cloudflare's Free zone plan and the standard named HTTP tunnel. Do not enable
Argo Smart Routing, Load Balancing, Advanced Rate Limiting, Workers Paid, or other
paid add-ons. The scripts never call billing APIs or create paid resources;
confirm the dashboard's plan and charges yourself. Domain renewal, electricity
and your internet connection remain separate costs.

Cloudflare's default DDoS protection is enabled on Free zones. That is not an
unlimited-capacity guarantee for the Mac: valid traffic and undetected abuse can
still reach the origin. Keep the app's 64 KB note limit and bounded connections.
Only two calculations run concurrently; additional requests receive HTTP 503
with Retry-After rather than growing an unbounded application work queue.
[DDoS defaults](https://developers.cloudflare.com/ddos-protection/get-started/).

As of October 9, 2026, Free includes one rate-limiting rule with per-IP counting,
a 10-second window and a 10-second mitigation period. Use the available free rule
for path `/api/calculate`, initially 10 requests per 10 seconds with Block. Check
existing rules first: this allowance is shared across the zone. Free rule matching
does not support hostname or method, so the same path on other subdomains is
affected. Do not purchase extra rules to work around that. Edge counters are not
global or exact; the origin busy safeguard remains useful.
[Free rate-limiting limits](https://developers.cloudflare.com/waf/rate-limiting-rules/).

## Public-repository and runtime security

- Serve only the packaged frontend; never serve the checkout, private runtime,
  logs, token file or a directory listing.
- Bind the app and connector metrics to loopback. Don't open router ports, bind
  to `0.0.0.0`, disable TLS validation, enable Flask debug, or run as root.
- Preserve hostname and same-origin validation. The installed serving profile
  trusts only X-Forwarded-Proto from the local connector through Waitress. It
  does not blindly trust forwarded Host/IP values or double-wrap ProxyFix.
- Source code and hostname are public, but credentials and runtime data stay
  outside Git. Ignore rules are a backup, not a substitute for reviewing a diff.
  The installer verifies existing runtime paths are private and rejects symlinks
  for generated credential/control files.
- Requests are isolated; there is no shared stored ledger or public history.
  Raw notes travel through Cloudflare to this Mac over HTTPS; Cloudflare is a
  reverse proxy, not end-to-end private storage. Rate lookup sends only currencies
  and dates onward to the FX provider. Each calculation fetches each missing
  currency pair once; no FX cache is read or written. Supply manual rates for
  calculations that need to work without the provider.
- `server.log` rotates; startup and tunnel logs are private. Tunnel logging is
  error-only. Inspect log sizes
  periodically. Avoid debug logging and never log request bodies or credentials.
- Public availability is not a security guarantee. Keep macOS, Python, app
  dependencies and cloudflared patched. Anyone who can execute code as your Mac
  user can read your token; file permissions do not protect against that.

## Verify before considering it deployed

Check local `/health`, then public `https://split.boboji.fyi`, its assets, a full
example calculation and audit download. Confirm unrelated hosts and origins are
rejected. Verify automatic restart by terminating an owned managed process and
checking status afterward; verify manual stop keeps both jobs unloaded. Verify
logout/reboot leaves them stopped until manual start. A public check must come
from outside the Mac's local session as well; never equate local health with
tunnel reachability.

## One-command deployment

After the one-time connector/tunnel-token setup, run from the checkout:

```sh
./deployment/trip-split deploy
```

This stops both serving jobs, installs the current checkout, starts the app and
connector, then runs the local health/frontend/calculation check. Update the
checkout separately with `git pull`. Public verification remains explicit:

```sh
./deployment/trip-split check --public
```

Deployment preserves private settings, the tunnel token and existing user data.
Stop waits up to 20 seconds per job for launchd to remove its label before
installation continues. A failed stop prevents installation. A failed installation leaves the app stopped
and marked incomplete; a failed start/final check unloads both jobs. Cleanup errors
are reported explicitly. There is no automatic rollback. Rerun a successful
installation/deployment before starting after an interrupted install. The separate
installer and `start`, `stop`, `status`, `tunnel` and `check` commands remain.
Nothing is installed for login/boot startup.

## Optional BoboWatch monitoring

The installed app can send HTTP metrics to the same BoboWatch dashboard as
Flightrepo. It uses an authenticated ingestion API, never a shared SQLite file.
Only eight standard-library producer/configuration modules from the pinned
`vendor/bobowatch` Git submodule enter the app wheel. The BoboWatch server, SQL
storage and dashboard are not installed into Trip Split. The wheel uses Hatchling
(the same build backend as Flightrepo) to bundle that explicit file list and all
frontend assets; no monitoring runtime dependency is downloaded with pip.

After installing a real (non-demo) BoboWatch runtime, deploy from Trip Split:

```sh
cd ~/p/trip_dollar
git switch main
git pull --ff-only
./deployment/trip-split deploy \
  --bobowatch-runtime-dir "$HOME/Library/Application Support/fyi.boboji.watch"
./deployment/trip-split check --public
```

Deployment initializes the exact recorded submodule revision before stopping the
app. Installation does not read BoboWatch configuration or register a producer.
At startup the installed adapter registers project `trip-dollar`, site
`trip-split-production`, a stable hostname-derived host ID and a distinct
`trip-split-…-r01` replica. It creates/reuses a private producer token automatically.
No copying/pasting monitoring tokens is required. The BoboWatch process can be
stopped during registration; neither its API nor its database is opened. The
existing receiver reloads registrations while serving. Start BoboWatch separately
whenever you want to view/collect history; Trip Split never starts it.

Trip Split always reads its own settings from
`~/Library/Application Support/fyi.boboji.split/settings.json`. The selected
BoboWatch runtime path and stable IDs are saved there as
`monitoring_registration`; API endpoint, token-file path and interval are saved
as `monitoring`. The token itself lives in `tokens/bobowatch.token`, outside Git.
The endpoint comes from the selected runtime's `monitor.toml` (same-host loopback).
Ordinary subsequent `deploy`/`start` commands reuse an enabled saved registration.
Using no target on a never-enabled installation leaves monitoring off. Explicitly
set `monitoring.enabled` to `false` to disable delivery; it remains disabled on
subsequent starts unless you explicitly select a BoboWatch runtime again.

For separate installation and startup:

```sh
./deployment/trip-split stop
python3 installation/install.py
./deployment/trip-split start \
  --bobowatch-runtime-dir "$HOME/Library/Application Support/fyi.boboji.watch"
```

An already-enabled sender reloads endpoint, token path, interval and enabled state
from its settings in the background. Re-register against a changed local receiver
configuration without restarting Trip Split:

```sh
./deployment/trip-split monitoring \
  --bobowatch-runtime-dir "$HOME/Library/Application Support/fyi.boboji.watch"
```

Initially enabling monitoring on an app launched with it disabled requires a
stop/start or deployment so middleware is installed. Changing replica identity
also requires a restart. Invalid live edits retain the last valid target.

Delivery is asynchronous with bounded in-memory counters/events and retry/backoff.
A stopped or unavailable BoboWatch does not block calculations. Aggregate counters
survive receiver outages while this app process remains alive; detailed outage
history is not reconstructed, and unsent counters are lost if the app restarts.
BoboWatch history remains subject to its RAM-only storage and retention quota.

Metrics cover requests/rate, status classes, errors, latency histograms, active
requests, known body sizes and fixed route templates (`/`, `/health`,
`/api/calculate`, `/static/<path:filename>` plus `unmatched`). They include Flask
host validation failures, payload rejections and busy-calculator 503s. Requests
rejected earlier by Waitress or Cloudflare are not observable by this middleware.
Body sizes exclude headers/network overhead; unread bodies and interrupted
responses remain unsized. Expense notes, participant names, amounts, FX values,
request/response bodies, query strings and client addresses are never exported.
Preflight `--check` and installation do not start a sender; regular local/public
checks against the running app are ordinary measured requests.

The normal package install/CLI stays usable without Flask, Waitress or an enabled
monitor. For development, initialize the pinned source before installing:

```sh
git submodule update --init vendor/bobowatch
python3 -m pip install -e '.[dev]'
python3 -m pytest -q
```
