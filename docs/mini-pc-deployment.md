# Mini PC Deployment

SelfieTL is deployed permanently on the mini PC over Tailscale.

- Service name: `selfietl.service`
- Host: `100.96.182.111`
- Port: `8766`
- URL: `http://100.96.182.111:8766`
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
/home/davis/selfietl/.venv/bin/python -m selfietl serve --host 0.0.0.0 --port 8766 --data-dir /home/davis/.selfietl
```

Tailscale Serve is intentionally left unchanged because `/` currently routes to Drive Web on port `8730`.

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

Trigger an immediate render from the UI's *Render now* button or via:

```bash
curl -X POST http://100.96.182.111:8766/api/auto-render/run
```

Set the time without opening the UI:

```bash
curl -X PATCH http://100.96.182.111:8766/api/auto-render \
  -H 'Content-Type: application/json' \
  -d '{"time": "03:30", "enabled": true}'
```

## Add to Home Screen on iPhone

1. Open `http://100.96.182.111:8766` in Safari over Tailscale.
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
