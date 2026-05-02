"""Rolling buffer of SUTR reads + K-window difference.

For frame number N, the autoguider receives reads N_001, N_002, ...
For each new read, this module either:
  - clears the buffer (new frame_number = detector reset);
  - or appends to the buffer (within the same frame);
and emits a guide image once the buffer holds 2*K reads, advancing by
`stride` reads between emissions.

guide_image = mean(reads[K+1..2K]) - mean(reads[1..K])

where indexing here is "newest at the right". K=1 / stride=1 is the
ALGORITHM.md default: image = read[i] - read[i-1] every read.
"""

from __future__ import annotations

import collections

import numpy as np


class FrameBuffer:
    def __init__(self, K: int = 1, stride: int = 1) -> None:
        if K < 1:
            raise ValueError(f"K must be >= 1, got {K}")
        if stride < 1:
            raise ValueError(f"stride must be >= 1, got {stride}")
        self.K = K
        self.stride = stride
        self._buf: collections.deque[np.ndarray] = collections.deque(maxlen=2 * K)
        self._current_frame: int | None = None
        self._reads_since_emit: int = 0

    def add(
        self,
        frame_number: int,
        sutr_number: int,
        read: np.ndarray,
    ) -> np.ndarray | None:
        """Add one SUTR read; return a guide image if one is emitted, else None.

        Stride semantics: once the buffer holds 2*K reads, an emit is
        produced every `stride` reads (not every `stride` newest reads).
        With K=1 / stride=1 - the default - every SUTR after the first
        emits a difference. With K=2 / stride=2, emits happen on reads
        4, 6, 8, ... (4 = first warm-up, then every-other).
        """
        if frame_number != self._current_frame:
            # New integration -> reset.
            self._buf.clear()
            self._current_frame = frame_number
            self._reads_since_emit = 0

        self._buf.append(read)
        self._reads_since_emit += 1

        if len(self._buf) < 2 * self.K:
            return None
        if self._reads_since_emit < self.stride:
            return None

        self._reads_since_emit = 0
        # Buffer is full (2K reads, oldest first).
        older = np.mean(np.stack(list(self._buf)[: self.K]), axis=0)
        newer = np.mean(np.stack(list(self._buf)[self.K :]), axis=0)
        return newer - older
