# CI and delivery

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main`, `phase4/**`, and
`feature/object-detection`, pull requests, and manual dispatch. Its jobs cover
real detection tests, offline manifest/script checks, Docker build and hardened
smoke, Terraform formatting/validation/mock-policy tests, and Studio contract
tests. `ci-success` fails unless all required jobs succeed.

The real test job downloads and verifies the pinned model release asset. Docker
builds also bake and verify the model. These checks need network access to the
public asset; they do not deploy AWS resources.

## AWS delivery

`.github/workflows/deploy-aws.yml` is manually dispatched and uses the protected
`aws-demo` environment and GitHub OIDC. It checks CI for the exact commit,
builds and smoke-tests the container, pushes an immutable commit-SHA image to
ECR, uploads a checksummed deployment bundle to S3, and invokes the scoped SSM
document. Terraform apply and starting a stopped EC2 instance are operator
actions, not workflow actions.

No hosted CI result or successful cloud delivery is asserted here; see
[the evidence ledger](EVIDENCE.md).