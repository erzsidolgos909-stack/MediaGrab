# MediaGrab

**English** | [简体中文](README.zh-CN.md)

A Windows image and video downloader: paste a link, pick a folder, hit start. It decides which backend a URL needs on its own, the UI is plain Tkinter, and it packages into a single-file EXE that non-programmers can just double-click.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![Tests](https://img.shields.io/badge/tests-44%20passed-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

## Download

Don't want to run Python? Grab a build from [**Releases**](https://github.com/erzsidolgos909-stack/MediaGrab/releases) — no environment needed:

| File | What it is |
|---|---|
| `MediaGrab-x.y.z-setup.exe` | Installer: Start-menu shortcuts, uninstaller, installs per-user without an admin prompt |
| `MediaGrab-x.y.z-portable.zip` | Portable: unzip anywhere and run |

Both are unsigned, so Windows SmartScreen and some antivirus engines may warn about them — that's the usual PyInstaller false positive.

## What it handles

| Link type | Backend | Notes |
|---|---|---|
| Image sites / albums / user pages | gallery-dl | Thousands of sites: Pixiv, X, Instagram, Reddit, Pinterest, Weibo, Bilibili albums… |
| Direct image URLs | requests | Single file; Weibo CDN thumbnails are upgraded to full size automatically |
| Video / audio | yt-dlp | YouTube, Bilibili, TikTok, … pick 1080p / 720p / 480p, or extract audio to MP3 |

Also:

- One link per line, optional concurrency (1–3 jobs)
- Cancellable — direct links and videos stop immediately; gallery jobs finish the current file first, because gallery-dl exposes no cancel hook
- Failed-item list with one-click retry, instead of scrolling the log to find what broke
- Daily log files on disk, kept for 14 days
- Browser cookie import (Firefox works; see the Chrome/Edge limitation below)
- Optional update check on startup — it only tells you, it never replaces its own EXE

## Running it

Python 3.10+ required.

```bash
pip install -r requirements.txt
python MediaGrab.py
```

Single-file EXE:

```bash
python -m PyInstaller MediaGrab.spec --noconfirm
```

Output lands in `dist\`. Note that the app resolves its config via `app_dir()`, so **keep the EXE in its own folder** — otherwise `config.json` and `history.json` follow it around.

## Tests

```bash
python -m pytest tests -q
```

44 tests, roughly 22 seconds:

- **40 unit tests** (0.4 s) — pure functions only: URL classification, status-code translation, short-link resolution, error messages, filename derivation, size formatting
- **4 end-to-end tests** — a real Tk window, real worker thread and queue, and a local HTTP server serving test files. They verify the wiring: paste a link → press start → file lands on disk → buttons reset. No mouse simulation (that would need pywinauto, which is flaky against Tkinter), but it catches mis-wired buttons and broken thread handoffs

The build script runs the whole suite first and refuses to package when anything is red.

## How the code is laid out

One file, ~1400 lines, in sections:

```
MediaGrab.py
├── dependency probing    missing libs don't crash, they flip a flag to False
├── exceptions            DownloadFailed / UnsupportedUrl / Cancelled
├── helpers               URL cleaning, size formatting, filename derivation
├── update check          version compare + manifest parsing, silent on failure
├── video download        yt-dlp wrapper: retries, cancel hook, cookies
├── image download        gallery-dl wrapper + direct download (.part then rename)
└── App (Tkinter)         UI, thread snapshot, queue draining
```

Three design notes worth calling out:

**1. Widget values are snapshotted on the main thread before the worker starts.** Tkinter isn't thread-safe, so pressing Start reads eight widget values into plain variables and passes them into the background thread. The worker never touches a Tk object.

**2. `gallery-dl`'s `run()` returns an integer status mask, not an object.** Treating it like an object (`getattr(status, "total", 0)`) silently yields `0` forever — the log showed "downloaded 0/0 files" and failures were counted as successes. The fix: check `rc != 0`.

**3. Never use a function's return value as proof that its side effect worked.** `build_options()` only writes into gallery-dl's global config and has no return value (implicit `None`). A caller treated that `None` as "image support unavailable", so every image download in v2.3.0–v2.3.2 failed with "missing dependencies" while the dependencies were installed all along. Only the end-to-end test surfaced it.

## Known limitations

**Mainland Douyin doesn't work.** Short links resolve, real login cookies are accepted, the `a_bogus` signature is computed, the TLS fingerprint is impersonated — all five layers pass, and the video detail endpoint still answers 403. The blocking piece is a session key generated inside their page JavaScript, which a plain HTTP client cannot obtain.

**Chrome and Edge cookies can't be read.** Since Chrome 127 both browsers protect cookies with App-Bound Encryption, while yt-dlp only implements the older DPAPI scheme (`Failed to decrypt with DPAPI`). Use Firefox for logged-in sites, or import a Netscape-format cookie file.

**Windows only.** The build scripts and path handling assume Windows.

**The single-file EXE takes 2–3 seconds to start**, because it unpacks itself each time.

## Disclaimer

MediaGrab is only a front-end for [gallery-dl](https://github.com/mikf/gallery-dl) and [yt-dlp](https://github.com/yt-dlp/yt-dlp). Every download capability comes from those libraries; this project contains no DRM circumvention and no paywall bypass.

Use it only for content you have the right to download, and follow each site's terms of service and your local law. Redistributing what you download may infringe someone's copyright. The author is not responsible for how this tool is used.

## License

MIT — see [LICENSE](LICENSE).
