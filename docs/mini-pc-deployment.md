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

## Daily selfie + auto-render

The mobile-first UI assumes the app is opened from an iPhone over Tailscale. Capture works from any browser using the standard `<input type="file" capture="user">` element, which opens the iOS native camera with the front lens — no HTTPS or Tailscale Funnel required. The iPhone uploads HEIC or JPEG; the backend handles both.

The daily auto-render is driven by `selfietl.scheduler.AutoRenderScheduler`, started in the FastAPI lifespan. By default it runs at **03:00 local time** of the mini PC. Settings live at `~/.selfietl/auto_render.json` and can be edited from the **Auto-render** page in the app, or directly on disk:

```json
{
  "enabled": true,
  "time": "03:00",
  "last_run_date": "2026-05-08",
  "last_render_id": 42,
  "render_config": { "resolution": "1080_vertical", "morph_mode": "landmark_delaunay" }
}
```

When the scheduler fires it:

1. Recomputes the canonical face from all included frames.
2. Re-aligns every active photo to the new canonical (`force=True`).
3. Renders the timelapse with the saved render config.

The scheduler records a day as complete only after the MP4 finishes successfully. If the mini PC was offline at the scheduled time, or a build fails, it will catch up after startup and retry later instead of silently skipping the day.

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
