# crywatch-vision 👀

English | [繁體中文](README.zh-TW.md)

**Teach your baby monitor to *see* — train your own detector that knows whether the
baby is actually in the crib**, even in night-vision, even wrapped in a blanket with
only a foot showing.

This is the optional **vision** module for [crywatch](../README.md). crywatch (the
main project) teaches the monitor to *hear* — a real cry vs. just "loud". This module
teaches it to *see*: it watches the crib camera, pushes a notification if the baby
hasn't been detected for a while (left the crib / covered up?), flags motion ("woke
up / rolling / crawling"), and draws a live box on the camera in the web viewer.

> ⚠️ **This ships NO model and NO images.** A model trained on a baby is private data.
> You train your **own** model on your **own** camera. This repo gives you the whole
> pipeline and tools to do that in a weekend — nothing about anyone's actual baby is
> published here, and your `frames/`, `labels/`, and `*.pt` are gitignored so they
> never leak either.

---

## Why you have to train your own

The obvious first try is: grab an off-the-shelf detector (YOLO) and point it at the
crib. In daylight it works great. At night it falls apart — grayscale night-vision +
a baby buried under a blanket = the model's confidence drops to ~0.03, i.e. **"I can't
see a baby at all."** Stock models were trained on clear, daytime, upright adults;
they've never seen *your* night-vision, blanket-covered infant.

So the detector here isn't a magic pretrained model — it's a **loop** that lets you
teach a tiny model exactly the cases it keeps missing:

```
 collect frames ──► the detector auto-saves its MISSES (frames/hard/)
        ▲                              │
        │                              ▼
   retrain ◄── you VERIFY  ◄── AI pre-boxes them for you  (prelabel.py)
   (a spare GPU)   (labeling/)
```

This is a **data flywheel**: instead of feeding it thousands of random frames, you
feed it exactly its own blind spots. Each round is small (~100 frames) but the jumps
are big. In the author's own setup, hard-pose recall went from roughly **2 in 10 → 9+
in 10** across two short rounds — and the second round only needed ~120 frames because
the model pre-boxed half of them for verification.

---

## What's in here

```
vision/
├── detector/
│   ├── presence_detector.py   # the service: poll go2rtc → detect → present/absent/motion → ntfy + overlay
│   ├── Dockerfile
│   └── requirements.txt → ../requirements.txt
├── labeling/
│   ├── label_server.py        # browser labeling tool with AI-suggested boxes
│   ├── label.html
│   └── collect_frames.py      # grab snapshots from go2rtc to seed a dataset
├── training/
│   ├── prelabel.py            # run your current model → pre-box new frames for verification
│   ├── train_baby.py          # fine-tune yolo11s → baby.pt
│   └── remote_train.sh        # train via the official Ultralytics Docker image on a spare GPU
├── .env.example
└── requirements.txt
```

Everything is config-driven via env vars (see `.env.example`). The detector runs on
CPU or GPU; training wants a GPU (a cheap one is plenty — it's a small model).

---

## The workflow (a weekend, roughly)

**0. Prereqs.** A working [go2rtc](https://github.com/AlexxIT/go2rtc) with your camera
streams (crywatch's `examples/go2rtc.example.yaml` covers this), and an
[ntfy](https://ntfy.sh) topic for push. `pip install -r requirements.txt`.

**1. Seed a first dataset.** Grab some frames to label:
```bash
cd vision/labeling
CAMS=cam2=bedroom python3 collect_frames.py --interval 30   # collect a night of frames
```

**2. Label them (fast, AI-assisted).** Pre-box with whatever model you have (first
round: stock `yolo11s.pt`), then just verify. `prelabel.py` reads frames/labels from
`../labeling/` (where the labeling tools keep them) and writes its suggestions and
review set back there — override with the `FRAMES`/`LABELS`/`PRELABELS`/`REVIEW`
env vars if your data lives elsewhere:
```bash
cd ../training
python3 prelabel.py --model yolo11s.pt          # -> ../labeling/prelabels/ + ../labeling/frames/review/
cd ../labeling
FRAMES_DIR=frames/review python3 label_server.py
# open http://localhost:1987/  → orange dashed box = AI guess: confirm, nudge, or redraw.
```
Empty crib? Press **"no baby"** (that's a negative sample — it stops false alarms).

**3. Train on a spare GPU.** Don't use the GPU that drives your desktop (a full run
freezes the screen 😅). Copy the data to a headless box and let Docker do it:
```bash
rsync -a frames/ labels/ ../training/train_baby.py  TRAIN_HOST:~/baby-train/
ssh TRAIN_HOST 'cd ~/baby-train && bash remote_train.sh'   # ~a few minutes on a modern GPU
scp TRAIN_HOST:~/baby-train/baby.pt ../detector/baby.pt
```

**4. Deploy.** Point the detector at your model and run it:
```bash
cd ../detector
cp ../.env.example .env    # set STREAM, NTFY_URL, MODEL=./baby.pt, etc.
python3 presence_detector.py
# or: docker build -f detector/Dockerfile -t baby-presence . && docker run --env-file .env ...
```

**5. Let it improve itself.** With `HARD_COLLECT=1`, the detector saves the frames it
*misses but where the baby is likely there* into `frames/hard/`. After a few nights,
repeat steps 2–4 with those — that's the flywheel. Each round gets sharper.

---

## Features worth knowing

- **Sleep zone.** Draw a box around the whole crib once (via the viewer's ✏️ button →
  `/zone/set`). The detector then (a) accepts weaker detections *inside* the crib and
  (b) scopes "is moving" to the crib, so a parent walking past doesn't count. It's a
  safety net for the last few impossible frames (fully covered + perfectly still).
- **Arm / disarm + absent-alarm toggle.** A tiny HTTP control API (`/arm`, `/disarm`,
  `/absent/off`, `/zone/set`, …) the web viewer wires to buttons — so an empty crib
  during the day doesn't spam you.
- **One box per baby.** The overlay shows only the single highest-confidence box, so
  you never get "two red boxes on one baby" (the model occasionally fires on legs +
  torso separately).
- **Runs local, no cloud.** Snapshots come from your own go2rtc; alerts go to your own
  ntfy. Nothing about your baby leaves your network.

## Viewer overlay

The detector writes normalized boxes to `boxes.json` (`BOXES_JSON`). Serve that file
from crywatch's `viewer/` (e.g. mount it into the web root) and have the viewer poll
it to draw the red box on the crib tile. The control API (`CONTROL_PORT`, default 1986)
backs the arm/disarm and ✏️-zone buttons.

## License

Apache-2.0, same as crywatch. See [../LICENSE](../LICENSE). Uses
[Ultralytics YOLO11](https://github.com/ultralytics/ultralytics) (AGPL-3.0) at
training/inference time — review its license before any commercial use.
