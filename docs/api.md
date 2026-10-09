# Internal job API

Authenticated asynchronous stage API; not an Ollama-compatible gateway.
Every route requires Authorization: Bearer with the configured internal token.
It is obtained from a named environment variable, never logged or forwarded.

- POST /jobs: exact request_id, target, operation, payload envelope.
- GET /jobs/{id}: status without input prompts.
- GET /jobs/{id}/result: terminal only; check state/error_code.
- GET /jobs/{id}/artifact: intended retrieved image/MP3, never arbitrary files.
- POST /jobs/{id}/cancel: waiting work only; no running-job interruption.
- GET /resources: readiness and durable owner summaries.

States: waiting, running, cleanup_pending, reconciling, success, failed, cancelled.
Acquisition, backend execution/cleanup and client waiting have separate bounds.
No forced recovery endpoint.

Trusted configuration alone supplies targets/resources/models/workflow templates.
A hash-bound workflow permits only declared mutations; types/links/output paths
remain fixed. Path mutations require exact enums; dynamic commands are forbidden.
No client backend URL, filesystem path, device override or shell call is accepted.

Duplicate JSON keys/nonfinite values and oversized requests/responses are rejected.
Artifacts come from the configured node/output namespace and expected PNG/MP3
signature/MIME. Composition, promotion and content quality remain caller logic.
