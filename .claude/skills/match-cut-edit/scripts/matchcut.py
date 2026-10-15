#!/usr/bin/env python3
"""Pose-matched montage ("match cut") editor for vertical dance/creator clips.

Pipeline:
  poses    - MediaPipe pose landmarks for every frame of every clip (cached .npy)
  plan     - beam-search a chain of fixed-length shots whose cut frames match in on-screen pose + motion
  preview  - contact sheet of every cut: outgoing | incoming | 50% overlay
  render   - 1080x1920 H.264 with a per-frame camera that keeps the torso the same size on screen

  loop     - seamless 2-clip loops: A -> B (-> A' -> B') -> back to A, every seam matched

Run `matchcut.py all --clips a.mp4 b.mp4 ... --out edit.mp4` to do everything.
"""
import argparse, json, os, subprocess, sys, urllib.request
import numpy as np
import cv2

W, H, FPS = 1080, 1920, 30
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task"
JOINTS = [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26]          # nose, shoulders, elbows, wrists, hips, knees
WJ = np.array([1.0, 1, 1, 1.5, 1.5, 2, 2, 1, 1, 0.8, 0.8])     # arms count most for "same pose"


# ---------------------------------------------------------------- io
def probe_size(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=width,height:stream_side_data=rotation", "-of", "json", path])
    s = json.loads(out)["streams"][0]
    w, h = s["width"], s["height"]
    rot = next((abs(int(d.get("rotation", 0))) for d in s.get("side_data_list", [])), 0)
    return (h, w) if rot in (90, 270) else (w, h)


def load_frames(path, pix="bgr24"):
    w, h = probe_size(path)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={FPS}", "-f", "rawvideo", "-pix_fmt", pix, "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)


def pose_path(work, i):
    return os.path.join(work, f"pose_{i}.npy")


# ---------------------------------------------------------------- 1. poses
def cmd_poses(a):
    import mediapipe as mp
    from mediapipe.tasks.python import vision, BaseOptions
    model = os.path.join(a.work, "pose_landmarker_heavy.task")
    if not os.path.exists(model):
        print("downloading pose model ...", file=sys.stderr)
        urllib.request.urlretrieve(MODEL_URL, model)
    for i, clip in enumerate(a.clips):
        out = pose_path(a.work, i)
        if os.path.exists(out) and not a.force:
            continue
        fr = load_frames(clip, "rgb24")
        h, w = fr.shape[1:3]
        lm = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model), running_mode=vision.RunningMode.VIDEO))
        P = np.full((len(fr), 33, 4), np.nan, np.float32)
        for k, f in enumerate(fr):
            r = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(f)), int(k * 1000 / FPS))
            if r.pose_landmarks:
                P[k] = [(p.x * w, p.y * h, p.visibility, p.presence) for p in r.pose_landmarks[0]]
        # fill frames with no detection from neighbours
        miss = np.isnan(P[:, 0, 0])
        if miss.all():
            sys.exit(f"no person detected in {clip}")
        if miss.any():
            idx = np.where(~miss)[0]
            for k in np.where(miss)[0]:
                P[k] = P[idx[np.argmin(abs(idx - k))]]
        np.save(out, P)
        print(f"clip {i}: {len(fr)} frames, {miss.sum()} filled  ({os.path.basename(clip)})")


# ---------------------------------------------------------------- camera
def smooth(a, k):
    return np.array([np.nanmean(a[max(0, i - k):i + k + 1], axis=0) for i in range(len(a))])


def camera(P, size, torso_px, hip_y, safe_top=0.0):
    """Per-frame (scale, tx, ty): torso length == torso_px on screen, hips steered to (W/2, hip_y), clamped in-frame."""
    ms = (P[:, 11, :2] + P[:, 12, :2]) / 2
    mh = (P[:, 23, :2] + P[:, 24, :2]) / 2
    T = smooth(np.linalg.norm(smooth(ms, 3) - smooth(mh, 3), axis=1), 12)
    mh = smooth(mh, 12)
    sw, sh = size
    y0 = safe_top * sh
    s = np.maximum(torso_px / T, max(W / sw, H / (sh - y0)))
    tx = np.clip(W / 2 - mh[:, 0] * s, W - sw * s, 0)
    ty = np.clip(hip_y - mh[:, 1] * s, H - sh * s, -y0 * s)
    return np.stack([s, tx, ty], 1)


def cameras(a):
    out = {}
    for i, clip in enumerate(a.clips):
        P = np.load(pose_path(a.work, i))
        out[i] = (camera(P, probe_size(clip), a.torso, a.hip_y), P)
    return out


# ---------------------------------------------------------------- 2. plan
def match_matrices(a):
    """DM[(x, y)][i, j] = cost of cutting from frame i of clip x to frame j of clip y (screen-space pose + motion)."""
    CAM = cameras(a)
    S, V, VIS = {}, {}, {}
    for c, (cam, P) in CAM.items():
        q = P[:, JOINTS, :2] * cam[:, None, :1] + cam[:, None, 1:]           # joints in screen space
        q = smooth(q, 2) / a.torso
        S[c] = q
        v = np.zeros_like(q); v[3:-3] = (q[6:] - q[:-6]) / 6; V[c] = v
        VIS[c] = np.clip(P[:, JOINTS, 2], 0.2, 1)
        warn = (abs(torso_on_screen(cam, P) / a.torso - 1) > 0.06).mean()
        if warn > 0.2:
            print(f"warning: clip {c} can't hold torso size on {warn:.0%} of frames (source too tight); try a larger --torso",
                  file=sys.stderr)

    def dist(x, y):
        w = WJ * np.sqrt(VIS[x][:, None] * VIS[y][None])
        d = np.linalg.norm(S[x][:, None] - S[y][None], axis=-1) + a.motion_weight * np.linalg.norm(V[x][:, None] - V[y][None], axis=-1)
        return (d * w).sum(-1) / w.sum(-1)

    DM = {(x, y): dist(x, y) for x in S for y in S if x != y}
    MOT = {c: np.linalg.norm(V[c], axis=-1).mean(-1) for c in S}            # per-frame body speed
    return DM, {c: len(S[c]) for c in S}, MOT


def cmd_plan(a):
    DM, n, _ = match_matrices(a)
    S = n
    total = round(a.duration * FPS)
    lmin = round((a.shot_min or a.shot) * FPS)
    lmax = round((a.shot_max or a.shot) * FPS)

    states = [(0.0, 0, ((c, f),)) for c in S for f in range(4, n[c] - lmin - 4, 2)]
    done = []
    while states:
        nxt = []
        for cost, fd, segs in states:
            c, fin = segs[-1]
            counts = {}
            for s in segs[:-1]:
                counts[s[0]] = counts.get(s[0], 0) + 1
            for L in range(lmin, lmax + 1):
                fout = fin + L
                if fout > n[c] - 4:
                    break
                nfd = fd + L
                if nfd == total or (total - nfd < lmin and fin + total - fd <= n[c] - 1):
                    if fin + total - fd <= n[c] - 1:
                        done.append((cost, segs[:-1] + ((c, fin, fin + total - fd),)))
                    continue
                if nfd > total - lmin:
                    continue
                for b in S:
                    if b == c or (len(segs) > 1 and segs[-2][0] == b and len(S) > 2):
                        continue
                    row = DM[(c, b)][fout]
                    hi = n[b] - lmin - 4
                    if hi <= 4:
                        continue
                    for g in np.argsort(row[4:hi])[:10] + 4:
                        overlap = any(s[0] == b and len(s) == 3 and g < s[2] + 10 and g + lmin > s[1] - 10 for s in segs[:-1])
                        pen = row[g] + 0.15 * counts.get(b, 0) + (1.0 if overlap else 0)
                        nxt.append((cost + pen, nfd, segs[:-1] + ((c, fin, fout), (b, int(g)))))
        nxt.sort(key=lambda s: s[0])
        seen, states = {}, []
        for s in nxt:
            key = (s[2][-1][0], s[2][-1][1] // 6, s[1])
            if seen.get(key, 0) < 2:
                seen[key] = seen.get(key, 0) + 1
                states.append(s)
            if len(states) >= a.beam:
                break
    if not done:
        sys.exit("no path found: clips too short for the requested shot length / duration")
    cost, segs = min(done, key=lambda s: s[0])
    plan = [dict(clip=int(c), file=a.clips[c], fin=int(x), fout=int(y)) for c, x, y in segs]
    json.dump(dict(torso=a.torso, hip_y=a.hip_y, shots=plan), open(a.plan, "w"), indent=1)
    for p, q in zip(plan, plan[1:]):
        print(f"clip{p['clip']}@{p['fout']:4d} -> clip{q['clip']}@{q['fin']:4d}  cost {DM[(p['clip'], q['clip'])][p['fout'], q['fin']]:.3f}")
    print(f"{len(plan)} shots, {sum(p['fout'] - p['fin'] for p in plan)} frames, total cost {cost:.3f} -> {a.plan}")


def torso_on_screen(cam, P):
    ms = (P[:, 11, :2] + P[:, 12, :2]) / 2
    mh = (P[:, 23, :2] + P[:, 24, :2]) / 2
    return np.linalg.norm(ms - mh, axis=1) * cam[:, 0]


# ---------------------------------------------------------------- loops
def edge(DM, n, MOT, X, Y, sx, sy, Ls, action):
    """Best cost of a shot of clip X starting at sx[i] (any length in Ls) cutting into clip Y at sy[k].
    The seam only depends on that shot's start, its length and the next start, so length is optimised per edge."""
    cs = np.cumsum(np.r_[0, MOT[X]])
    best = np.full((len(sx), len(sy)), np.inf); arg = np.zeros(best.shape, int)
    for L in Ls:
        ok = sx + L <= n[X] - 4
        if not ok.any():
            continue
        c = np.full(best.shape, np.inf)
        c[ok] = DM[(X, Y)][sx[ok] + L][:, sy] - action * ((cs[sx[ok] + L] - cs[sx[ok]]) / L)[:, None]
        better = c < best
        best[better] = c[better]; arg[better] = L
    return best, arg


def best_loop(DM, n, MOT, A, B, Ls, shots, action):
    """Exact search for a seamless loop of clips A,B: A -> B -> back to A (shots=2) or A -> B -> A' -> B' -> A (shots=4).
    Every seam, including last -> first, is scored. Returns (cost, [(clip, start, length), ...])."""
    lmin, lmax = min(Ls), max(Ls)
    ia = np.arange(4, n[A] - lmin - 4)
    kb = np.arange(4, n[B] - lmin - 4)
    if len(ia) == 0 or len(kb) == 0:
        return None
    AB, LA = edge(DM, n, MOT, A, B, ia, kb, Ls, action)                    # A shot i  -> B shot k
    BA, LB = edge(DM, n, MOT, B, A, kb, ia, Ls, action)                    # B shot k  -> A shot i
    if shots == 2:
        C = AB + BA.T
        i, k = np.unravel_index(np.argmin(C), C.shape)
        return C[i, k], [(A, int(ia[i]), int(LA[i, k])), (B, int(kb[k]), int(LB[k, i]))]
    # 4 shots: A(i) -> B(k) -> A(j) -> B(m) -> A(i); X[i, j] = best B shot between A(i) and A(j)
    gap = lmax + 10
    X = np.empty((len(ia), len(ia))); K = np.empty_like(X, dtype=int)
    for r in range(0, len(ia), 64):
        t = AB[r:r + 64, :, None] + BA[None]                               # (i, k, j)
        K[r:r + 64] = t.argmin(1); X[r:r + 64] = t.min(1)
    C = X + X.T
    C[abs(ia[:, None] - ia[None]) < gap] = np.inf                          # two different A moments
    best = (np.inf, None)
    for flat in np.argsort(C, axis=None)[:300]:
        i, j = np.unravel_index(flat, C.shape)
        if C[i, j] >= best[0]:
            break
        k, m = K[i, j], K[j, i]
        if abs(kb[k] - kb[m]) < gap:                                       # B moments collide: solve this (i, j) exactly
            p1 = AB[i] + BA[:, j]; p2 = AB[j] + BA[:, i]
            T = p1[:, None] + p2[None]
            T[abs(kb[:, None] - kb[None]) < gap] = np.inf
            k, m = np.unravel_index(np.argmin(T), T.shape)
            cost = T[k, m]
        else:
            cost = C[i, j]
        if cost < best[0]:
            best = (cost, [(A, int(ia[i]), int(LA[i, k])), (B, int(kb[k]), int(LB[k, j])),
                           (A, int(ia[j]), int(LA[j, m])), (B, int(kb[m]), int(LB[m, i]))])
    return best if best[1] else None


def cmd_loop(a):
    DM, n, MOT = match_matrices(a)
    Ls = range(round((a.shot_min or a.shot) * FPS), round((a.shot_max or a.shot) * FPS) + 1)
    clips = sorted(n)
    pairs = [tuple(map(int, p.split(","))) for p in a.pairs] if a.pairs else \
            [(x, y) for i, x in enumerate(clips) for y in clips[i + 1:]]
    found = []
    for A, B in pairs:
        r = best_loop(DM, n, MOT, A, B, Ls, a.loop_shots, a.action)
        if r:
            found.append((r[0], A, B, r[1]))
    found.sort(key=lambda r: r[0])
    for cost, A, B, shots in found:
        print(f"clips {A}+{B}: cost {cost:+.3f}  " + "  ".join(f"c{c}@{f}+{L / FPS:.2f}s" for c, f, L in shots))
    os.makedirs(a.loop_dir, exist_ok=True)
    for cost, A, B, shots in found[:a.top]:
        plan = dict(torso=a.torso, hip_y=a.hip_y, loop=True,
                    shots=[dict(clip=c, file=a.clips[c], fin=f, fout=f + L) for c, f, L in shots])
        base = os.path.join(a.loop_dir, f"loop_{A}_{B}")
        json.dump(plan, open(base + ".json", "w"), indent=1)
        R = Renderer(a, plan)
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                                "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                                "-pix_fmt", "yuv420p", "-movflags", "+faststart", base + ".mp4"], stdin=subprocess.PIPE)
        frames = [R.frame(s["clip"], f) for s in plan["shots"] for f in range(s["fin"], s["fout"])]
        for _ in range(a.loop_repeat):
            for img in frames:
                enc.stdin.write(img.tobytes())
        enc.stdin.close(); enc.wait()
        # seam sheet: every cut including the wrap-around
        sh = plan["shots"]; tiles = []
        for x, y in zip(sh, sh[1:] + sh[:1]):
            p = cv2.resize(R.frame(x["clip"], x["fout"]), (270, 480))      # frame the cut replaces
            q = cv2.resize(R.frame(y["clip"], y["fin"]), (270, 480))
            tiles.append(np.hstack([p, q, cv2.addWeighted(p, .5, q, .5, 0), np.zeros((480, 10, 3), np.uint8)]))
        cv2.imwrite(base + "_seams.jpg", np.vstack(tiles))
        print("loop ->", base + ".mp4")


# ---------------------------------------------------------------- 3/4. render
vy, vx = np.mgrid[0:H, 0:W].astype(np.float32)
VIG = np.clip(1 - 0.22 * (((vx - W / 2) / (W / 2)) ** 2 + ((vy - H / 2) / (H / 2)) ** 2) ** 1.4, 0.7, 1)[..., None]
LUT = np.clip(255 * (0.5 + 0.5 * np.tanh(1.3 * (np.arange(256) / 255 - 0.5)) / np.tanh(0.65)), 0, 255).astype(np.uint8)


def grade(img):
    img = cv2.LUT(img, LUT)                                                    # gentle S-curve
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * 1.10, 0, 255)
    img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR).astype(np.float32)
    img[..., 2] *= 1.03; img[..., 0] *= 0.97                                   # warm
    img = img + 0.35 * (img - cv2.GaussianBlur(img, (0, 0), 2.0))              # sharpen upscaled sources
    return np.clip(img * VIG, 0, 255).astype(np.uint8)


def caption_mask(frames, top_frac):
    """Burned-in white caption text: pixels bright in >80% of frames, limited to the top part of the frame."""
    g = frames[::10].astype(np.float32).mean(-1)
    m = ((g > 215).mean(0) > 0.8).astype(np.uint8) * 255
    m[int(top_frac * m.shape[0]):] = 0
    return cv2.dilate(m, np.ones((5, 5), np.uint8), iterations=2) if m.any() else None


class Renderer:
    def __init__(self, a, plan):
        self.a = a
        self.clips = sorted({s["clip"] for s in plan["shots"]})
        self.frames = {c: load_frames(a.clips[c]) for c in self.clips}
        self.cam = {c: camera(np.load(pose_path(a.work, c)), probe_size(a.clips[c]), plan["torso"], plan["hip_y"])
                    for c in self.clips}
        self.mask = {}
        if a.remove_captions:
            for c in self.clips:
                m = caption_mask(self.frames[c], a.remove_captions)
                if m is not None:
                    self.mask[c] = m

    def frame(self, c, f):
        s, tx, ty = self.cam[c][f]
        src = self.frames[c][f]
        if c in self.mask:
            src = cv2.inpaint(src, self.mask[c], 5, cv2.INPAINT_TELEA)
        M = np.array([[s, 0, tx], [0, s, ty]], np.float32)
        img = cv2.warpAffine(src, M, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        return grade(img) if not self.a.no_grade else img


def cmd_preview(a):
    plan = json.load(open(a.plan))
    R, shots = Renderer(a, plan), plan["shots"]
    tiles = []
    for x, y in zip(shots, shots[1:]):
        p = cv2.resize(R.frame(x["clip"], x["fout"] - 1), (270, 480))
        q = cv2.resize(R.frame(y["clip"], y["fin"]), (270, 480))
        tiles.append(np.hstack([p, q, cv2.addWeighted(p, .5, q, .5, 0), np.zeros((480, 10, 3), np.uint8)]))
    while len(tiles) % 2:
        tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(a.preview, np.vstack([np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]))
    print("cut sheet ->", a.preview)


def cmd_render(a):
    plan = json.load(open(a.plan))
    R = Renderer(a, plan)
    dur = sum(s["fout"] - s["fin"] for s in plan["shots"]) / FPS
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    audio = a.audio if a.audio is not None else None
    if audio is not None:
        src = a.clips[int(audio)] if str(audio).isdigit() else audio
        cmd += ["-ss", str(a.audio_start), "-i", src, "-map", "0:v", "-map", "1:a",
                "-af", f"afade=t=out:st={max(0, dur - 0.5)}:d=0.5", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-t", f"{dur}", "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", a.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for s in plan["shots"]:
        for f in range(s["fin"], s["fout"]):
            enc.stdin.write(R.frame(s["clip"], f).tobytes())
    enc.stdin.close()
    if enc.wait():
        sys.exit("ffmpeg failed")
    print("video ->", a.out)


def cmd_all(a):
    cmd_poses(a); cmd_plan(a); cmd_preview(a); cmd_render(a)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["poses", "plan", "preview", "render", "all", "loop"])
    ap.add_argument("--clips", nargs="+", required=True, help="source videos (order defines clip ids 0..N-1)")
    ap.add_argument("--work", default="matchcut_work", help="cache dir for poses/model")
    ap.add_argument("--plan", default=None, help="plan json (default <work>/plan.json)")
    ap.add_argument("--preview", default=None, help="cut sheet jpg (default <work>/cuts.jpg)")
    ap.add_argument("--out", default="matchcut.mp4")
    ap.add_argument("--duration", type=float, default=15.0, help="total seconds")
    ap.add_argument("--shot", type=float, default=1.0, help="exact shot length in seconds")
    ap.add_argument("--shot-min", type=float, default=None, help="variable shots: min seconds (overrides --shot)")
    ap.add_argument("--shot-max", type=float, default=None, help="variable shots: max seconds")
    ap.add_argument("--torso", type=float, default=600, help="on-screen shoulder-to-hip length in px (smaller = more full body)")
    ap.add_argument("--hip-y", type=float, default=1500, help="target on-screen hip height in px")
    ap.add_argument("--motion-weight", type=float, default=6.0, help="how much matching movement direction matters vs pose")
    ap.add_argument("--beam", type=int, default=4000)
    ap.add_argument("--audio", default=None, help="clip id or audio/video file for the soundtrack (default: silent)")
    ap.add_argument("--audio-start", type=float, default=0.0)
    ap.add_argument("--remove-captions", type=float, default=0.0, metavar="TOP_FRAC",
                    help="inpaint static white caption text in the top TOP_FRAC of each clip (e.g. 0.3)")
    ap.add_argument("--loop-shots", type=int, default=2, choices=[2, 4], help="loop: shots per cycle (A B or A B A' B')")
    ap.add_argument("--pairs", nargs="*", help="loop: clip pairs like 0,3 1,4 (default: every pair)")
    ap.add_argument("--top", type=int, default=3, help="loop: render the N best pairs")
    ap.add_argument("--action", type=float, default=0.5, help="loop: reward for movement inside shots (avoid static loops)")
    ap.add_argument("--loop-repeat", type=int, default=1, help="loop: cycles written to the mp4")
    ap.add_argument("--loop-dir", default=None, help="loop: output dir (default <work>/loops)")
    ap.add_argument("--no-grade", action="store_true")
    ap.add_argument("--force", action="store_true", help="recompute cached poses")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    a.clips = [os.path.abspath(c) for c in a.clips]
    a.plan = a.plan or os.path.join(a.work, "plan.json")
    a.preview = a.preview or os.path.join(a.work, "cuts.jpg")
    a.loop_dir = a.loop_dir or os.path.join(a.work, "loops")
    {"loop": cmd_loop, "poses": cmd_poses, "plan": cmd_plan, "preview": cmd_preview, "render": cmd_render, "all": cmd_all}[a.command](a)


if __name__ == "__main__":
    main()
