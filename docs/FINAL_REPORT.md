# Delivery status report

Date: 2026-10-23

## Implemented and locally verified

The service is migrated to YOLOv8n object detection with pinned ONNX Runtime
CPU weights, raw JPEG/PNG/WebP requests, image validation, bounded inference,
the detection Studio, hardened container configuration, and Kubernetes/HPA and
k6 tooling. The model export contract and digest are recorded in
[model provenance](../models/yolov8n-export.json).

Three local Minikube experiments reached three ready replicas and returned to
one. Mean sustained successful goodput was 10.726 requests/second; mean
successful-response p95 was 483.0 ms. The runs also produced expected 429
capacity rejections under eight-VU saturation, reported separately from
successful traffic. Per-run details and sampling limitations are in
[the measured analysis](../results/ANALYSIS.md).

Checks recorded for this delivery: full Python suite, 276 passed; Studio Node
contract suite, 60 passed; focused manifest/Terraform-safety tests, 69 passed;
deployment-bundle safety script, 17 passed; and experiment analysis, nine rows
generated. Terraform CLI was unavailable for fmt/validate/test. A hosted CI
result was not checked.

## Not completed or claimed

No AWS infrastructure was applied; no AWS deployment, cloud smoke test, AWS
HPA run, CloudWatch delivery, or teardown was performed. Local Minikube results
are not AWS results and do not prove high availability. Team/contribution names
were not supplied and are not invented here.

See the [evidence ledger](EVIDENCE.md) for individual check status. Review the
[AGPL-3.0 notice](../models/NOTICE.md) with the project owner before shipping;
this report is not legal advice.