# Read-only recovery summary (review candidate)

`GET /recovery-summary` uses the existing Bearer-token authentication. It reads
the current atomic ownership journal and recovery status of currently owned jobs.
It creates no locks or files, writes no records, and never calls recovery,
inference, backend readiness or GPU operations. Other HTTP methods are rejected.

The complete response schema is:

```json
{
  "schema_version": 1,
  "incidents_7d": 3,
  "operator_required": false,
  "snapshot_at": "2026-10-24T16:00:00+00:00"
}
```

No job/request/lease IDs, GPU/container identities, evidence, prompts, credentials
or configuration are returned. Missing/unreadable durable state uses the existing
generic API failure response rather than manufacturing a zero count.

Counting uses an inclusive rolling `[snapshot - 7 days, snapshot]` window, in UTC.
The first trustworthy recorded observation of a distinct incident determines its
date. Dates must be explicit timezone-aware ISO timestamps, not mutable status
timestamps; malformed, naive, missing and future dates are ignored. Retries and
late audit/release writes cannot move an old incident back into the window.

- A reboot requires two distinct valid boot UUIDs in the stored startup identity
  or original-owner/observed-boot identity. All startup, per-lease and release
  records for the same resulting boot are one event, regardless of GPU count.
- Wallpaper incidents use stable job/lease identity, falling back to a durable
  incident ID where necessary. Matching automatic/manual audit representations
  count once. A legacy dated wallpaper release with stable identity is supported;
  unidentified old evidence and unverifiable reboot labels are not guessed.
- A later reboot audit may resolve a provisional wallpaper incident. The older
  provisional wallpaper date cannot backdate the reboot. A separate wallpaper
  incident and a separate reboot count independently.
- Healthy jobs, ordinary cleanup/unloads and policy authorization create no event.

Attention is independent of the seven-day window and timestamp validity. Current
boot/worker/job operator-required states and unresolved automatic/startup incidents
trigger it. Successful matching release audits resolve old automatic records;
unowned historical job errors and older boot failures superseded by a newer
verified boot do not remain current warnings. No recovery algorithm, journal
schema, admission policy, placement or scheduling behavior is changed.
