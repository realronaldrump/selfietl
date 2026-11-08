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
embedded metadata, including when a date was adjusted in Photos. The metadata
reader checks EXIF DateTimeOriginal, XMP capture dates, date-stamped filenames,
and EXIF DateTimeDigitized in that order. EXIF DateTime records image changes.

In the web app, a filename such as `capture_1759557552000.jpeg` can supply a
date in the device's timezone when the file lacks a date in its metadata. For
library imports, the filename timestamp must predate opening the picker so a
temporary export name cannot assign the selection time. A fresh Camera capture
can also use the camera file's timestamp from within that picker session.
Review shows the source of each detected date and allows edits before upload.

When the web picker delivers a copy without its date, select the photos in
iPhone Photos, use **Share → Export Unmodified Originals**, and choose the saved
images from Files in SelfieTL. If the date exists only in the Photos library,
use the native app with Photos access or enter it in web review. The web app
can keep the dated photos from a mixed selection. Library imports require a
capture time recovered from the file or supplied in review. Single and batch
APIs reject undated imports before processing, and folder scans report and
skip undated files.
Inbox writes also require a known capture time. A retry of a cataloged photo
uses the saved catalog date so a date edit is retained. Date edits invalidate
cached images containing the old date label; analysis and video fingerprints
also include the capture time. Render range validation and filtering compare
these same local capture times.

Older imports discarded their source offsets. Their dates cannot be reliably
recovered by subtracting a fixed number of hours: source timezones, daylight
saving, and deliberate date edits can differ. Correct an existing photo through
`PATCH /api/photos/{hash}` when its intended date is known.
