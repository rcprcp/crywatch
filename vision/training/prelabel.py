#!/usr/bin/env python3
"""
prelabel.py — model-assisted labeling: run your CURRENT model over new frames,
write a suggested box per detection, and copy a de-duplicated review set for the
labeling tool. You then just verify (see ../labeling/).

What it does:
  1. Gather unlabeled frames from frames/** (those without a labels/<name>.txt).
  2. De-duplicate near-identical frames (a sleeping baby produces hundreds of almost
     identical shots — keep one per distinct look) via a perceptual hash.
  3. Run the model at a low threshold; write the top-1 box to prelabels/<name>.txt
     for frames it detects. Frames it misses get no prelabel (you draw those — they
     are exactly the blind spots worth teaching).
  4. Copy the review set to frames/review/ and point the labeling tool at it.

Usage:
    python3 prelabel.py --model ../detector/baby.pt
    FRAMES_DIR=frames/review python3 ../labeling/label_server.py   # then verify

Needs: ultralytics, opencv, numpy. Runs fine on CPU (a few hundred frames ~minutes).
"""
import os, glob, shutil, argparse
import numpy as np
import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
# Frames/labels/prelabels live in the labeling module (collect_frames.py and
# label_server.py write there), so default to ../labeling/*. Override with the
# FRAMES/LABELS/PRELABELS/REVIEW env vars when your data lives elsewhere.
LABELING = os.path.normpath(os.path.join(HERE, "..", "labeling"))
FRAMES = os.environ.get("FRAMES", os.path.join(LABELING, "frames"))
LABELS = os.environ.get("LABELS", os.path.join(LABELING, "labels"))
PRELABELS = os.environ.get("PRELABELS", os.path.join(LABELING, "prelabels"))
REVIEW = os.environ.get("REVIEW", os.path.join(FRAMES, "review"))


def labeled(f):
    return os.path.exists(os.path.join(LABELS, os.path.splitext(os.path.basename(f))[0] + ".txt"))


def ahash(path, s=16):
    im = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if im is None:
        return None
    im = cv2.resize(im, (s, s), interpolation=cv2.INTER_AREA)
    return (im > im.mean()).flatten()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(HERE, "..", "detector", "baby.pt"))
    ap.add_argument("--conf", type=float, default=0.12, help="low threshold — catch weak detections too")
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--hamming", type=int, default=12, help="dedup distance over a 256-bit hash")
    a = ap.parse_args()

    for d in (PRELABELS, REVIEW):
        os.makedirs(d, exist_ok=True)
        for f in glob.glob(os.path.join(d, "*")):
            os.remove(f)

    cand = [f for f in sorted(glob.glob(os.path.join(FRAMES, "**", "*.jpg"), recursive=True))
            if os.path.dirname(f) != REVIEW and not labeled(f)]
    reps, keep = [], []
    for f in cand:
        h = ahash(f)
        if h is None:
            continue
        if all(np.count_nonzero(h != r) > a.hamming for r in reps):
            reps.append(h); keep.append(f)
    print(f"unlabeled={len(cand)}  distinct(after dedup)={len(keep)}")

    from ultralytics import YOLO
    model = YOLO(a.model)
    pre = miss = 0
    for f in keep:
        name = os.path.basename(f)
        shutil.copy2(f, os.path.join(REVIEW, name))
        r = model.predict(f, imgsz=a.imgsz, conf=a.conf, device=a.device, verbose=False)[0]
        if len(r.boxes) == 0:
            miss += 1
            continue
        i = int(r.boxes.conf.argmax())                     # top-1 (one baby)
        xc, yc, w, h = r.boxes.xywhn[i].cpu().numpy()
        with open(os.path.join(PRELABELS, os.path.splitext(name)[0] + ".txt"), "w") as fp:
            fp.write(f"0 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
        pre += 1
    print(f"pre-boxed (verify these) = {pre}   AI missed (draw by hand) = {miss}")
    print(f"review set: {REVIEW}\nNow: FRAMES_DIR={REVIEW} python3 ../labeling/label_server.py")


if __name__ == "__main__":
    main()
