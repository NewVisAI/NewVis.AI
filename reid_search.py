"""
Deferred / on-demand cross-camera Re-ID.

In deferred mode (REID_DEFERRED=1) the live pipeline does NOT run OSNet — it saves
a few representative person crops per (camera, global-id) track instead. Full OSNet
matching runs ONLY here, when someone asks "where else did this person go?", and
only over the relevant crops. This moves the heaviest continuous GPU cost off the
24/7 path and onto a rare, bounded search.

    reid_search.save_track_crop(camera_id, gid, track_id, frame, bbox)   # live, cheap
    reid_search.search(query_gid, within_seconds=1800)                    # on-demand OSNet
"""

import os
import time
import glob
from typing import Dict, List, Optional

import cv2
import numpy as np

CROP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reid_crops")
MAX_CROPS_PER_TRACK = 3          # keep a few best crops per (camera,gid)
_saved_counts: Dict[str, int] = {}   # (camera,gid) -> how many crops saved

# One shared full-OSNet embedder for search (built lazily, non-deferred).
_search_embedder = None


def _key(camera_id, gid) -> str:
    return f"c{camera_id}_g{gid}"


def save_track_crop(camera_id: int, gid: int, track_id: int, frame, bbox) -> None:
    """Cheap: save up to N representative crops per (camera, gid). No neural work."""
    try:
        key = _key(camera_id, gid)
        if _saved_counts.get(key, 0) >= MAX_CROPS_PER_TRACK:
            return
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = frame.shape[:2]
        x1 = max(0, min(x1, w - 1)); y1 = max(0, min(y1, h - 1))
        x2 = max(x1 + 1, min(x2, w)); y2 = max(y1 + 1, min(y2, h))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0 or (x2 - x1) < 24 or (y2 - y1) < 48:
            return  # skip tiny/low-quality crops
        os.makedirs(CROP_DIR, exist_ok=True)
        n = _saved_counts.get(key, 0)
        ts = int(time.time() * 1000)
        path = os.path.join(CROP_DIR, f"{key}_t{track_id}_{n}_{ts}.jpg")
        cv2.imwrite(path, crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        _saved_counts[key] = n + 1
    except Exception:
        pass


def _get_search_embedder():
    """A full OSNet embedder for search time — forced non-deferred."""
    global _search_embedder
    if _search_embedder is None:
        os.environ["REID_DEFERRED"] = "0"       # this embedder must run the real model
        import inference_config
        inference_config.reload()
        from reid import NeuralReIDEmbedder
        _search_embedder = NeuralReIDEmbedder()
    return _search_embedder


def _parse(path: str):
    base = os.path.basename(path)
    # c{cam}_g{gid}_t{track}_{n}_{ts}.jpg
    try:
        parts = base.split("_")
        cam = int(parts[0][1:]); gid = int(parts[1][1:]); ts = int(parts[4].split(".")[0])
        return cam, gid, ts
    except Exception:
        return None, None, None


def _embed_crop(embedder, crop) -> np.ndarray:
    h, w = crop.shape[:2]
    return embedder.extract(crop, (0, 0, w, h))


def search(query_gid: int, within_seconds: float = 1800, threshold: float = 0.5,
           top_k: int = 10) -> Dict:
    """Run OSNet ONLY NOW, over saved crops, to find the query GID on OTHER cameras.

    Returns matched (camera, gid) with similarity — a cross-camera trajectory built
    on demand instead of continuously."""
    t0 = time.time()
    embedder = _get_search_embedder()

    files = glob.glob(os.path.join(CROP_DIR, "*.jpg"))
    if not files:
        return {"query_gid": query_gid, "matches": [], "note": "no saved crops", "search_seconds": 0}

    # 1) build the query embedding(s) from this GID's crops
    query_embs, others = [], {}
    latest_ts = 0
    for f in files:
        cam, gid, ts = _parse(f)
        if gid is None:
            continue
        img = cv2.imread(f)
        if img is None:
            continue
        emb = _embed_crop(embedder, img)
        if gid == query_gid:
            query_embs.append(emb); latest_ts = max(latest_ts, ts)
        else:
            others.setdefault((cam, gid), []).append((emb, ts))

    if not query_embs:
        return {"query_gid": query_gid, "matches": [], "note": "query GID has no saved crops",
                "search_seconds": round(time.time() - t0, 3)}
    q = np.mean(np.stack(query_embs), axis=0)
    q = q / (np.linalg.norm(q) + 1e-9)

    # 2) compare against every OTHER (camera, gid) within the time window
    results = []
    n_embeds = len(query_embs)
    for (cam, gid), lst in others.items():
        best = 0.0
        for emb, ts in lst:
            if within_seconds and abs(ts - latest_ts) > within_seconds * 1000:
                continue
            e = emb / (np.linalg.norm(emb) + 1e-9)
            best = max(best, float(np.dot(q, e)))
        n_embeds += len(lst)
        if best >= threshold:
            results.append({"camera_id": cam, "gid": gid, "similarity": round(best, 3)})

    results.sort(key=lambda r: r["similarity"], reverse=True)
    return {
        "query_gid": query_gid,
        "matches": results[:top_k],
        "crops_scored": n_embeds,
        "search_seconds": round(time.time() - t0, 3),
    }
