# Hair analysis

`hair-v2` measures visible hair in a selfie and flags sustained contractions that might be a haircut. A suggestion is an observation to review, not a prediction of when someone will need a haircut. Only a confirmed date changes the last-haircut counter.

## After a selfie

The capture pipeline detects the face, aligns it, measures hair, and immediately checks recent haircut boundaries. The incremental check includes 42 days before the uploaded photo plus 21 days of earlier baseline context. Backdated uploads revisit subsequent observations. Full rechecks and nightly alignment refreshes check the complete history.

Capture completion invalidates the web hair query. The Hair page also refreshes while open, shows the latest successfully analyzed selfie date separately from the confirmed haircut date, and reports pending or failed analysis. Hair errors are recorded as unusable measurements without rejecting the selfie or leaving its earlier measurement eligible. Recheck photos retries them.

## Segmentation and measurements

The pinned [MediaPipe HairSegmenter model](https://ai.google.dev/edge/mediapipe/solutions/vision/image_segmenter) has background and hair classes, at indexes 0 and 1. It runs locally. Source confidence masks are cached separately from refinement; valid v1 caches can be reused when upgrading without running inference again. Source and landmark changes invalidate measurements. Writes use temporary files and atomic replacement.

Cleanup runs at a maximum side of 1024 pixels with source landmarks scaled to the same rectangular image coordinates. Eight-connected components need a high-confidence core near the scalp or temples. The lower threshold retains connected wisps and long hair, while disconnected background and beard components are rejected. Only small pinholes are filled. Nonfinite values contribute no hair.

Unclear masks, cropping at the source or alignment boundary, poor photo quality, unsuitable head angles, invalid landmarks, and distorted affine alignment exclude a photo from calculations. The page retains the original photo and the reason. Masks are aligned against the current canonical face; a changed or missing anchor is repaired before measurement.

Area is divided by squared eye distance. Extents use the eye-axis coordinate system and 1st/99th percentiles, reducing sensitivity to roll, scale, and isolated stray pixels. Crown area, side area, perimeter, retained area, and alignment anisotropy are also recorded. These are image measurements, not physical hair length.

One eligible, included photo per calendar day is chosen consistently for the manifest, detection, and export. Selection prioritizes eligibility, inclusion, model confidence, and then capture time. Multiple same-day photos cannot provide independent evidence.

## Haircut detection

Comparison masks use a fixed eye-centered, scale- and roll-normalized coordinate system. Comparisons require compatible camera metadata and head pose, and a gap of no more than 21 days.

A candidate needs at least two preceding comparable days, sufficient loss of visible area and extent, and substantially more disappearing hair than newly appearing hair. Shape change must exceed a robust threshold based on recent median change and median absolute deviation. Supported changes account for the number of distinct days in the before/after windows; provisional changes retain the single-observation threshold. Two later, distinct days within 21 days must retain the shorter shape before the candidate becomes a suggestion. A recent uncontradicted change with less follow-up remains provisional. Reversals, equal-area styling changes, camera switches, and long gaps do not satisfy these rules.

Nearby boundaries are consolidated. Confirmed and dismissed decisions are preserved. Unsupported automatic suggestions are removed, including when no eligible measurements remain. Stored evidence includes the before/after photos, observation interval, number of supporting days, and contraction measurements. The score measures deviation from recent variation; it is not a calibrated probability. The observed interval is shown because the first short-haired selfie does not establish the exact haircut date.

`haircuts-v3` also checks regional contractions: a reduction of at least 20% in crown or side area, with a smaller overall outline and two later dates retaining at least a 10% reduction in that same region. This catches side trims whose crown styling makes whole-mask overlap noisy. It still requires disappearing hair to exceed newly appearing hair and compatible photos. Consolidation keeps the earliest supported boundary, rather than moving a cut to a later photo with a larger drop. Confirmed records can gain matching before/after evidence without changing their dates or source.

## Display and export

The elapsed-time counter uses calendar dates, including leap days and daylight-saving transitions. It counts from the latest confirmed, nonfuture haircut. Haircuts can be added before any segmentation is available. Future dates are rejected, and repeated additions of an already confirmed date are idempotent.

Outline change compares median areas from up to three comparable days within 21 days after the confirmed haircut against up to three recent days within 14 days of the latest eligible selfie. Each window needs at least two days and must not overlap. Unsupported comparisons stay unavailable.

Video ranges and exports use the same dates relative to the latest archived selfie. Exports include one selected photo per day, validate ranges and even dimensions, and preserve project-scoped atomic video replacement. Segmentation/source/alignment changes, included photos, and confirmed haircut markers invalidate the video fingerprint. Automatic suggestions and measurement timestamps do not invalidate a completed video. Fresh measurements are reused during rechecks.

Opening the page or changing its range never starts a video render. Videos are created with the explicit button or by the nightly scheduler. Identical completed requests reuse the existing file, and simultaneous identical requests share one job.

## Limits and validation

Wet hair, products, hats, lighting, occlusion, and sustained styling changes can alter the silhouette without a haircut. Face-relative filtering and repeated observations reduce these errors but cannot eliminate them. Missing or inconsistent photos can hide a real haircut. The detector never confirms a haircut automatically.

Regression tests cover segmentation noise, clipping, nonfinite inputs, scale/roll invariance, styling reversals, camera switches, gaps, same-day bursts, cache migration, stale alignment, failed analysis, confirmed-date preservation, comparable growth windows, export validation, and the complete saved-selfie-to-updated-suggestion path. UI tests cover confirmation, calendar arithmetic, source/mask inspection, errors, empty history, and consistent date ranges. There is no labeled pixel-mask or haircut benchmark for this personal archive, so these safeguards do not establish a measured accuracy percentage.
