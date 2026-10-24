# A 5 EUR monthly budget on this project. It alerts, it never stops anything: the real brakes are Cloud Run's
# 2 instances at most, the 3-day lifecycle of the raw bucket and the rate limit in front of everything.
#
# No all_updates_rule: with only threshold rules, Cloud Billing emails the billing account administrators and users
# at each threshold (the default recipients), which is the wanted channel.
resource "google_billing_budget" "s1d1" {
  billing_account = var.billing_account
  display_name    = "s1d1-alliance"

  budget_filter {
    projects        = ["projects/${data.google_project.this.number}"]
    calendar_period = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.api]
}
