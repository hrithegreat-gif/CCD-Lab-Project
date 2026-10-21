# Optional extensions and non-goals

The application adapters are documented in [the model extension guide](../docs/MODEL_EXTENSIONS.md). They have separate generation/speech/transcription contracts and are disabled by default. Their unit tests do not substitute for real weight/provider execution or a resource benchmark.

Private S3/ECR, SSM and CloudWatch support the authorized Mumbai deployment described in [the AWS runbook](../docs/AWS_RUNBOOK.md); they are host/deployment services rather than a pretend in-application database/queue. [The architecture](../docs/ARCHITECTURE.md) explains those boundaries.

Floci emulation, SQS/job state, DynamoDB, separate independent model services and full Prometheus/Grafana deployments are not implemented prerequisites. Add them only after the real container/HPA/measurement gates in [PHASES.md](../docs/PHASES.md) pass. No EKS is used. Any billable resources or provider calls require separate authorization; local emulation/static checks cannot prove AWS deployment.
