"""
금속 사각 너트 비전 QC — 데이터 전처리 검증 스크립트
(report/data_preprocessing.md 개정판의 수치를 재현하는 코드)

[실행]
  로컬:  python preprocessing_test.py --data-root ../industrial-nut-defect-synthetic-dataset
  코랩:  1) 데이터셋 폴더를 드라이브에 올리고 마운트
         2) !pip install -q opencv-python-headless scikit-learn matplotlib
         3) !python preprocessing_test.py --data-root /content/drive/MyDrive/industrial-nut-defect-synthetic-dataset --out /content/preprocess_out

[구성]
  실험 1. 데이터셋 통계 (밝기, 배경, 채도, 부품 면적, 선명도)
  실험 2. 중복 탐지 (MD5 완전 중복 + pHash 유사 그룹)          → A1
  실험 3. 도메인 태깅 (material / scale / blur / aspect)        → A2~A4
  실험 4. 편향(Shortcut) 테스트: 전역 통계만으로 정상/불량 분류 → 1.2절
  실험 5. ROI 검출 비교: Otsu vs 그래디언트 + 볼록 껍질          → B3
  실험 6. 엣지 전처리 파이프라인 B1~B8 전체 + 단계별 지연시간

[출력] --out 폴더
  image_stats.csv, tags.csv, roi_compare.jpg, pipeline_samples.jpg, summary.txt
"""
import argparse
import csv
import glob
import hashlib
import os
import re
import time
from collections import Counter, defaultdict

import cv2
import numpy as np
import matplotlib
matplotlib.use("Agg")  # 코랩/서버에서도 파일로 저장
import matplotlib.pyplot as plt


CLASSES = {"synthetic_defect": 1, "synthetic_non_defect": 0}

# 육안 분류 결과 (근사치, 검수 필요) — 불량 클래스 파일 번호
ZINC_IDS = {2, 13, 16, 18, 27, 38, 44, 45, 49, 60, 64, 72, 79, 80, 88, 93, 104, 105, 110, 122, 127, 130}
RUST_IDS = {3, 5, 17, 23, 30, 46, 53, 56, 59, 63, 69, 81, 90, 112, 115, 117, 119, 126, 128}

# 임계값
BLUR_LAPVAR = 30.0      # 라플라시안 분산 미만이면 blurry
SMALL_AREA = 0.12       # 부품 면적 비율 미만이면 small
ASPECT_DISTORT = 1.3    # 외곽 박스 장단변 비 초과면 왜곡
PHASH_DIST = 6          # pHash 해밍 거리 이하면 유사 이미지
BG_TARGET = 220         # 배경 밝기 정규화 목표값
OUT_SIZE = 256          # PatchCore 입력 크기
CROP_MARGIN = 0.10      # 크롭 여백 (사방 10%)


# -------------------------------------------------------------
# [공통] 데이터 로딩
# -------------------------------------------------------------
def list_images(data_root):
    items = []
    for cls, label in CLASSES.items():
        files = glob.glob(os.path.join(data_root, cls, "*.png"))
        files.sort(key=lambda p: int(re.findall(r"(\d+)\.png$", p)[0]))
        for f in files:
            idx = int(re.findall(r"(\d+)\.png$", f)[0])
            items.append({"path": f, "file": os.path.basename(f), "cls": cls, "label": label, "idx": idx})
    if not items:
        raise SystemExit(f"이미지를 찾지 못했습니다: {data_root}/synthetic_defect, synthetic_non_defect 폴더를 확인하세요.")
    return items


def border_pixels(img, width=20):
    return np.concatenate([img[:width].ravel(), img[-width:].ravel(),
                           img[:, :width].ravel(), img[:, -width:].ravel()])


# -------------------------------------------------------------
# [실험 1] 이미지 통계
# -------------------------------------------------------------
def image_stats(bgr):
    rgb = bgr[:, :, ::-1].astype(np.float32)
    g = rgb.mean(axis=2)
    sat = (rgb.max(axis=2) - rgb.min(axis=2)).mean()      # 채도 (0이면 완전 흑백)
    b = border_pixels(g)
    bg, bgstd = b.mean(), b.std()
    fg = np.abs(g - bg) > 35                               # 배경과 다른 픽셀 = 부품
    area = fg.mean()
    lap = cv2.Laplacian(g, cv2.CV_32F)
    hi = (g >= 250).mean()                                 # 포화 비율

    # 고주파 에너지 비율 (업스케일된 흐린 이미지는 낮음)
    F = np.abs(np.fft.fftshift(np.fft.fft2(g - g.mean())))
    h, w = g.shape
    yy, xx = np.ogrid[:h, :w]
    r = np.hypot(yy - h // 2, xx - w // 2)
    hf = F[r > min(h, w) / 4].sum() / F.sum()

    return {"mean": g.mean(), "std": g.std(), "bg": bg, "bgstd": bgstd, "sat": sat,
            "area": area, "lapvar": lap.var(), "hi": hi, "hf": hf}


# -------------------------------------------------------------
# [실험 2] 중복 탐지 — MD5 + pHash
# -------------------------------------------------------------
def md5_of(path):
    with open(path, "rb") as fp:
        return hashlib.md5(fp.read()).hexdigest()


def phash(gray, hash_size=8):
    """DCT 기반 지각 해시 (imagehash.phash와 같은 방식, 64bit)"""
    small = cv2.resize(gray, (hash_size * 4, hash_size * 4), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(small)[:hash_size, :hash_size]
    return (dct > np.median(dct)).ravel()


def group_duplicates(items):
    # 완전 중복
    by_md5 = defaultdict(list)
    for it in items:
        by_md5[it["md5"]].append(it["file"])
    exact = [v for v in by_md5.values() if len(v) > 1]

    # 유사 이미지: 같은 클래스 안에서 해밍 거리 PHASH_DIST 이하를 Union-Find로 묶음
    parent = {it["file"]: it["file"] for it in items}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for cls in CLASSES:
        sub = [it for it in items if it["cls"] == cls]
        for i, a in enumerate(sub):
            for b in sub[i + 1:]:
                if np.count_nonzero(a["phash"] != b["phash"]) <= PHASH_DIST:
                    parent[find(a["file"])] = find(b["file"])

    roots = {}
    for it in items:
        root = find(it["file"])
        it["group_id"] = roots.setdefault(root, len(roots))
    return exact


# -------------------------------------------------------------
# [실험 4] 편향(Shortcut) 테스트
# -------------------------------------------------------------
def shortcut_test(items):
    """결함을 보지 않는 전역 통계만으로 정상/불량을 얼마나 맞히는지 (50%에 가까울수록 좋음)"""
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, cross_val_score
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return {"skipped": "scikit-learn 미설치"}

    y = np.array([it["label"] for it in items])
    cv = StratifiedKFold(5, shuffle=True, random_state=0)
    feature_sets = {
        "선명도(lapvar)": ["lapvar"],
        "고주파 비율(hf)": ["hf"],
        "채도(sat)": ["sat"],
        "배경 밝기(bg)": ["bg"],
        "부품 면적(area)": ["area"],
        "전부": ["lapvar", "hf", "sat", "area", "bg", "bgstd"],
    }
    result = {}
    for name, keys in feature_sets.items():
        X = np.array([[np.log1p(it[k]) if k == "lapvar" else it[k] for k in keys] for it in items])
        model = make_pipeline(StandardScaler(), LogisticRegression())
        result[name] = cross_val_score(model, X, y, cv=cv).mean()
    return result


# -------------------------------------------------------------
# [실험 5] ROI 검출 — Otsu(기존) vs 그래디언트 + 볼록 껍질(개정)
# -------------------------------------------------------------
def roi_otsu(gray):
    """기존 방식: Otsu 이진화 후 최대 윤곽. solidity가 낮으면 부품 일부만 잡힌 것"""
    b = cv2.GaussianBlur(gray, (5, 5), 0)
    _, m = cv2.threshold(b, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(contours, key=cv2.contourArea)
    hull_area = max(cv2.contourArea(cv2.convexHull(c)), 1.0)
    solidity = cv2.contourArea(c) / hull_area
    return c, solidity


def roi_gradient(gray, valid=None):
    """
    개정 방식 (B3):
    1/2 축소 → 가우시안 블러 → Sobel 그래디언트 → 테두리 기준 적응형 임계값
    → 작은 잡음 제거 → 엣지 점 전체의 볼록 껍질 = 부품 영역
    valid: 검사할 영역 마스크 (패딩 경계를 엣지로 오인하지 않도록 할 때 사용)
    """
    h, w = gray.shape
    s = cv2.resize(gray, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    b = cv2.GaussianBlur(s, (5, 5), 0)
    mag = cv2.magnitude(cv2.Sobel(b, cv2.CV_32F, 1, 0), cv2.Sobel(b, cv2.CV_32F, 0, 1))

    # 배경은 매끈하므로 테두리 그래디언트 중앙값이 배경 잡음 수준
    t = float(np.clip(np.median(border_pixels(mag, 8)) * 4, 12, 60))
    m = (mag > t).astype(np.uint8)
    if valid is not None:
        m &= cv2.resize(valid, (w // 2, h // 2), interpolation=cv2.INTER_NEAREST) > 0

    # 엣지를 팽창시켜 부품 엣지끼리 이어 붙인 뒤, 가장 큰 덩어리만 부품으로 채택
    # (원거리 소형 촬영에서 배경의 점·잡티가 ROI에 섞이는 문제 방지)
    merged = cv2.dilate(m, np.ones((9, 9), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(merged)
    if n <= 1:
        return None
    biggest = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    pts = cv2.findNonZero(((lab == biggest) & (m > 0)).astype(np.uint8))
    if pts is None or len(pts) < 30:
        return None
    return cv2.convexHull(pts) * 2  # 원본 좌표로 복원


# -------------------------------------------------------------
# [실험 6] 엣지 전처리 파이프라인 B1~B8
# -------------------------------------------------------------
class EdgePreprocessor:
    def __init__(self, out_size=OUT_SIZE, bg_target=BG_TARGET, margin=CROP_MARGIN,
                 clahe_clip=1.5, clahe_grid=(8, 8), mean=None, std=None):
        self.out_size = out_size
        self.bg_target = bg_target
        self.margin = margin
        self.clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=clahe_grid)
        self.mean = mean
        self.std = std

    def b1_gray(self, bgr):
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    def b2_normalize_bg(self, gray):
        bg = float(np.median(border_pixels(gray)))
        gain = self.bg_target / max(bg, 1.0)
        return cv2.convertScaleAbs(gray, alpha=gain, beta=0)

    def b3_roi(self, gray):
        """반환: (minAreaRect, roi_fail 여부)"""
        hull = roi_gradient(gray)
        h, w = gray.shape
        if hull is None:
            return ((w / 2, h / 2), (w, h), 0.0), True
        ratio = cv2.contourArea(hull) / (h * w)
        if ratio > 0.90 or ratio < 0.08:  # 안전장치: 원본 전체 사용
            return ((w / 2, h / 2), (w, h), 0.0), True
        return cv2.minAreaRect(hull), False

    def b4_b6_align_crop_pad(self, gray, rect, border_value=None):
        """회전 정렬(B4) + 크롭·크기 정규화(B5) + 배경색 패딩(B6)을 warpAffine 1회로 처리"""
        (cx, cy), (rw, rh), angle = rect
        side = max(rw, rh) * (1 + 2 * self.margin)
        scale = self.out_size / max(side, 1.0)
        M = cv2.getRotationMatrix2D((cx, cy), angle, scale)
        M[0, 2] += self.out_size / 2 - cx
        M[1, 2] += self.out_size / 2 - cy
        return cv2.warpAffine(gray, M, (self.out_size, self.out_size),
                              flags=cv2.INTER_LINEAR,
                              borderValue=self.bg_target if border_value is None else border_value)

    def b7_clahe(self, img):
        return self.clahe.apply(img)

    def b8_normalize(self, img):
        x = img.astype(np.float32) / 255.0
        if self.mean is not None and self.std is not None:
            x = (x - self.mean) / max(self.std, 1e-6)
        return x

    def __call__(self, bgr, timings=None):
        steps = {}
        t = time.perf_counter()

        def lap(name):
            nonlocal t
            now = time.perf_counter()
            if timings is not None:
                timings[name].append((now - t) * 1000)
            t = now

        g = self.b1_gray(bgr); lap("B1 gray")
        n = self.b2_normalize_bg(g); lap("B2 bg_norm")
        rect, fail = self.b3_roi(g); lap("B3 roi")
        a = self.b4_b6_align_crop_pad(n, rect); lap("B4-6 align/crop/pad")
        c = self.b7_clahe(a); lap("B7 clahe")
        x = self.b8_normalize(c); lap("B8 normalize")

        steps.update(gray=g, bg_norm=n, rect=rect, aligned=a, clahe=c, tensor=x,
                     roi_fail=fail, low_res=max(rect[1]) < 200)
        return steps


def residual_angle(prep, gray, rect, aligned):
    """
    정렬 결과 검증: 정렬 후 다시 잰 외곽 각도가 0°/90°에서 벗어난 정도.
    패딩 경계선이 엣지로 잡히지 않도록 원본 이미지가 있던 영역만 검사함.
    """
    ones = np.full(gray.shape, 255, np.uint8)
    valid = prep.b4_b6_align_crop_pad(ones, rect, border_value=0)
    valid = cv2.erode(valid, np.ones((7, 7), np.uint8))
    hull = roi_gradient(aligned, valid)
    if hull is None:
        return None
    ang = cv2.minAreaRect(hull)[2] % 90
    return min(ang, 90 - ang)


# -------------------------------------------------------------
# [시각화]
# -------------------------------------------------------------
def save_roi_compare(items, out_path, names):
    by_file = {it["file"]: it for it in items}
    picks = [by_file[n] for n in names if n in by_file]
    fig, axes = plt.subplots(2, len(picks), figsize=(2.6 * len(picks), 5.4))
    for j, it in enumerate(picks):
        gray = cv2.imread(it["path"], cv2.IMREAD_GRAYSCALE)
        c, sol = roi_otsu(gray)
        v1 = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        cv2.drawContours(v1, [c], -1, (255, 0, 0), 4)
        hull = roi_gradient(gray)
        v2 = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        if hull is not None:
            cv2.drawContours(v2, [hull], -1, (255, 0, 0), 4)
            cv2.drawContours(v2, [np.int32(cv2.boxPoints(cv2.minAreaRect(hull)))], -1, (0, 180, 0), 3)
        axes[0, j].imshow(v1); axes[0, j].set_title(f"Otsu\n{it['file'][6:-4]} sol={sol:.2f}", fontsize=8)
        axes[1, j].imshow(v2); axes[1, j].set_title("Gradient+Hull", fontsize=8)
    for ax in axes.ravel():
        ax.axis("off")
    plt.tight_layout(); plt.savefig(out_path, dpi=90); plt.close()


def save_pipeline_samples(items, prep, out_path, names):
    by_file = {it["file"]: it for it in items}
    picks = [by_file[n] for n in names if n in by_file]
    cols = ["raw", "B2 bg_norm + ROI", "B4-6 aligned", "B7 CLAHE"]
    fig, axes = plt.subplots(len(picks), 4, figsize=(10, 2.6 * len(picks)))
    for i, it in enumerate(picks):
        bgr = cv2.imread(it["path"])
        s = prep(bgr)
        roi_v = cv2.cvtColor(s["bg_norm"], cv2.COLOR_GRAY2RGB)
        cv2.drawContours(roi_v, [np.int32(cv2.boxPoints(s["rect"]))], -1, (0, 180, 0), 4)
        imgs = [bgr[:, :, ::-1], roi_v, s["aligned"], s["clahe"]]
        for j, im in enumerate(imgs):
            axes[i, j].imshow(im, cmap="gray", vmin=0, vmax=255)
            axes[i, j].set_title(f"{it['file'][6:-4]} | {cols[j]}" if j == 0 else cols[j], fontsize=8)
            axes[i, j].axis("off")
    plt.tight_layout(); plt.savefig(out_path, dpi=90); plt.close()


def pct(v, qs=(0, 10, 50, 90, 100)):
    return " / ".join(f"{np.percentile(v, q):.3f}" for q in qs)


# -------------------------------------------------------------
# [실행]
# -------------------------------------------------------------
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default=os.path.join(here, "..", "industrial-nut-defect-synthetic-dataset"))
    ap.add_argument("--out", default=os.path.join(here, "preprocess_out"))
    args, _ = ap.parse_known_args()  # 코랩 커널 인자 무시
    os.makedirs(args.out, exist_ok=True)
    log_lines = []

    def log(msg=""):
        print(msg)
        log_lines.append(msg)

    items = list_images(args.data_root)
    log(f"[0] 이미지 {len(items)}장 로드: " + ", ".join(f"{c} {sum(it['cls'] == c for it in items)}장" for c in CLASSES))

    # ---- 실험 1: 통계 ----
    for it in items:
        bgr = cv2.imread(it["path"])
        it["h"], it["w"] = bgr.shape[:2]
        it.update(image_stats(bgr))
        it["md5"] = md5_of(it["path"])
        it["phash"] = phash(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))

    log("\n[1] 데이터셋 통계 (min / p10 / median / p90 / max)")
    for cls in CLASSES:
        sub = [it for it in items if it["cls"] == cls]
        log(f"  - {cls}: 해상도 {dict(Counter((it['w'], it['h']) for it in sub))}")
        for k in ["bg", "sat", "area", "lapvar", "hi"]:
            log(f"      {k:7s} {pct([it[k] for it in sub])}")

    # ---- 실험 2: 중복 ----
    exact = group_duplicates(items)
    log("\n[2] 중복 탐지 (A1)")
    log(f"  - 완전 중복 {len(exact)}그룹, 제거 대상 {sum(len(g) - 1 for g in exact)}장")
    for g in exact:
        log(f"      {g}")
    for cls in CLASSES:
        n_groups = len({it["group_id"] for it in items if it["cls"] == cls})
        log(f"  - {cls}: 유사 이미지 묶음 후 고유 그룹 {n_groups}개 (pHash 거리 ≤ {PHASH_DIST})")

    # ---- 실험 3: 도메인 태깅 ----
    for it in items:
        if it["label"] == 1 and it["idx"] in ZINC_IDS:
            it["material"] = "zinc"
        elif it["label"] == 1 and it["idx"] in RUST_IDS:
            it["material"] = "rust"
        else:
            it["material"] = "plain"
        it["scale"] = "small" if it["area"] < SMALL_AREA else "normal"
        it["blur"] = "blurry" if it["lapvar"] < BLUR_LAPVAR else "sharp"
        hull = roi_gradient(cv2.imread(it["path"], cv2.IMREAD_GRAYSCALE))
        (_, _), (rw, rh), _ = cv2.minAreaRect(hull) if hull is not None else ((0, 0), (1, 1), 0)
        it["aspect"] = max(rw, rh) / max(min(rw, rh), 1)
        it["aspect_distorted"] = it["aspect"] > ASPECT_DISTORT

    log("\n[3] 도메인 태그 (A2~A4)")
    for cls in CLASSES:
        sub = [it for it in items if it["cls"] == cls]
        log(f"  - {cls}: material {dict(Counter(it['material'] for it in sub))}, "
            f"small {sum(it['scale'] == 'small' for it in sub)}, "
            f"blurry {sum(it['blur'] == 'blurry' for it in sub)}, "
            f"aspect>{ASPECT_DISTORT} {sum(it['aspect_distorted'] for it in sub)}")

    # ---- 실험 4: 편향 테스트 ----
    log("\n[4] 편향 테스트 — 전역 통계만으로 분류한 정확도 (5-fold, 50%에 가까울수록 좋음)")
    for name, acc in shortcut_test(items).items():
        log(f"  - {name:16s} {acc if isinstance(acc, str) else f'{acc * 100:.1f}%'}")
    plain = [it for it in items if it["material"] == "plain"]
    log("  [참고] material=plain만 남긴 경우 (A2 적용 후)")
    for name, acc in shortcut_test(plain).items():
        log(f"  - {name:16s} {acc if isinstance(acc, str) else f'{acc * 100:.1f}%'}")

    # ---- 실험 5: ROI 비교 ----
    otsu_bad, grad_ms, grad_fail = [], [], []
    for it in items:
        gray = cv2.imread(it["path"], cv2.IMREAD_GRAYSCALE)
        _, sol = roi_otsu(gray)
        if sol < 0.85:
            otsu_bad.append(it["file"])
        t0 = time.perf_counter()
        hull = roi_gradient(gray)
        grad_ms.append((time.perf_counter() - t0) * 1000)
        ratio = 0 if hull is None else cv2.contourArea(hull) / gray.size
        if hull is None or ratio > 0.90 or ratio < 0.08:
            grad_fail.append(it["file"])
    log("\n[5] ROI 검출 비교 (B3)")
    log(f"  - Otsu: solidity < 0.85 (부품 일부만 검출) {len(otsu_bad)}/{len(items)}장 ({len(otsu_bad) / len(items) * 100:.1f}%)")
    log(f"  - Gradient+Hull: 안전장치 발동 {len(grad_fail)}장 {grad_fail}")
    log(f"  - Gradient+Hull 처리 시간: median {np.median(grad_ms):.2f} ms / p95 {np.percentile(grad_ms, 95):.2f} ms")
    save_roi_compare(items, os.path.join(args.out, "roi_compare.jpg"),
                     ["synth_defect_10.png", "synth_defect_20.png", "synth_defect_91.png", "synth_defect_2.png",
                      "synth_non_defect_12.png", "synth_non_defect_99.png", "synth_non_defect_105.png"])

    # ---- 실험 6: 전체 파이프라인 ----
    # B8 정규화 통계: 정상 이미지(plain)의 전처리 결과로 계산
    prep = EdgePreprocessor()
    normals = [prep(cv2.imread(it["path"]))["clahe"] for it in items if it["label"] == 0]
    stack = np.stack(normals).astype(np.float32) / 255.0
    prep.mean, prep.std = float(stack.mean()), float(stack.std())

    timings = defaultdict(list)
    residuals, misaligned, n_fail, n_lowres = [], [], 0, 0
    for it in items:
        s = prep(cv2.imread(it["path"]), timings)
        n_fail += s["roi_fail"]
        n_lowres += s["low_res"]
        it["roi_fail"], it["low_res"] = s["roi_fail"], s["low_res"]
        # 화면 밖으로 잘린 부품은 외곽을 다시 잴 수 없으므로 정렬 검증에서 제외
        x, y, w, h = cv2.boundingRect(roi_gradient(s["gray"]))
        H, W = s["gray"].shape
        if x <= 4 or y <= 4 or x + w >= W - 4 or y + h >= H - 4:
            continue
        r = residual_angle(prep, s["gray"], s["rect"], s["aligned"])
        if r is not None:
            residuals.append(r)
            if r > 5:
                misaligned.append(it["file"])

    log("\n[6] 엣지 전처리 파이프라인 B1~B8")
    log(f"  - 출력 텐서: ({OUT_SIZE}, {OUT_SIZE}) float32, 정규화 mean={prep.mean:.3f} std={prep.std:.3f}")
    log(f"  - roi_fail {n_fail}장, low_res(부품 한 변 < 200px) {n_lowres}장")
    log(f"  - 정렬 후 잔여 각도 (화면 안에 온전히 들어온 {len(residuals)}장 기준): "
        f"median {np.median(residuals):.1f}° / p95 {np.percentile(residuals, 95):.1f}° (목표 ≤ 5°), 초과 {misaligned}")
    log("  - 단계별 지연시간 (median / p95, 이 PC 기준 — 엣지 PC 재측정 필요)")
    total = np.zeros(len(items))
    for name, v in timings.items():
        v = np.array(v); total += v
        log(f"      {name:22s} {np.median(v):6.2f} / {np.percentile(v, 95):6.2f} ms")
    log(f"      {'합계':22s} {np.median(total):6.2f} / {np.percentile(total, 95):6.2f} ms (목표 ≤ 6.0ms)")

    # 전처리(B1~B7) 결과에 편향 테스트를 다시 적용 — 전처리만으로 편향이 줄어드는지 확인
    crop_items = []
    for it in items:
        c = prep(cv2.imread(it["path"]))["clahe"]
        crop_items.append({**image_stats(cv2.cvtColor(c, cv2.COLOR_GRAY2BGR)),
                           "label": it["label"], "material": it["material"]})
    log("  - 전처리 후 편향 테스트 (전체 / material=plain)")
    res_all = shortcut_test(crop_items)
    res_plain = shortcut_test([c for c in crop_items if c["material"] == "plain"])
    for name in res_all:
        fmt = lambda v: v if isinstance(v, str) else f"{v * 100:.1f}%"
        log(f"      {name:16s} {fmt(res_all[name])} / {fmt(res_plain[name])}")

    save_pipeline_samples(items, prep,os.path.join(args.out, "pipeline_samples.jpg"),
                          ["synth_defect_1.png", "synth_defect_31.png", "synth_defect_86.png",
                           "synth_non_defect_1.png", "synth_non_defect_42.png"])

    # ---- 저장 ----
    stat_keys = ["file", "cls", "label", "w", "h", "mean", "std", "bg", "bgstd", "sat", "area", "lapvar", "hi", "hf", "md5"]
    with open(os.path.join(args.out, "image_stats.csv"), "w", newline="", encoding="utf-8") as fp:
        wr = csv.DictWriter(fp, fieldnames=stat_keys, extrasaction="ignore")
        wr.writeheader(); wr.writerows(items)
    tag_keys = ["file", "cls", "label", "group_id", "material", "scale", "blur", "aspect", "aspect_distorted", "roi_fail", "low_res"]
    with open(os.path.join(args.out, "tags.csv"), "w", newline="", encoding="utf-8") as fp:
        wr = csv.DictWriter(fp, fieldnames=tag_keys, extrasaction="ignore")
        wr.writeheader(); wr.writerows(items)
    with open(os.path.join(args.out, "summary.txt"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(log_lines))
    log(f"\n[완료] 결과 저장: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
