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
8. **Filename convention.** What does the filename look like (UTC stamp,
   sequence number, target, …)? Any sidecar files (e.g. `.hdr`, `.log`)?
9. **FITS structure.** Is the file already a 2D collapsed image (slope fit
   from the SUTR ramp), or a SUTR cube we have to reduce ourselves? Single
   extension or MEF? Pixel datatype?
10. **Pointing in the FITS header.** Are HA, Dec, RA, Dec, rotator angle (PA),
    UTC, exposure time, and any TCS state already written to the FITS header
    by the DAQ? (We need HA & Dec to bin the guiding measurements for
    nonperiodic-error analysis.)
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

## Operations

15. **`guider_cmd_processing` enable/disable in operations.** Who toggles it,
    and when? (Affects how the GUI should behave — do we surface this to the
    observer?)
16. **Guiding while slewing.** Should the guider auto-pause around target
    acquisitions / large slews?

---

(Add new items at the bottom; keep the section grouping.)
