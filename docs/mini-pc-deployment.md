# Mini PC Deployment

SelfieTL is deployed permanently on the mini PC over Tailscale.

- Service name: `selfietl.service`
- Host: `100.96.182.111`
- Local service port: `127.0.0.1:8766`
- Tailnet URL: `https://davis-mini-pc-1.tail59b3f5.ts.net/selfietl/`
- Working directory: `/home/davis/selfietl`
- Data directory: `/home/davis/.selfietl`
- Env file: `/home/davis/.config/selfietl/selfietl.env`

The app is managed as a system systemd service, similar to `drive-web.service`, and runs as user `davis`.

System packages required by the full detector stack:

```bash
sudo apt-get install -y libegl1 libgles2
```

MediaPipe can import without these packages, but face landmarking fails at runtime without `libGLESv2.so.2`; the app then falls back to OpenCV, which can detect a face box but cannot save the detailed face map.

Useful commands on the mini PC:

```bash
sudo systemctl status selfietl.service
sudo systemctl restart selfietl.service
sudo journalctl -u selfietl.service -f
```

Deployment command:

```bash
cd /home/davis/selfietl/web
npm ci
npm run build
cd /home/davis/selfietl
sudo systemctl restart selfietl.service
```

The systemd service binds to `127.0.0.1:8766`. Tailscale Serve points `/` at the local Caddy portal on `127.0.0.1:8700`; Caddy routes `/selfietl/*`, `/assets/*`, and SelfieTL API requests to `127.0.0.1:8766` while leaving the other mini PC apps in place.

## Safari cannot establish a secure connection

The normal SelfieTL URL is private to the Tailscale network. Use the full HTTPS hostname above with Tailscale connected and **Use Tailscale DNS settings** enabled on the client.

Other services on this machine use Funnel on separate ports. That can make the hostname's public DNS point at a Funnel relay, while SelfieTL on port 443 remains private. A connected Tailscale client can still fail in Safari if its DNS lookup or browser traffic bypasses the private route. The public route then fails during TLS, before any request reaches SelfieTL.

Check the private endpoint with normal certificate verification:

```bash
curl --resolve davis-mini-pc-1.tail59b3f5.ts.net:443:100.96.182.111 \
  https://davis-mini-pc-1.tail59b3f5.ts.net/selfietl/api/health
```

If this succeeds while Safari fails, check the phone's Tailscale DNS setting, reconnect Tailscale, and retry the HTTPS URL in a new tab. If necessary, check whether another VPN, DNS profile, or Safari Private Relay is changing the path. Compare the certificate and server response before restarting the app. Enabling public Funnel for the whole portal changes access to personal data and is not a substitute for repairing private connectivity.

## Daily selfie + auto-render

The mobile-first UI assumes the app is opened from an iPhone over Tailscale using the HTTPS URL. Capture uses the standard `<input type="file" capture="user">` element, which opens the iOS native camera with the front lens. The iPhone uploads HEIC or JPEG; the backend handles both.

The daily auto-render is driven by `selfietl.scheduler.AutoRenderScheduler`, started in the FastAPI lifespan. By default it runs at **03:00 local time** of the mini PC. Settings live at `~/.selfietl/auto_render.json` and can be edited from the **Auto-render** page in the app, or directly on disk:

```json
{
  "enabled": true,
  "time": "03:00",
  "last_run_date": "2026-05-08",
  "last_checked_date": "2026-05-09",
  "last_render_id": 42,
  "last_render_signature": "a3f...",
  "render_config": { "resolution": "1080_vertical", "morph_mode": "landmark_delaunay" }
}
```

When the scheduler fires it first fingerprints the active photo set, the saved render config, and the alignment settings. If that fingerprint matches the last successful auto-render, the check is recorded and no duplicate MP4 is created. When inputs have changed, it:

1. Recomputes the canonical face from all included frames.
2. Re-aligns every active photo to the new canonical (`force=True`).
3. Renders the timelapse with the saved render config.

The scheduler records a render as successful only after the MP4 finishes. It records unchanged-input checks separately so the nightly loop waits until the next day without implying a new video was produced. If the mini PC was offline at the scheduled time, or a build fails, it will catch up after startup and retry later instead of silently skipping changed inputs.

Each project keeps one completed timelapse, one range preview, and one Hair timeline. A successful render atomically replaces the prior video of the same kind; older render records remain without links to deleted files. Quick previews and playback copies use replaceable project-scoped paths.

Trigger an immediate render from the UI's *Render now* button or via:

```bash
curl -X POST https://davis-mini-pc-1.tail59b3f5.ts.net/selfietl/api/auto-render/run
```

Set the time without opening the UI:

```bash
curl -X PATCH https://davis-mini-pc-1.tail59b3f5.ts.net/selfietl/api/auto-render \
  -H 'Content-Type: application/json' \
  -d '{"time": "03:30", "enabled": true}'
```

## Add to Home Screen on iPhone

1. Open `https://davis-mini-pc-1.tail59b3f5.ts.net/selfietl/` in Safari over Tailscale.
2. Tap the share sheet → **Add to Home Screen**.
3. Launch from the icon: it opens full-screen with the bottom tab bar (Today / Timeline / Capture / Video) and saves Today as the default page.

The PWA assets live under `web/public/`:

- `manifest.webmanifest`
- `icon.svg`, `icon-192.png`, `icon-512.png`, `apple-touch-icon.png`
- `sw.js` (caches the app shell; bypasses `/api/*` so data stays fresh)

## Inbox layout

Captured selfies are written into `~/.selfietl/inbox/selfie_YYYY-MM-DD_HHMMSS.<ext>`. The single-photo pipeline (`selfietl.pipeline.single`) ingests, detects, and aligns each capture inline so the auto-render at 3 AM has nothing left to do except recompute the canonical and assemble the video.

To watch the daily pipeline live:

```bash
sudo journalctl -u selfietl.service -f | grep -E 'auto_render|capture'
```

## Independent remote access

Open `https://davis-mini-pc-1.tail59b3f5.ts.net:10000/selfietl/`. This is a public HTTPS route protected by SelfieTL's access code. It works without a Tailscale client or private DNS. The mini PC provides TLS through Tailscale Funnel; SelfieTL has a separate route and authentication gateway. It uses no domain, Cloudflare tunnel, authentication, or application service from another project.

The gateway's user service is `selfietl-remote-access.service`, listening only on `127.0.0.1:8777` and forwarding to the SelfieTL backend on `127.0.0.1:8766`. Its unit template is in `deploy/selfietl-remote-access.service`. Enable user lingering so the gateway starts at boot. The original private Tailscale endpoint remains available to the native client.

Add only SelfieTL's Funnel mount; retain existing routes:

```bash
tailscale funnel --bg --https=10000 --set-path=/selfietl/ http://127.0.0.1:8777
```

Funnel removes the mount prefix before proxying. Configure the gateway's `--public-origin` with the full public `/selfietl/` address so the login form and session cookie stay within that route. The `--bg` configuration persists across reboot. Do not expose port `8766` through Funnel.

The gateway checks authentication for API, images, and video. Its signing key, access-code override, and consumed login tickets stay in the SelfieTL data directory with private permissions. Login links use short-lived, single-use tickets in the URL fragment. Sessions use a Secure, HttpOnly, host-only cookie scoped to `/selfietl/`, and state-changing requests check the origin. Browser responses do not cache private data. The gateway permits at most five sign-in attempts per minute; existing authenticated sessions continue normally.

Issue a private sign-in link on the mini PC:

```bash
cd /home/davis/selfietl
.venv/bin/python -m selfietl.remote_access issue-link \
  --public-origin https://davis-mini-pc-1.tail59b3f5.ts.net:10000/selfietl/ \
  --data-dir /home/davis/.selfietl
```

Use `show-code` in place of `issue-link` to retrieve the owner's configured access code locally. Keep access codes, login links, cookies, and signing keys out of source control and public reports.
