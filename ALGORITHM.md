# 2D image cross-correlation — the recommended SUTR-guide algorithm

The best per-pair offset measurement (X and Y, jointly) is a 2D image
cross-correlation of the trace stamp against a high-S/N template.
This document explains it in plain language and shows the few lines
of math you need.

## Inputs

- **Template** `T(x, y)` — a 2D stamp of the trace at a known reference
  position. Build it once from the cumulative ramp of the *first*
  frame in the sequence: `read[N] − read[1]`. Sky-subtract it.
- **Data** `D(x, y)` — the same stamp on the SUTR pair you want to
  centroid: `pair = read[k+1] − read[k]`. Same X window, same Y range.

A good stamp covers the full bright trace:
- Y: the whole illuminated stripe (filter cutoff to filter cutoff,
  e.g. Y = 600..1980 in R-J).
- X: a snug window around the trace (±25 px is plenty for a 6-px drift).

## What the algorithm does

In one sentence: **slide the template around inside the data and
find the shift `(Δx, Δy)` where they line up best.**

In numbers — for every candidate shift `(δx, δy)` in a small search
window (e.g. ±12 px), compute the cross-correlation:

```
C(δx, δy) = Σ_y Σ_x  T(x, y) · D(x + δx, y + δy)
```

That sum is large when the absorption features in `D` sit on top of
the absorption features in `T`. The peak of `C(δx, δy)` is the
best-fit shift.

## Why this works for both X and Y at once

Cross-dispersion (X) precision comes from the **continuum profile** —
all the photons summed across the trace make a sharp ridgeline, which
locates X to ~0.1 px on a single SUTR pair.

Along-dispersion (Y) precision comes from **every gradient in the
spectral direction**:
- the sharp blue/red filter cutoffs (very steep, dominate the Y peak),
- the telluric absorption band edges,
- the Paschen stellar absorptions,
- even the smooth continuum slope.

The Cramér-Rao bound says the uncertainty on a shift scales like
1 / √(Σ (∂T/∂axis)²). The 2D xcor uses *every* gradient pixel in
*both* axes, weighted by its own gradient² — so it's near-optimal.

## Sub-pixel refinement

The integer-shift maximum of `C` only locates the peak to ±1 px. To
get sub-pixel precision, fit a parabola to the three correlation
values around the peak in each axis independently:

```
For X: at the peak row,
   a, b, c = C(δx-1, peak_y), C(δx, peak_y), C(δx+1, peak_y)
   sub_x = 0.5 · (a − c) / (a − 2b + c)
   Δx = δx_peak + sub_x

For Y: same thing along the peak column.
```

The curvature `(a − 2b + c)` also gives a formal uncertainty — bigger
curvature means a sharper peak means a better-pinned shift.

## Sky subtraction (don't skip)

Before correlating, subtract a row-by-row local sky from each stamp:
take the median of the outer 1/6 of pixels on each side of the row,
subtract it. This removes:
- Detector pedestal differences between reads.
- Sky background gradients along the trace.
- Slow per-frame H2RG bias drift.

Without this, the correlation peak is biased toward whatever has the
biggest mean offset, not the structure that contains the position
information.

## Why this beats fitting individual features

We tried two simpler approaches and they were worse:

1. **Single-Gaussian fit on a single absorption** (e.g. the telluric
   band at Y≈1794): σ_pair ≈ 0.5 px in Y. Bad — it only uses photons
   inside one ~10-px-wide feature, and the fit is biased by continuum
   slope.
2. **1D cross-correlation on a single band** (Y = 1700..1860, just the
   telluric region): σ_pair ≈ 0.15 px in Y. Better — uses every
   sub-feature in the band — but ran into a per-frame ~0.5–1 px
   "staircase" artifact from H2RG state changes / continuum-norm wobble.

The 2D xcor on the *whole* trace stamp delivers σ_pair ≈ 0.05 px in Y
on a bright source and **kills the staircase** — the per-frame
biases that messed up the band-only xcor are diluted into noise when
you also use the filter cutoffs and the continuum profile.

## Pseudocode (~15 lines)

```python
def measure_offset(pair, template, good_mask, x_center, halfw=25,
                   y_lo=600, y_hi=1980, search=12):
    # 1. extract stamp + sky-subtract
    sub = pair[y_lo:y_hi, x_center-halfw : x_center+halfw+1]
    edge = sub.shape[1] // 6
    sky = np.median(np.r_['1', sub[:, :edge], sub[:, -edge:]],
                    axis=1, keepdims=True)
    D = np.where(good_mask_stamp, sub - sky, 0.0)

    # 2. brute-force correlation within ±search pixels
    C = np.zeros((2*search+1, 2*search+1))
    for iy, dy in enumerate(range(-search, search+1)):
        for ix, dx in enumerate(range(-search, search+1)):
            C[iy, ix] = (D[max(0,dy):D.shape[0]+min(0,dy),
                           max(0,dx):D.shape[1]+min(0,dx)] *
                         template[max(0,-dy):template.shape[0]+min(0,-dy),
                                  max(0,-dx):template.shape[1]+min(0,-dx)]
                        ).sum()

    # 3. peak + parabolic sub-pixel refinement
    iy, ix = np.unravel_index(C.argmax(), C.shape)
    a, b, c = C[iy, ix-1], C[iy, ix], C[iy, ix+1]
    sub_x = 0.5*(a-c) / (a - 2*b + c)
    a, b, c = C[iy-1, ix], C[iy, ix], C[iy+1, ix]
    sub_y = 0.5*(a-c) / (a - 2*b + c)
    return (ix - search) + sub_x, (iy - search) + sub_y
```

That's the entire algorithm. Run it on every SUTR pair against a fixed
template, and you have a per-pair (Δx, Δy) time series at ~0.1 px / 0.05 px
precision — usable as a direct guide signal.

## When to use a *sliding* template

For sequences longer than a few minutes, replace the fixed
frame-1 template with the cumulative ramp of the *previous* frame
(or a running average of the last few). This removes systematics from
slow shape evolution (water vapor, airmass, thermal) without
sacrificing precision — adjacent frames are nearly identical apart
from the small drift you're trying to measure.

For short sequences (≤ 5 min) the fixed template is fine.

## Implementation in this directory

See `measure_2d_xcor.py` — the full working version, with multi-star
support, time-tagging from headers, and a comparison plot against the
1-D band xcor to show the staircase getting killed.
