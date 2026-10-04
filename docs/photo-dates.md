# Photo dates

`photos.captured_at` is the photo's local calendar date and clock time, stored
without a timezone. Calendar grouping, duplicate detection, daily replacement,
analysis, and video overlays all use that source date.

Single imports, batch imports, and date edits share
`selfietl.capture_dates.parse_capture_datetime`. An ISO timestamp such as
`2026-10-03T23:59:00-06:00` is stored as `2026-10-03 23:59:00`, regardless of the
server timezone. Offsets (including `Z`) are validated but do not move the clock.
Clients should send the intended local capture time rather than converting it
to UTC first. The native app already does this for Photos-library dates.

The Photos-library timestamp sent by the native app takes precedence over
embedded metadata, including when a date was adjusted in Photos. Without a
client timestamp, EXIF DateTimeOriginal supplies the photo date. When it is
absent, the metadata reader recognizes date-stamped inbox, native upload, and
legacy import filenames. File modification times and EXIF DateTime (the image
modification date) never substitute for an original photo timestamp.

Missing dates stay missing in previews. Browser imports require the original
date to be entered before upload; single and batch APIs reject undated imports
before starting processing, and folder scans report and skip undated files.
Inbox writes also require a known capture time. A retry of a cataloged photo
uses the saved catalog date so a date edit is retained. Date edits invalidate
cached images containing the old date label; analysis and video fingerprints
also include the capture time. Render range validation and filtering compare
these same local capture times.

Older imports discarded their source offsets. Their dates cannot be reliably
recovered by subtracting a fixed number of hours: source timezones, daylight
saving, and deliberate date edits can differ. Correct an existing photo through
`PATCH /api/photos/{hash}` when its intended date is known.
