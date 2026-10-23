# Budget and cost controls

AWS deployment is optional for local development and creates billable resources.
Terraform defaults target one `m7i-flex.large` instance in `ap-south-1`, a
40-GiB encrypted gp3 root volume, private ECR/S3, and seven-day CloudWatch log
retention. The default AWS Budget is an alert, not a spending cap; notifications
can be delayed. Stopping EC2 does not stop storage charges for EBS, ECR, S3, or
CloudWatch Logs.

Generate the repository's illustrative estimate with:

```sh
python scripts/cost_estimate.py --hours 200 --months 2
```

Review the assumptions in the script and current regional pricing before
applying Terraform. Account credits, instance eligibility, Mumbai capacity,
taxes, retained data, and price changes can alter actual cost. Do not treat an
estimate or budget alert as an authorization to spend.

Local tests and inference require no AWS resources. For deployment approval,
teardown, and remaining-cost considerations, see the [AWS runbook](AWS_RUNBOOK.md).