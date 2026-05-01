# Open Questions for William (Software Engineer)

Running list of questions that need to be answered by William as the TCS /
DAQ subject-matter expert. Items here block or shape parts of the autoguider
design.

Last updated: 2026-04-30

## TCS / wire protocol

1. **Settle time between successive `G` commands.** What is the minimum interval
   we should respect between sending two `G` offsets so that the second one is
   not silently dropped by the `!guiding_ra && !guiding_dec` gate? This also
   sets the max effective rate at which we can split a large correction into
   multiple `G` commands.
2. **`guider_cmd_processing` flag.** What controls this — operator UI on the
   TCS, a separate command from the instrument computer, or some internal
   state? How do we know it's enabled before we start guiding?
3. **TCS status / telemetry channel.** Is there any way for the instrument
   computer to *read* the current TCS state — pointing (HA, Dec), rotator
   angle, slewing flags, whether a previous `G` was accepted? If yes, what's
   the protocol? If no, we have to design assuming open-loop telemetry.
4. **Behavior on dropped commands.** When a `G` is silently dropped (TCS
   slewing or `guider_cmd_processing` false), is there any indication to us at
   all (log, side-channel)?
5. **Timing on the wire.** Any delay we should expect between TCP send and
   the offset being applied?

## DAQ / SUTR frame delivery

6. **File-completeness convention.** We're assuming the DAQ writes
   `foo.fits.tmp` and atomic-renames to `foo.fits` once the SUTR is collapsed
   and written. **Confirm** this is what the DAQ actually does (or tell us the
   real convention).
7. **Output directory.** Where does the DAQ write completed SUTR frames?
   Configurable, or fixed?
8. **Filename convention.** Real-data sample shows
   `henNNNN_sssr.fits` (e.g. `hen1764_001r.fits`) for the per-SUTR raw
   reads, plus a final integrated `henNNNN.fits` (no underscore, no `r`)
   per integration. Need to confirm:
   - What does the trailing `r` mean? Candidates: (a) "raw" read, (b)
     "reference-pixel-corrected" already, (c) just a naming convention.
   - Is `_sssr` the only suffix, or are there processed variants (e.g.
     `_sssp`, `_sssc`) that may also land in the watch directory?
   - The final `henNNNN.fits` (no `_sss`): is that the ramp's slope-fit
     output, an end-of-integration co-add, or something else? Should the
     autoguider react to it (it carries no new SUTR information once the
     `_023r` has arrived) or ignore it?
   - Any sidecar files (`.hdr`, `.log`, `.thumb.png`)?
9. **FITS structure.** Real-data sample is a 2048×2048 single-extension
   image, `BITPIX=16`, `BUNIT='DU/PIXEL'`, `BSCALE=1.0`, `BZERO=0.0`. So
   each `_sssr.fits` IS a 2D image (one read at sample `sss`), as
   expected — not a SUTR cube. Need to confirm:
   - Are the outer 4 rows/columns reference pixels (standard H2RG layout)?
     If so, the autoguider should mask them before any reduction.
   - Pixel datatype is `int16` after BSCALE/BZERO — does that match all
     possible read modes, or do high-flux reads ever overflow into a
     signed-int wrap? (`saturation_dn = 40000` config default suggests
     headroom is fine, but worth confirming.)
   - Single extension vs MEF: confirm always single, or is there ever a
     case (e.g. dark-corrected runs) with extra HDUs?
10. **Pointing in the FITS header.** Are HA, Dec, RA, Dec, rotator angle (PA),
    UTC, exposure time, airmass, **detector temperature**, and **focus
    position** all already written to the FITS header by the DAQ? Exact
    keyword names? (We need HA & Dec to bin the guiding measurements for
    nonperiodic-error analysis, and the others for diagnostics.)
11. **Intermediate non-destructive reads.** Confirmed not available — the
    only signal we get is one completed file per 1–3 min. (No action needed,
    just flagged.)

## Instrument geometry

12. **PA convention.** When the user enters the instrument PA, what's the
    convention? Degrees east of north for the +Y detector axis? Something
    else? We need this exactly to do the detector→sky rotation correctly.
13. **Plate scale.** Arcsec/pixel on the science detector — needed to convert
    pixel deltas to arcsec before the PA rotation.
14. **Detector orientation parity.** Does +X on the detector correspond to +RA
    or -RA at PA=0 (and similarly for Y vs Dec)? Off-by-180 mistakes here are
    classic.

## Bad pixel mask

17. **Source and location.** Where does the bad-pixel mask come from
    (calibration pipeline product, hand-curated, regenerated per night)?
    What path is it written to, and is it expected to be stable across an
    observing run?
18. **Format.** FITS image with the same shape as the science frame? Boolean
    (0=good, 1=bad) or bit-encoded (different categories of badness)? Single
    extension or MEF?
19. **Lifecycle.** Does it ever change mid-night (e.g., new hot pixels found),
    or is it loaded once at startup?

## Detector parameters (needed for Monte Carlo "Estimate K")

20. **Gain.** Default placeholder is 4 e⁻/DN — what's the actual measured value
    for the science detector? Per-quadrant or per-amplifier variation?
21. **Read noise.** What's the RN per single non-destructive read, in
    electrons? Needed to model the noise budget of K-sample averages.
22. **Saturation / linearity.** At what DN level does the detector go
    non-linear? Needed for sanity-checking guide-image pixel values.

## Operations

15. **`guider_cmd_processing` enable/disable in operations.** Who toggles it,
    and when? (Affects how the GUI should behave — do we surface this to the
    observer?)
16. **Guiding while slewing.** Should the guider auto-pause around target
    acquisitions / large slews?

---

(Add new items at the bottom; keep the section grouping.)
