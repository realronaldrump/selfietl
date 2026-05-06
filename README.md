# SelfieTL

SelfieTL is a local-first web app for turning a folder of selfies into an anchored, morphed timelapse video. The backend catalogs original files without modifying them, caches face landmarks by file hash, aligns full-resolution frames, and assembles exports with FFmpeg.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[full,dev]"

cd web
npm install
npm run build
cd ..

python -m selfietl serve
```

Open [http://localhost:8765](http://localhost:8765).

For a lighter API/test-only install without MediaPipe, OpenCV, HEIC, skimage, or librosa:

```bash
pip install -e ".[dev]"
```

If MediaPipe is unavailable, the detector falls back to OpenCV Haar detection when OpenCV is installed. If neither detector is present, images are cataloged and auto-skipped with `no_face_detected` until the full dependency set is installed.

## Development

Backend API only:

```bash
python -m selfietl serve --reload
```

Frontend dev server:

```bash
cd web
npm run dev
```

The Vite dev server proxies `/api` requests to `http://localhost:8765`.

## Data

By default, local data lives under `~/.selfietl`:

```text
~/.selfietl/
  config.toml
  catalog.db
  cache/
    landmarks/
    aligned_landmarks/
    thumbs/
    renders/
  aligned/
  exports/
```

Set `SELFIE_TL_HOME=/path/to/data` to use another data directory.
