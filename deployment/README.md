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
./deployment/trip-split stop
```

`start` checks installation, token permissions and the port, loads the server,
waits for local health, then loads the connector. A failed local start unloads
both jobs. Tunnel registration is asynchronous: a loaded process is not proof
that the public route works. `status` distinguishes a stopped job from a loaded
job and shows its local process state. It makes no claim about public health.

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
