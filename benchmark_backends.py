"""
Benchmark the active inference backends and estimate camera capacity per box.

This turns the "~10-20 cameras per Coral" claims from the deployment proposals
into measured numbers on whatever hardware/model is actually configured. It
times the detector and the ReID embedder that inference_config resolves, so
running it on a Coral/NPU box (with deployment.json pointed at the edge models)
reports that box's real throughput.

    python benchmark_backends.py --iters 100 --target-fps 5

Reports ms/frame, frames/sec, and cameras-per-box at the target analysis fps.
Backends that aren't installed are skipped, never fatal.
"""

import argparse
import time

import numpy as np

import inference_config


def _synthetic_frame(h=1080, w=1920):
    return np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)


def _bench(fn, iters, warmup=3):
    for _ in range(warmup):
        fn()
    start = time.perf_counter()
    for _ in range(iters):
        fn()
    elapsed = time.perf_counter() - start
    per_call_ms = (elapsed / iters) * 1000.0
    fps = iters / elapsed if elapsed > 0 else float("inf")
    return per_call_ms, fps


def bench_detector(iters):
    try:
        from detector import HumanDetector
    except Exception as exc:
        print(f"[detector] skipped (import failed: {exc})")
        return None
    try:
        det = HumanDetector()
    except Exception as exc:
        print(f"[detector] skipped (model load failed: {exc})")
        return None
    frame = _synthetic_frame()
    ms, fps = _bench(lambda: det.detect(frame), iters)
    print(f"[detector] {ms:6.1f} ms/frame   {fps:7.1f} frames/s")
    return fps


def bench_reid(iters):
    try:
        from reid import NeuralReIDEmbedder
    except Exception as exc:
        print(f"[reid] skipped (import failed: {exc})")
        return None
    embedder = NeuralReIDEmbedder()
    frame = _synthetic_frame(256, 128)
    bbox = (0, 0, 128, 256)
    ms, fps = _bench(lambda: embedder.extract(frame, bbox), iters)
    print(f"[reid:{embedder.backend_name}] {ms:6.2f} ms/crop   {fps:7.1f} crops/s")
    return fps


def main():
    parser = argparse.ArgumentParser(description="Benchmark Sentinel inference backends.")
    parser.add_argument("--iters", type=int, default=50)
    parser.add_argument("--target-fps", type=float, default=5.0,
                        help="Per-camera analysis frame rate for the capacity estimate")
    parser.add_argument("--people-per-frame", type=float, default=4.0,
                        help="Avg people per frame (drives ReID cost per frame)")
    args = parser.parse_args()

    print(f"Tier: {inference_config.get_tier()}")
    print(f"Detector weights: {inference_config.get_detector_weights() or '(default .pt)'}")
    print(f"ReID onnx: {inference_config.get_reid_onnx_path() or '-'} | "
          f"tflite: {inference_config.get_reid_tflite_path() or '-'}")
    print("-" * 60)

    det_fps = bench_detector(args.iters)
    reid_cps = bench_reid(args.iters * 4)
    print("-" * 60)

    # Capacity: per camera per second we run `target_fps` detections and
    # `target_fps * people_per_frame` ReID crops. Cameras/box is whichever
    # stage saturates first.
    if det_fps:
        det_budget = det_fps / args.target_fps
        print(f"Detector supports ~{det_budget:.0f} cameras/box at {args.target_fps:g} fps")
    if reid_cps:
        reid_budget = reid_cps / (args.target_fps * args.people_per_frame)
        print(f"ReID supports    ~{reid_budget:.0f} cameras/box at {args.target_fps:g} fps "
              f"({args.people_per_frame:g} people/frame)")
    if det_fps and reid_cps:
        cams = min(det_fps / args.target_fps,
                   reid_cps / (args.target_fps * args.people_per_frame))
        print(f"\n==> Combined estimate: ~{cams:.0f} cameras per box "
              f"(bottleneck: {'detector' if det_fps/args.target_fps < reid_cps/(args.target_fps*args.people_per_frame) else 'reid'})")


if __name__ == "__main__":
    main()
