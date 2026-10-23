---
name: match-cut-edit
description: Build pose-matched "flawless cut" edits and seamless two-clip loops from vertical creator/dance clips of the same person. Use when the user wants a highlight edit, montage, match-cut, transition-on-pose, or looping video from several phone clips (TikTok / Reels / Shorts), cut on matching poses instead of on the music, with the body kept the same size on screen across cuts.
---

# Match-cut edit

Turns several vertical clips of one person into either
- a **montage**: a chain of fixed-length shots (default exactly 1.0s) where every cut lands on the most similar on-screen pose and movement, or
- **loops**: exactly two shots from two clips, A → B → back to A, each shot 0.5–2s, where both seams (including the wrap-around) are matched, so the file loops seamlessly. A 4-shot A → B → A′ → B′ variant exists (`--loop-shots 4`).

Everything is in `scripts/matchcut.py` (one CLI). Output is 1080×1920, 30fps, H.264.

## How it works (so you can tune it)

1. **Poses**: MediaPipe Pose (heavy) runs on every frame of every clip, and the results are cached as `<work>/pose_<i>.npy`. This is the slow step (~1 min per 15s clip on CPU).
2. **Camera**: for every frame a scale + translation is computed so the shoulder-to-hip length is exactly `--torso` px on screen and the hips are steered to (540, `--hip-y`). The camera is smoothed (~0.4s) and clamped so it never leaves the source frame. Same skeleton size in every shot is what makes the cuts read as seamless.
3. **Match cost**: joints (nose, shoulders, elbows, wrists, hips, knees) are compared **in screen space after the camera**, so a cut is scored on what the viewer actually sees. Cost = visibility-weighted joint distance + `--motion-weight` × joint-velocity distance; arms count most. A cut from clip X at frame i to clip Y at frame j compares X[i] (the frame the cut replaces) with Y[j].
4. **Montage search** (`plan`): beam search over shot chains of exactly `--shot` seconds (or `--shot-min/--shot-max`). It penalises reusing a clip too often, the same clip twice in a row (A B A), and reusing the same moment.
5. **Loop search** (`loop`): **exact**, not a beam. A seam's cost depends only on (shot start, shot length, next shot start), so for every pair of starts the best length is chosen per seam independently (`edge()`), and then the full (A start × B start) matrix is scored: cost = seam A→B + seam B→A. For 4 shots it does a min-plus product over the B shot and checks that the two A moments and the two B moments don't overlap. `--action` rewards shots that contain movement, so it doesn't pick two people standing still.
6. **Render**: per-frame warp (Lanczos), a light grade (soft S-curve, +10% saturation, warm, sharpen for upscaled sources, soft vignette), and optional inpainting of burned-in captions. No transitions or effects: the matched cut is the effect.

## Steps

```bash
S=.claude/skills/match-cut-edit/scripts      # or wherever the skill lives
bash $S/setup.sh                              # ffmpeg, EGL libs, opencv-python-headless, mediapipe (idempotent)

# 15s montage, 1s shots, soundtrack taken from clip 1
python3 $S/matchcut.py all --clips c0.mp4 c1.mp4 c2.mp4 ... --work work --audio 1 --out edit.mp4

# seamless loops: best 3 clip pairs, two shots (A -> B -> A) of 0.5-2s each, cycle written 4x
python3 $S/matchcut.py loop --clips c0.mp4 c1.mp4 ... --work work --shot-min 0.5 --shot-max 2 --top 3 --loop-repeat 4
# --top 0 just prints the ranking of every pair (fast, ~10s) without rendering
```

Subcommands: `poses`, `plan`, `preview`, `render`, `all` (poses+plan+preview+render), `loop`. They share `--work`, so poses are computed once.

Outputs:
- montage: `edit.mp4`, `work/plan.json` (the shot list), `work/cuts.jpg` (every cut as outgoing | incoming | 50% overlay)
- loops: `work/loops/loop_A_B.mp4`, `.json`, `_seams.jpg` (every seam, the last row is the wrap-around)

Always look at the cut/seam sheet before handing over: in the overlay column the two bodies should sit on top of each other. Then send the user the video and the sheet.

## Knobs

| flag | default | effect |
|---|---|---|
| `--shot` | 1.0 | exact shot length (s). Montage default: the user wanted exactly 1s shots |
| `--shot-min/--shot-max` | – | variable shot length instead (gives better matches). For loops the user wanted 0.5–2s |
| `--duration` | 15 | montage length (s) |
| `--torso` | 600 | on-screen torso px. Smaller shows more full body but the camera hits frame edges more (hip height starts to drift) |
| `--hip-y` | 1500 | on-screen hip height |
| `--motion-weight` | 6 | how much matching movement direction matters vs static pose |
| `--audio` | none | clip id or file for the soundtrack (loops are silent so they loop cleanly) |
| `--remove-captions 0.3` | off | inpaint static white text (captions, @handle watermarks) in the top fraction of each clip. Use 1.0 for captions anywhere; a stroke and shape filter skips bright windows and walls |
| `--loop-shots` | 2 | 2 = A B (preferred: only two clips, two shots), 4 = A B A′ B′ |
| `--pairs 0,3 1,4` | all | restrict which clip pairs to try for loops |
| `--action` | 0.5 | loop reward for movement within shots |
| `--per-pair` | 1 | distinct loops per clip pair (no shared moments). Use 4+ when there are only 2 clips |
| `--min-vis` | 0.5 | frames whose shoulder/hip visibility is below this are never shown or cut on (close-ups, walking out of frame) |

## Choosing --torso / --hip-y per footage

Run `loop ... --top 0` for a few combinations: it just prints the ranking (~10s each). Pick the lowest cost **that still keeps the head in frame**. Shoulders sit at `hip_y - torso` on screen and the head needs ~0.5×torso above that.
- Loose framing (head to knees, torso ≈ 25% of frame height): `--torso 600 --hip-y 1500`.
- Tight selfie framing (head to upper thigh, torso ≈ 45%): `--torso 1000 --hip-y 1800`. 1100/1700 matched slightly better but cut off the top of the head.
- The "can't hold torso size" warning means the source is too tight for that torso value. Raise `--torso`.

## Gotchas

- MediaPipe needs `libEGL.so.1` even on CPU (`setup.sh` installs it). The `TypeError: 'NoneType' object is not callable` printed at exit is a harmless MediaPipe destructor warning.
- Phone clips are usually framed head-to-thigh, so there's little room below the hips. If the plan step warns that a clip "can't hold torso size", raise `--torso`.
- Clips must be at least shot length + ~0.3s long.
- Don't add zoom punches, whips or shakes on top: they break the match. The user explicitly wanted them removed.
