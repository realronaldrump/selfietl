# Face shape trend, version 3

This is a personal visual index, not an estimate of weight, body fat, or an anatomical diagnosis. Its accuracy has been checked against controlled synthetic perturbations and API regression tests, not against a labeled clinical dataset. The uncertainty ranges are approximate, conditional on the measurements; they cannot account for every systematic detector error.

## Measurements and hair

MediaPipe normalizes x and y by different image dimensions. Version 3 restores isotropic coordinates using the oriented `image_size` saved beside the landmarks, falling back to catalog dimensions for legacy archives. Missing or invalid dimensions and nonfinite landmarks are rejected. Eye alignment and interocular scaling then remove translation, in-plane rotation, and uniform scale. This does not remove perspective distortion from camera distance or fully correct head rotation.

The six fullness inputs are cheek width / eye-to-chin height, jaw / cheek width, lower-cheek width, lower-face area, lower-face roundness, and chin / cheek width. Forehead points no longer enter the index. The legacy JSON keys `face_width_height` and `outline_roundness` now refer to those lower-face definitions; the algorithm version distinguishes them from earlier measurements. The displayed outline still includes the forehead. Temple proportions, jaw angle, length, and symmetry remain supplementary features.

No hair segmentation model is used for this index. Forehead landmark displacement alone cannot alter it, but hair or beards obscuring the cheeks or jaw can still distort its inputs. Facial expression, lighting, aging, and camera perspective remain confounders. Pose values are the detector pipeline's geometric proxies, not calibrated 3D angles. The existing conservative pose/expression gates remain, and missing or nonfinite quality/pose values are rejected.

MediaPipe coordinate reference: https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/components/containers/NormalizedLandmark

## Baseline and corrections

A baseline requires six distinct eligible dates. Repeated photographs are consolidated within date and capture profile, then across profiles for one baseline vote per date. The baseline remains frozen after creation. Scales use median absolute deviation with an engineering noise floor of max(0.01, 1% of the feature center magnitude); this prevents floating-point or tiny landmark changes from becoming huge scores. The floor is conservative and not a measured detector error rate.

Pose/expression correction is learned only from within-day, same-profile contrasts, with at least eight paired dates. Symmetric contrasts and regularized robust regression avoid confusing slow face changes with changing camera habits. Otherwise, correction is disabled and the eligibility gates remain in force. Correction inputs are clipped to the observed training range rather than extrapolated. Duplicate observations do not create additional correction evidence.

Camera offsets require at least three exact matched dates. Mere overlap of two date ranges is insufficient. Capture profile switches still break chart segments, and direct comparisons across different profiles do not assert a shape-change conclusion. Camera identity is limited by available metadata; an unknown lens or camera distance cannot be inferred from make/model and dimensions alone.

Calibration uses daily summaries from at least five distinct dates per anchor, one matching capture profile, and similar pose/expression. Features that contradict the chosen direction receive no calibration weight. Old calibration coefficients cannot be carried across different measurement definitions.

## Trends and evidence

Each date contributes one reading from the best-quality capture profile available that day; incompatible cameras are not blended. Robust local linear smoothing favors nearby, higher-quality dates. A nonzero residual scale floor keeps isolated errors from defeating the robust fit when most points lie on a perfect line.

Local intervals use Student-t critical values, weighted intercept leverage (including wider chart-edge intervals), and conservative lag-one residual dependence inflation. Period medians and Theil-Sen slope intervals use deterministic moving-block resampling. Kendall evidence includes ties, continuity correction, and a residual-dependence variance adjustment. Long-archive Theil-Sen calculations use a deterministic bounded sample of 20,000 pairs; smaller archives use every pair.

Sustained-shift detection compares its best split against block permutations of the full searched set (at most 128 candidate dates), rather than pretending the selected date was specified beforehand. These remain exploratory findings. Dependence adjustments and the noise floor reduce false precision; they do not guarantee nominal 95% coverage on real photographs.

A 90-day comparison requires a point within 21 days of the target date in the same continuous segment. Direct period comparisons suppress conclusions when capture profiles or pose/expression differ, or when either period has fewer than three distinct dates. Confidence cannot be high solely because many low-quality observations agree.

## Migration and caches

`face-shape-v3` invalidates version-2 measurements and profiles. The next analysis job remeasures cached landmarks and builds a new baseline; it does not need to redetect or modify source photos. Old calibration must be selected again. The usual app analysis/recompute flow triggers the migration after the updated backend is deployed.

Cached reports include all calculation-relevant metadata and landmark file signatures in their revision. A stale report keeps the revision it actually analyzed and exposes the new `source_revision` separately. Comparisons and calibration reject changed inputs until recomputation completes. Existing trend-cache database migrations are preserved and tested.

## Regression coverage

`tests/test_face_shape_accuracy.py` covers aspect ratio and image-size provenance, forehead invariance, invalid inputs, pose/time confounding, supported within-day correction, duplicate baseline weighting, matched camera offsets, unsupported comparisons, insufficient date coverage, noise floors, stale files/metadata, version migration, endpoint leverage, dependence, isolated outliers, and retention of a sustained known trend. API, cache, comparison, and export behavior are also tested in `tests/test_face_shape.py`.
