# Only created when ses_from_email is set - customers who skip email flows
# entirely (password reset/verification stay log-only, see
# backend/app/core/email.py) don't need this.
resource "aws_ses_domain_identity" "main" {
  count  = var.ses_from_email != "" ? 1 : 0
  domain = split("@", var.ses_from_email)[1]
}

output "ses_domain_verification_record" {
  description = "TXT record to add at your DNS provider to verify the SES sending domain (skip if ses_from_email is unset)"
  value       = var.ses_from_email != "" ? aws_ses_domain_identity.main[0].verification_token : null
}
