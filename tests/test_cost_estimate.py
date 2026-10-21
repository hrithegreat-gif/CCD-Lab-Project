"""Offline deterministic costs; these are not billing or deployment tests."""
from decimal import Decimal as D

import pytest

from scripts.cost_estimate import Usage, estimate, main, nonnegative


def test_original_mumbai_demo_estimate():
    result = estimate(Usage())
    assert result["aws_total"] == "17.86"
    assert result["aws_line_items"]["ec2"] == "10.08"
    assert result["aws_line_items"]["ebs_gp3"] == "3.65"
    assert result["groq_total_separate_billing"] == "0.00"
    assert result["region"] == "ap-south-1"


def test_two_month_project_includes_idle_storage_and_groq():
    result = estimate(Usage(
        running_hours=D("200"), storage_months=D("2"),
        groq_input_million=D("1"), groq_output_million=D("1"),
        groq_audio_hours=D("5"),
    ))
    assert result["aws_total"] == "35.71"
    assert result["groq_total_separate_billing"] == "0.95"
    assert result["combined_total"] == "36.66"


def test_always_on_estimate():
    assert estimate(Usage(running_hours=D("730")))["aws_total"] == "84.48"


def test_stopping_compute_does_not_stop_disk_storage_cost():
    result = estimate(Usage(running_hours=D("0")))
    assert result["aws_line_items"]["ec2"] == "0.00"
    assert result["aws_line_items"]["public_ipv4"] == "0.00"
    assert result["aws_line_items"]["ebs_gp3"] == "3.65"


def test_extra_nodes_have_extra_compute_and_disk_cost():
    result = estimate(Usage(instances=2))
    assert result["aws_line_items"]["ec2"] == "20.15"
    assert result["aws_line_items"]["ebs_gp3"] == "7.30"
    # ECR and S3 are shared, not incorrectly multiplied by node count.
    assert result["aws_line_items"]["ecr_storage"] == "0.50"


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "-Infinity", "not-a-number"])
def test_reject_invalid_numeric_inputs(value):
    from argparse import ArgumentTypeError
    with pytest.raises(ArgumentTypeError):
        nonnegative(value)


@pytest.mark.parametrize("usage", [
    Usage(instances=0), Usage(storage_months=D("0")),
    Usage(running_hours=D("731")), Usage(disk_gib=D("NaN")),
])
def test_reject_invalid_usage(usage):
    with pytest.raises(ValueError):
        estimate(usage)


def test_cli_outputs_json(capsys):
    assert main(["--hours", "200", "--months", "2"]) == 0
    assert '"aws_total": "35.71"' in capsys.readouterr().out
