# Mumbai lab budget and guardrails

This is a **$100 total credit budget**, not $100 per month. Check the actual remaining balance, account plan, eligible services and credit expiry before provisioning. No resources were created as part of authoring these artifacts.

## Offline calculator

No credentials or network access are needed:

```powershell
python scripts/cost_estimate.py --hours 100 --months 1
python scripts/cost_estimate.py --hours 200 --months 2 --groq-input-million 1 --groq-output-million 1 --groq-audio-hours 5
python scripts/cost_estimate.py --hours 730 --months 1
```

[The calculator](../scripts/cost_estimate.py) uses Decimal arithmetic, validates finite nonnegative quantities, includes idle storage, and separates AWS from Groq billing. `--hours` is total running hours **per instance across the whole project**, not hours per month. `--months` is storage retention duration. `--instances 2` adds a second equal-sized machine for all specified hours; use a separate estimate for a worker used for less time.

## Assumed deployment and checked rates

Public rates checked on 2026-10-13 for Mumbai (`ap-south-1`), Linux on-demand shared tenancy:

| Item | Rate / assumption |
| --- | --- |
| m7i-flex.large EC2, 2 vCPU / 8 GiB | $0.10075 per running hour |
| One public IPv4 | $0.005 per address-hour |
| gp3 storage, baseline performance | $0.0912 per GiB-month; 40 GiB provisioned |
| ECR image storage | $0.10 per GB-month; 5 GB retained |
| S3 Standard | $0.025 per GB-month; 5 GB retained |
| S3 requests | 1,000 PUT and 10,000 GET per month in this example |
| Monitoring/light-usage reserve | $3/month, a planning allowance, **not a service price or spending cap** |

Sources: [EC2 Mumbai price data](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/Asia%20Pacific%20%28Mumbai%29/Linux/index.json), [EBS price data](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ebs.json), [VPC price data](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonVPC/current/ap-south-1/index.json), [ECR price data](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECR/current/ap-south-1/index.json), [S3 price data](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/ap-south-1/index.json).

| Scenario | AWS estimate |
| --- | ---: |
| 100 running hours, storage retained one month | $17.86 |
| 200 running hours, storage retained one month | $28.43 |
| 200 running hours, storage retained two months | $35.71 |
| 730 running hours, storage retained one month | $84.48 |

Allow **roughly $20–40** for a scheduled development/demo project. This is not a guarantee: taxes, backups, high egress, more disks/nodes, excessive logs/custom metrics, a domain, GPU, EKS, NAT gateway, ALB and paid KMS keys are excluded. Free allowances/credits/discounts are not subtracted. Region rates and service eligibility can change.

The estimate assumes Qwen/Kitten are optional CPU extensions and transcription/larger-model routing may use Groq; it does not promise all models or HPA replicas fit on the suggested instance. Benchmark before enabling additional models. The initial scaling experiment uses the lightweight real sentiment model.

## Groq is separate

Published GPT-OSS-120B prices: $0.15/million uncached input tokens, $0.60/million output tokens. Whisper-large-v3-turbo is $0.04/audio-hour. An illustrative **1 million input + 1 million output tokens + 5 hours audio** costs **$0.95**, separately billed. Free quotas, caching discounts, minimum billed audio duration and provider rate limits may affect actual charges. Disable these adapters unless you knowingly opt in; do not use unrestricted provider calls in a load test.

Sources: [Groq text pricing](https://console.groq.com/docs/model/openai/gpt-oss-120b), [Groq Whisper pricing](https://console.groq.com/docs/model/whisper-large-v3-turbo).

## Operating safeguards

- Default architecture has no inbound ports; use SSM port forwarding. Restrict AWS/CI identities and require an API key for the cloud deployment.
- Review a Terraform plan and account-specific eligibility before apply. Neither CI nor helper commands should automatically create or start infrastructure.
- The on-boot auto-stop timer is a fallback, not a budget cap. Stop the instance after every test/demo; verify it reaches `stopped`.
- An ephemeral auto-assigned public IPv4 is released on stop. An Elastic IP retained while stopped would still cost money; this design does not allocate one.
- EBS, ECR, S3 and retained logs can keep consuming credits when compute is stopped. Check snapshots, addresses and storage after teardown.
- Keep backups and reproducible source outside an expiring Free-plan account. Secrets must stay out of Git, deployment bundles, Terraform inputs/state and output logs.
- Budgets notifications are delayed. An alert cannot guarantee prevention of overruns.
- AWS Free plan ends after six months or credit depletion, whichever comes first. A Paid plan can bill beyond credits; both can start with the same $100 balance. [AWS plan rules](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html)
