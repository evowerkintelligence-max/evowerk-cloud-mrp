output "alb_dns_name" {
  description = "Point your DNS at this (CNAME) if route53_zone_id wasn't set"
  value       = aws_lb.main.dns_name
}

output "acm_certificate_validation_records" {
  description = "Only populated if domain_name was set and route53_zone_id was left empty - add these as CNAME records at your DNS provider, wait for validation, then re-apply."
  value = var.domain_name != "" && var.route53_zone_id == "" ? [
    for dvo in aws_acm_certificate.main[0].domain_validation_options : {
      name  = dvo.resource_record_name
      type  = dvo.resource_record_type
      value = dvo.resource_record_value
    }
  ] : []
}

output "app_url" {
  description = "Where to reach the app - the ALB DNS name over plain HTTP if domain_name was left empty, otherwise your domain over HTTPS."
  value       = var.domain_name != "" ? "https://${var.domain_name}" : "http://${aws_lb.main.dns_name}"
}

output "ecr_backend_repository_url" {
  description = "ECR repository to push the backend image to"
  value       = aws_ecr_repository.backend.repository_url
}

output "ecr_frontend_repository_url" {
  description = "ECR repository to push the frontend image to"
  value       = aws_ecr_repository.frontend.repository_url
}

output "rds_endpoint" {
  description = "RDS Postgres endpoint (host:port), reachable only from inside the VPC"
  value       = aws_db_instance.postgres.endpoint
  sensitive   = true
}

output "db_password" {
  description = "Only needed if you need to connect manually - the app itself reads DATABASE_URL from Secrets Manager"
  value       = random_password.db_password.result
  sensitive   = true
}
