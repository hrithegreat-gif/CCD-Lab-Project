"""Offline guardrails, not Terraform plan/IAM simulation/cloud execution evidence."""
from pathlib import Path
import re

import pytest


INFRA = Path(__file__).resolve().parents[1] / "infra" / "terraform"


def text(name: str) -> str:
    return (INFRA / name).read_text(encoding="utf-8-sig")


def test_iam_uses_real_resource_field_not_resources_typo():
    iam = text("iam.tf")
    assert not re.search(r"^\s*Resources\s*=", iam, re.MULTILINE)
    assert re.search(r"^\s*Resource\s*=", iam, re.MULTILINE)


def test_managed_ssm_policy_does_not_bypass_operator_parameter_scope():
    iam = text("iam.tf")
    if "AmazonSSMManagedInstanceCore" in iam:
        # The managed policy allows GetParameter(s) on '*'. A narrower Allow
        # alone does not restrict it; the inline policy must explicitly deny
        # access outside the operator prefix (or not attach that policy).
        assert re.search(r'Effect\s*=\s*"Deny"', iam)
        assert re.search(r"NotResource\s*=", iam)
        assert '"ssm:GetParameter"' in iam and '"ssm:GetParameters"' in iam
        assert "parameter${var.ssm_parameter_prefix}/api-key" in iam


def test_kms_decrypt_targets_keys_and_is_constrained_to_ssm_parameters():
    iam = text("iam.tf")
    assert "kms:Decrypt" in iam
    assert ':alias/aws/ssm"' not in iam  # Alias ARN cannot authorize Decrypt.
    assert ':key/*"' in iam
    assert "kms:ViaService" in iam
    assert "kms:EncryptionContext:PARAMETER_ARN" in iam


def test_ec2_bootstrap_is_not_double_base64_encoded():
    compute = text("compute.tf")
    assert not re.search(r"\buser_data\s*=\s*base64encode", compute)
    assert re.search(r"\buser_data\s*=\s*templatefile", compute)
    assert re.search(r"\buser_data_replace_on_change\s*=\s*true", compute)


def test_instance_waits_for_its_scoped_bootstrap_permissions():
    compute = text("compute.tf")
    assert re.search(r"depends_on\s*=\s*\[[^\]]*aws_iam_role_policy\.node_scoped", compute)


def test_imds_requires_v2_and_does_not_pass_credentials_into_standard_pods():
    compute = text("compute.tf")
    assert re.search(r'http_tokens\s*=\s*"required"', compute)
    assert re.search(r"http_put_response_hop_limit\s*=\s*1\b", compute)
    assert re.search(r'instance_initiated_shutdown_behavior\s*=\s*"stop"', compute)
    assert re.search(r"encrypted\s*=\s*true", compute)


def test_network_has_no_inbound_rules_or_expensive_managed_components():
    network = text("network.tf")
    assert not re.search(r"^\s*ingress\s*\{", network, re.MULTILINE)
    assert '"aws_vpc_security_group_ingress_rule"' not in network
    assert not re.search(r'type\s*=\s*"ingress"', network)
    for file in INFRA.glob("*.tf"):
        source = file.read_text(encoding="utf-8-sig")
        for forbidden in ("aws_nat_gateway", "aws_eks_cluster", "aws_lb", "aws_db_instance"):
            assert not re.search(rf'resource\s+"{forbidden}"', source), file.name


@pytest.mark.parametrize("setting", ["AWS_REGION", "AWS_DEFAULT_REGION", "SSM_PARAMETER_PREFIX", "ECR_REPOSITORY", "ARTIFACT_BUCKET"])
def test_ssm_bundle_exports_deploy_script_contract(setting):
    assert re.search(rf"^export {setting}=", text("ssm_deploy_document.tftpl"), re.MULTILINE)


def test_ssm_deploy_does_not_enable_shell_secret_tracing():
    assert not re.search(r"^set\s+-[^\s]*x", text("ssm_deploy_document.tftpl"), re.MULTILINE)


def test_bundle_integrity_is_checked_before_extraction():
    script = text("ssm_deploy_document.tftpl")
    assert script.index("sha256sum -c") < script.index("tar -xzf")
    # grep -q can terminate tar early; with pipefail, SIGPIPE can invert the
    # condition and accidentally skip rejection for a large unsafe archive.
    assert not re.search(r"tar -tzf[^\n]*\|\s*grep\s+-[^\s]*q", script)


def test_account_budget_cannot_silently_hide_untagged_credit_consumption():
    assert not re.search(r"^\s*cost_filter\s*\{", text("budget.tf"), re.MULTILINE)
