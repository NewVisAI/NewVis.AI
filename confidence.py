"""
Shared confidence-class helper for Beta safety features.

Fall, running, and altercation detectors each derive a numeric score from their
own signals (bbox drop ratio, sustained speed, oscillation count, etc.). To keep
the UI consistent, they all bucket that score into the SAME three labels:

    "low"    — signal fired but weak / one supporting evidence only
    "medium" — signal fired with the primary heuristic met cleanly
    "high"   — primary heuristic + at least one independent confirmation
               (pose torso ratio, post-fall stillness, motion oscillation)

Independent of thresholds: each caller passes its own (medium_threshold,
high_threshold) so the helper stays generic.
"""

from typing import Tuple


def classify(score: float, thresholds: Tuple[float, float]) -> str:
    """Bucket a 0..1-ish score into "low" / "medium" / "high".

    ``thresholds`` is (medium_threshold, high_threshold) — a score >= high goes to
    "high", >= medium goes to "medium", below both stays "low". The caller owns
    the numbers so this stays framework-agnostic.
    """
    medium, high = thresholds
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"
