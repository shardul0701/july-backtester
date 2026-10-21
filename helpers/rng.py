"""helpers/rng.py

Deterministic per-task seed derivation for CONFIG["random_seed"] (#439).

``random_seed=None`` (default) means "unseeded": callers keep the legacy
global-RNG behaviour exactly. When set, each random consumer derives its own
seed from the base seed plus a stable label (portfolio / strategy / symbol),
so results are reproducible AND independent of which pool worker runs a task
(forked workers otherwise inherit identical global RNG state).
"""

from __future__ import annotations

import zlib


def derive_seed(base_seed, *parts) -> int:
    """Return a stable 32-bit seed from *base_seed* and label *parts*.

    Uses crc32 (not ``hash()``, which is salted per process).
    """
    key = "|".join([str(base_seed), *[str(p) for p in parts]])
    return zlib.crc32(key.encode("utf-8"))
