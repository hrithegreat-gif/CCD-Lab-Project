"""Offline Mumbai lab estimate. No AWS login, API call, or resource changes.

Uses public rates checked on 2026-10-13, not account-specific discounts.
All arithmetic uses Decimal. This estimates AWS resources only.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

D = Decimal
RATES_CHECKED = "2026-10-13"
HOURS_PER_MONTH = D("730")
RATES = {
    "ec2_instance_hour": D("0.10075"),
    "public_ipv4_hour": D("0.005"),
    "gp3_gib_month": D("0.0912"),
    "ecr_gb_month": D("0.10"),
    "s3_standard_gb_month": D("0.025"),
    "s3_put_request": D("0.000005"),
    "s3_get_request": D("0.0000004"),
}


def nonnegative(value: str) -> Decimal:
    try:
        number = D(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("Must be a nonnegative finite number") from exc
    if not number.is_finite() or number < 0:
        raise argparse.ArgumentTypeError("Must be a nonnegative finite number")
    return number


@dataclass(frozen=True)
class Usage:
    running_hours: Decimal = D("100")  # hours per instance, over the whole project
    storage_months: Decimal = D("1")
    instances: int = 1
    disk_gib: Decimal = D("40")  # provisioned per instance, even when stopped
    ecr_gb: Decimal = D("5")
    s3_gb: Decimal = D("5")
    puts_per_month: Decimal = D("1000")
    gets_per_month: Decimal = D("10000")
    reserve_per_month: Decimal = D("3")  # monitoring/light usage allowance, NOT a cap


def estimate(usage: Usage) -> dict:
    if usage.instances < 1:
        raise ValueError("At least one instance is required")
    for name, value in vars(usage).items():
        if name != "instances" and (not value.is_finite() or value < 0):
            raise ValueError(f"{name} must be nonnegative and finite")
    if usage.storage_months <= 0:
        raise ValueError("Storage months must be positive")
    if usage.running_hours > usage.storage_months * HOURS_PER_MONTH:
        raise ValueError("Running hours per instance exceed the selected project duration")
    hours = usage.running_hours * usage.instances
    months = usage.storage_months
    costs = {
        "ec2": hours * RATES["ec2_instance_hour"],
        "public_ipv4": hours * RATES["public_ipv4_hour"],
        "ebs_gp3": usage.disk_gib * usage.instances * months * RATES["gp3_gib_month"],
        "ecr_storage": usage.ecr_gb * months * RATES["ecr_gb_month"],
        "s3_storage_and_requests": months * (
            usage.s3_gb * RATES["s3_standard_gb_month"]
            + usage.puts_per_month * RATES["s3_put_request"]
            + usage.gets_per_month * RATES["s3_get_request"]
        ),
        "monitoring_and_light_usage_reserve": months * usage.reserve_per_month,
    }
    aws_total = sum(costs.values(), D("0"))
    money = lambda value: str(value.quantize(D("0.01"), rounding=ROUND_HALF_UP))
    return {
        "currency": "USD",
        "region": "ap-south-1",
        "instance_type": "m7i-flex.large",
        "rates_checked": RATES_CHECKED,
        "running_hours_per_instance": str(usage.running_hours),
        "storage_months": str(months),
        "instances": usage.instances,
        "aws_line_items": {name: money(value) for name, value in costs.items()},
        "aws_total": money(aws_total),
        "caveats": [
            "Estimate, not a quote or spending cap; rates can change.",
            "Before taxes, account free allowances, credits, or discounts.",
            "No EKS, GPU, NAT gateway, load balancer, domain, paid KMS key, or backups.",
            "No large Internet/cross-region transfer or excessive logs/custom metrics.",
            "Ephemeral IPv4 released on instance stop; a retained Elastic IP costs extra.",
            "Storage is charged for all selected months, including stopped periods.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=nonnegative, default=D("100"), help="Running hours per instance across the entire project")
    parser.add_argument("--months", type=nonnegative, default=D("1"), help="Months retaining all storage")
    parser.add_argument("--instances", type=int, default=1)
    parser.add_argument("--disk-gib", type=nonnegative, default=D("40"))
    parser.add_argument("--ecr-gb", type=nonnegative, default=D("5"))
    parser.add_argument("--s3-gb", type=nonnegative, default=D("5"))
    parser.add_argument("--reserve", type=nonnegative, default=D("3"), help="Monthly reserve, not a hard cap")
    args = parser.parse_args(argv)
    try:
        result = estimate(Usage(
            running_hours=args.hours, storage_months=args.months,
            instances=args.instances, disk_gib=args.disk_gib,
            ecr_gb=args.ecr_gb, s3_gb=args.s3_gb,
            reserve_per_month=args.reserve,
        ))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
