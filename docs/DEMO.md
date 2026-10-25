# Detection platform demo (6–7 minutes)

Use a local ready service if available. Do not promise a live Kubernetes
scale-in within the presentation: the recorded return-to-one interval is about
seven minutes. Show the existing plots and CSV for scaling behavior instead.

| Time | Demonstration |
| --- | --- |
| 0:00–0:45 | State the scope: image detection replaced the obsolete sentiment API; one CPU model, one Service, Pods, HPA and Metrics Server. Point to the [architecture diagram](ARCHITECTURE.md). |
| 0:45–1:30 | Open the Studio at `http://127.0.0.1:8000/`. Show readiness, the image upload and the cat/coffee sample buttons. Explain the API key is tab-session-only. |
| 1:30–2:30 | Run the cat sample, show confidence, original-image pixel box, accessible table and annotated PNG download. Optionally show the persisted browser screenshot in `results/evidence/detection-20261023/`. |
| 2:30–3:15 | Show `POST /api/detect` in the [API contract](API_CONTRACT.md): raw bytes, content/signature validation, pixel/byte bounds, and response timing. State the smoke test also checks authorization and rejection paths. |
| 3:15–4:15 | If Minikube is running, show `kubectl --context ai-serving-local -n ai-serving get deploy,pods,svc,hpa` and `kubectl --context ai-serving-local top nodes`. Otherwise use the recorded replica timeline and state it is historical local evidence. |
| 4:15–5:45 | Show [results/ANALYSIS.md](../results/ANALYSIS.md), the measured rows, and the plot. Explain 10.726 req/s mean successful sustained goodput, 483 ms mean p95, 128,547 separate 429 rejections, single-node limits, and incomplete historical Pod metrics. |
| 5:45–6:30 | Close with local verification, manual browser gate, no hosted CI result, and no AWS deployment claim. Identify any team contribution confirmations or course-specific slides still needed. |

## Presenter preparation

1. Follow the [fresh Windows runbook](RUNBOOK.md), if a live service is desired.
2. Confirm the Studio, model readiness, key, and smoke test before presenting.
3. Keep the presentation to functional evidence and recorded measurements; do
   not present sample-photo detections as accuracy evaluation.
4. Use the historical scaling plot if the cluster is unavailable. Do not start
   a new experiment unless there is ample time for its bounded load and
   recovery period.
5. Use [the presentation deck](CCD_Detection_Serving_Project.pptx) and
   confirm the team contribution split before submission.
