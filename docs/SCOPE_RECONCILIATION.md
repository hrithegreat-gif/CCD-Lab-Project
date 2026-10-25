# Scope reconciliation: sentiment specification to detection MVP

The original build specification describes a text-sentiment service with
`POST /api/inference` and positive/negative text examples. The implemented
project intentionally migrated to CPU object detection; those examples and
that request contract are historical and are not implemented.

The current contract is defined by [API_CONTRACT.md](API_CONTRACT.md), the
application behavior in `app/`, and the current acceptance commands in
[RUNBOOK.md](RUNBOOK.md).

| Original requirement or example | Current implementation | Status |
| --- | --- | --- |
| Text sentiment model and text payload | Pinned YOLOv8n ONNX CPU model; raw JPEG, PNG, or WebP bytes to `POST /api/detect` | Replaced, not dual-served |
| `POST /api/inference` | `POST /api/detect`, with original-coordinate boxes and confidence scores | Contract changed |
| Positive/negative text response | Ordered COCO object detections, model metadata, timing, and image dimensions | Contract changed |
| Input validation | Content type and signature checks, byte and pixel bounds, animation rejection, query validation, and sanitized errors | Implemented for images |
| Authentication and bounded serving | Optional API-key protection for `/api/*` and `/metrics`; bounded body size, inference capacity, and timeout | Implemented |
| Health and model metadata | Liveness/readiness endpoints and an allowlisted single-model registry | Implemented |
| Container delivery | Pinned model is baked into a hardened non-root image | Implemented; local acceptance recorded |
| Kubernetes scaling | One-worker Pods with CPU HPA, Metrics Server and bounded k6 experiments | Local scale-out evidence recorded; not HA |
| CI | Python/model, offline manifest, Docker, Terraform, frontend, and aggregate-gate jobs | Workflow implemented; hosted acceptance remains unverified |
| Optional AWS and emulator integrations | Terraform and operator workflow exist; cloud deployment and optional emulator/queue features are not part of the locally verified MVP | Deferred |

The build specification is retained as source-history context and should not
be read as the current API contract. Do not restore sentiment inference or add
a second model solely to satisfy its obsolete examples. The project is
currently scoped to the local detection MVP; AWS acceptance remains an
owner-authorized optional delivery step.

The report and demonstration must describe object detection consistently.
Latency and scaling numbers are functional/load observations, not detection
accuracy claims. See [the measured analysis](../results/ANALYSIS.md) for the
sample size, rejection counts, and sampling limitations.
