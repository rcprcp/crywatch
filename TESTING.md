# Human Testing Guide

Manual test structure for crywatch. Organized into tiers you can run
independently: tiers **A/B** need no cameras or GPU, **C/D** need go2rtc (or a
fake), and **F** is the full stack.

> **Ground rule: no production code changes.** Every case below uses hooks that
> already exist (the `window.__cry` object, the detector control API, env vars,
> `CRY_LOG`/`PRESENCE_LOG`). Fixtures are the only files you add.

---

## 1. Components under test

| # | Component | Entry point | Needs |
|---|---|---|---|
| 1 | Multi-cam viewer | `viewer/multi.html` | browser (+ go2rtc for video) |
| 2 | Single-cam viewer | `viewer/index.html` | browser (+ go2rtc for video) |
| 3 | Cry detector | `cry-detector/yamnet_detect.py` | ffmpeg + TF, an RTSP source |
| 4 | Presence detector | `vision/detector/presence_detector.py` | an image source |
| 5 | Labeler | `vision/labeling/label_server.py` | python3 stdlib |
| 6 | Pre-label / train | `vision/training/prelabel.py`, `train_baby.py` | ultralytics |
| 7 | Stack | `docker-compose.yml` | docker |

## 2. Existing test hooks (no changes needed)

| Hook | Where | Use |
|---|---|---|
| `window.__cry.fire()` | `multi.html:194` | fire a cry alert now |
| `window.__cry.levels()` | `multi.html:196` | dump per-panel level 0–255 |
| `window.__cry.setThreshold(n)` | `multi.html:197` | move the client threshold |
| `window.__cry.tick(ts)` | `multi.html:199` | drive the alert state machine manually |
| `GET /arm /disarm /toggle` | `presence_detector.py:118` | arm state; response = full state JSON |
| `GET /absent/{on,off,toggle}` | `presence_detector.py:128` | absent-alarm toggle |
| `GET /zone/set?x1&y1&x2&y2`, `/zone/clear` | `presence_detector.py:134` | sleep zone |
| `boxes.json`, `control.json`, `zone.json` | detector `HERE` | inspect runtime state |
| `CRY_LOG=1`, `PRESENCE_LOG=1` | both detectors | per-sample logs |
| all `CRY_*`, `CONF`, `MOTION_*`, `HARD_*` env vars | `.env.example` | tuning without code edits |

## 3. Test tiers

### Tier A — Offline / no hardware

| ID | Case | Steps | Expected |
|---|---|---|---|
| A1 | JS parses | `node --check`-equivalent: open both HTML files; load in browser console | No syntax errors; grid renders "connecting…" tiles |
| A2 | Python compiles | `python3 -m py_compile cry-detector/yamnet_detect.py vision/detector/presence_detector.py vision/labeling/label_server.py vision/labeling/collect_frames.py vision/training/*.py` | exit 0 |
| A3 | Shell lint | `bash -n cry-detector/run.sh vision/training/remote_train.sh` | exit 0 |
| A4 | Secret scan | `gitleaks detect --source . --no-git` | no findings; `.env`/`*.pt`/frames ignored |
| A5 | Compose config | `docker compose config` | valid, no interpolation warnings |

### Tier B — Viewer UI (browser only, no video)

Serve the static files: `python3 -m http.server 8080 --directory viewer`.

| ID | Case | Steps | Expected |
|---|---|---|---|
| B1 | Grid layout / labels | open `http://localhost:8080/multi.html?cams=a,b,c,d&labels=Nursery,Bedroom,Hall,Play` | 2×2 grid (auto-cols for >2), correct labels, control strip below each video |
| B2 | Portrait feed | narrow the window (portrait) | single scrollable column, 16:9 tiles, floating 🔔 button pinned bottom-centre |
| B3 | Zoom / pan (mouse) | wheel-zoom, drag | transform follows cursor; pan clamped; % label updates; reset returns to 100% |
| B4 | Double-click zoom | double-click a tile | toggles 1× ↔ 2.5× |
| B5 | Focus mode | tap ⛶ on a tile | tile expands, others hidden, 🔔 hidden, ✕ shown; ✕ restores grid + zoom reset |
| B6 | Mute radio | unmute two tiles in turn | only one plays at a time; icons sync |
| B7 | Cry toggle UI | click 🔔 Cry alert | button turns red / "ON"; clicking again clears any `.crying` panel |
| B8 | `__cry` hooks | in console: `__cry.fire(); __cry.levels(); __cry.setThreshold(10)` | `fire()` triggers banner/notification (if permitted); `levels()` returns `[{label,level}]` |
| B9 | Alert state machine | `__cry.setThreshold(0); __cry.tick(0); __cry.tick(2000)` after enabling alerts | `crying` class appears after `CRY_SUSTAIN_MS` (1500ms) only |
| B10 | Single-cam viewer | open `index.html?src=x` | loads, shows "connecting…", zoom/pan/fullscreen buttons behave |
| B11 | XSS probe (see E1) | open `multi.html?labels=<img src=x onerror=alert(1)>,b` | the string renders as literal text; no alert (fixed) |

### Tier C — Cry detector (audio plumbing)

Build once: `docker build -t baby-cry-detector ./cry-detector`.

Use a throwaway `go2rtc-test.yaml` (no cameras):

```yaml
streams:
  test:
    - ffmpeg:/fixtures/cry.wav     # loop a known clip
rtsp:
  listen: ":8554"
api:
  listen: ":1984"
```

| ID | Case | Steps | Expected |
|---|---|---|---|
| C1 | Silent input → gate | stream silence; `CRY_LOG=1` | log shows `cry=0.00 rms < -60dB top='(silence)'`; no push |
| C2 | Loud non-cry → no push | stream speech/vacuum clip | top class not a cry class; `cry` below `CRY_PROB`; no push |
| C3 | Real cry → push | stream a real cry clip | `cry` ≥ 0.4 for `CRY_SUSTAIN_SAMPLES` consecutive samples → `[notify] sent` and an ntfy message arrives |
| C4 | Cooldown | keep cry looping | after first push, no second push within `CRY_COOLDOWN` (default 60s) |
| C5 | Sample length | set `CRY_SAMPLE_SEC=5` | log cadence matches ~5s; detector still runs |
| C6 | Stream down / recovery | stop go2rtc mid-run, restart | `[sample]` errors/sleep, then recovers without crash; `streak` resets |
| C7 | Multi-cam | `CAMS=test=A,test2=B` (2 streams) | independent streaks/labels; inference serialized (no TF crash) |
| C8 | Bad RTSP URL | point at a dead host | logs errors, keeps retrying, container stays up |
| C9 | Notification content | inspect ntfy | Title "Baby cry alert", Priority high, Click = `CRY_CLICK_URL` |

### Tier D — Presence detector (vision)

No GPU/model needed: unset `MODEL` and no `baby.pt` → falls back to `yolo11s.pt`
(downloads on first run). Point it at a fake snapshot API:

```bash
mkdir -p /tmp/fake/api && cp fixtures/crib.jpg /tmp/fake/api/frame.jpeg
python3 -m http.server 1984 --directory /tmp/fake   # serves /api/frame.jpeg?src=...
GO2RTC=http://localhost:1984 STREAM=x PRESENCE_LOG=1 CONTROL_PORT=1986 \
  python3 vision/detector/presence_detector.py
```

| ID | Case | Steps | Expected |
|---|---|---|---|
| D1 | Boot / state | `curl :1986/` | JSON with `armed:true`, `present:true`, `zone:null` |
| D2 | Arm/disarm | `curl :1986/disarm` then `/arm`, `/toggle` | `armed` flips; persisted to `control.json` across restart |
| D3 | Absent toggle | `/absent/off` then `/absent/on` | `absent_alarm` flips; state persists |
| D4 | Zone set / clear | `/zone/set?x1=0.1&y1=0.2&x2=0.9&y2=0.9` | `zone` in state + `zone.json`; `/zone/clear` removes both |
| D5 | Zone validation | `/zone/set?x1=0.5&y1=0.5&x2=0.51&y2=0.51` | rejected (too small, <0.02); `/zone/set` with out-of-range clamps to 0–1 |
| D6 | Overlay file | watch `BOXES_JSON` | atomic JSON with normalized boxes, `present`, `moving`, `motion`, `zone` |
| D7 | Absent alarm | serve an *empty* crib frame, arm, lower `ABSENT_SEC=5` | after 5s → ntfy "not detected"; alert JPEG written to `alerts/` |
| D8 | Back | swap snapshot to a frame with a person (daylight) | ntfy "{LABEL} · back" once |
| D9 | Motion | alternate two frames differing in-zone; lower `MOTION_FRAC` | "moving" state true after `MOTION_SUSTAIN`; ntfy "moving" |
| D10 | Hard collect | `HARD_COLLECT=1`, weak in-zone box + motion | frame saved under `frames/hard/`; stops at `HARD_MAX` |
| D11 | Snapshot down | stop fake server | `[grab]` errors, loop continues, no crash |
| D12 | Top-1 overlay | scenario producing 2 boxes | `boxes.json` contains only the highest-conf box |

### Tier E — Security & negative

| ID | Case | Expected |
|---|---|---|
| E1 | Viewer label XSS | `labels` is rendered via `textContent` (`viewer/multi.html`), so `?labels=<img onerror=alert(1)>` shows as literal text and does **not** execute. |
| E2 | Control API auth | Any LAN host can `GET /disarm` unauthenticated. Confirm and document as LAN/VPN-only (or add a token). |
| E3 | CORS | `Access-Control-Allow-Origin: *` on `/` responses — acceptable only if never port-forwarded |
| E4 | No secret leaks | `go2rtc.yaml`, `.env`, `*.key`, `*.pt`, `frames/` are untracked (`git status --ignored`) |
| E5 | Port exposure | only 1984/1985/8554/8555/8095(+1986) exposed; none port-forwarded to WAN |

### Tier F — Full stack (docker compose)

| ID | Case | Steps | Expected |
|---|---|---|---|
| F1 | Bring up | `cp examples/go2rtc.example.yaml go2rtc.yaml && cp .env.example .env`, fill, `docker compose up -d --build` | all four containers healthy |
| F2 | Viewer live | open `:1985/multi.html?cams=cam1,cam2&labels=Nursery,Bedroom` | live WebRTC video, sub-second latency, audio plays on unmute |
| F3 | Focus HD switch | tap ⛶ | loader/countdown shows, swaps to `*_hd`, watchdog drops back to base if HD stalls >8s |
| F4 | Reconnect | restart go2rtc | viewer reconnects (loader → live) without page reload |
| F5 | Cry → push | play a cry clip on a cam (or C3 stream) | ntfy push on phone; tap opens `CRY_CLICK_URL` |
| F6 | Port conflict / restart | `docker compose restart` | detector resumes; `restart: unless-stopped` honored |

## 4. Quick regression checklist (pre-release)

- [ ] A2 / A3 / A4 clean
- [ ] B1, B3, B5, B7 pass
- [ ] C3 push received, C4 cooldown holds
- [ ] D1, D4, D7 pass (control API + absent alarm + zone)
- [ ] E1 fixed or explicitly accepted; E4 clean
- [ ] F2, F4 pass

## 5. Fixtures to add (only new files)

```
test/
├── fixtures/
│   ├── cry.wav        # a real infant cry (private; do not commit)
│   ├── speech.wav     # non-cry loud audio
│   ├── silence.wav
│   └── crib.jpg       # one crib image for the presence tests
└── fake-go2rtc/       # tiny http.server root: api/frame.jpeg
```

Audio for C2/C3 can be replaced by any local clip with `ffmpeg`. Keep all
fixtures out of git (`test/fixtures/` should be added to `.gitignore`).

## 6. Findings from the code review

**High** (all fixed)
1. **[FIXED]** XSS in `multi.html` — `labels` from the query string went into
   `panel.innerHTML`. Now set with `textContent`.
2. **[FIXED]** `index.html` lacked the generation guard from `multi.html`; its
   reconnects could stack PeerConnections. Guard ported (`gen`/`stale()`).
3. **[FIXED]** Workflow path mismatch — `prelabel.py` now defaults to
   `../labeling/{frames,labels,prelabels}` so it matches `collect_frames.py` /
   `label_server.py`.

**Medium**
4. `yamnet_detect.py:89` sends ffmpeg stderr to `DEVNULL`, so stream failures are
   opaque. Capture/report the last stderr lines.
5. Presence control API is unauthenticated on `0.0.0.0` with CORS `*`
   (`presence_detector.py:105`). Fine on a trusted LAN/VPN; document or add a token.
6. Client-side cry flash (`multi.html`, 300–1000Hz level) is a *loudness* heuristic
   and contradicts the project's "loud ≠ crying" premise — it fires on any loud
   mid-band sound. Label it clearly as a rough client hint.
7. `train_baby.py` hardcodes `device=0` and uses `os.symlink` — fails on
   Windows/CPU-only boxes despite the otherwise-portable setup.

**Low**
8. `.env.example` omits `CRY_SAMPLE_SEC` and `CRY_RMS_GATE_DB` (both supported by
   the code); compose doesn't pass them either.
9. `presence_detector.py` writes `alerts/` at import and creates `frames/hard/`
   lazily; a missing `BOXES_JSON` directory silently errors every poll.
10. `index.html` uses a Google STUN server while `multi.html` uses none — harmless
    but inconsistent.
11. No graceful shutdown / signal handling in either detector (SIGTERM just kills).
