# Optional ALERT-ONLY budget, deliberately ACCOUNT-WIDE: the lab's ~$100 credit
# is shared by every resource in the account, and a tag filter would report $0
# until the "Project" tag is activated as a cost-allocation tag (silently hiding
# overspend), so no cost filter is applied.
# DISCLOSURES: (a) notifications can lag up to ~24 hours (alerts evaluate about
# once per day) and a budget NEVER blocks spend — it is not a hard cap;
# (b) this is a MONTHLY rate alert, not a lifetime cap on the credit balance.
# The Budgets API exists only in us-east-1 (global billing data), hence the
# aws.global provider alias.
resource "aws_budgets_budget" "monthly" {
  count = var.budget_limit == null ? 0 : 1

  provider     = aws.global
  name         = "${var.project_prefix}-monthly-account-wide"
  budget_type  = "COST"
  time_unit    = "MONTHLY"
  account_id   = local.account_id
  limit_amount = tostring(var.budget_limit)
  limit_unit   = "USD"

  # Each threshold is a separate notification (Budgets API rule).
  dynamic "notification" {
    for_each = var.budget_email == null ? [] : [1]

    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = "ACTUAL"
      threshold                  = 80
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.budget_email]
    }
  }

  dynamic "notification" {
    for_each = var.budget_email == null ? [] : [1]

    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = "FORECASTED"
      threshold                  = 100
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.budget_email]
    }
  }
}
