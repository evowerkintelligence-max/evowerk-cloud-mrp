# trivy:ignore:AVD-AWS-0053 This ALB is the deployment's single public entry point (frontend only; backend stays private).
resource "aws_lb" "main" {
  name                       = "${local.name_prefix}-alb"
  internal                   = false
  drop_invalid_header_fields = true
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "frontend" {
  # AWS caps target group names at 32 chars; "-fe-tg" keeps the default
  # prefix ("evowerk-mrp-production", 22 chars) inside it.
  name        = "${local.name_prefix}-fe-tg"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/nginx-health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }

  # A rename replaces the target group; the listener still points at the old
  # one, so create the new one first or the destroy fails with "in use".
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate" "main" {
  count             = var.domain_name != "" ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# Only created if a Route53 zone was provided - otherwise the validation
# records are surfaced via the acm_certificate_validation_records output for
# you to add manually at your DNS provider. See README.md for the two-step
# apply this requires without Route53.
resource "aws_route53_record" "cert_validation" {
  for_each = var.domain_name != "" && var.route53_zone_id != "" ? {
    for dvo in aws_acm_certificate.main[0].domain_validation_options : dvo.domain_name => {
      name  = dvo.resource_record_name
      type  = dvo.resource_record_type
      value = dvo.resource_record_value
    }
  } : {}

  zone_id = var.route53_zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 300
  records = [each.value.value]
}

resource "aws_acm_certificate_validation" "main" {
  count                   = var.domain_name != "" && var.route53_zone_id != "" ? 1 : 0
  certificate_arn         = aws_acm_certificate.main[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# Only created when domain_name is set - see the http_forward listener below
# for the no-domain path (plain HTTP straight to the frontend).
resource "aws_lb_listener" "https" {
  count             = var.domain_name != "" ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.main[0].arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  count             = var.domain_name != "" ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# No-domain mode: no ACM cert to attach, so port 80 forwards straight to the
# frontend instead of redirecting to HTTPS. Fine for test deploys; switch to
# a real domain_name (see variables.tf) before this is a real deployment.
# trivy:ignore:AVD-AWS-0054 No-domain test mode only; with domain_name set, port 80 redirects to HTTPS (above).
resource "aws_lb_listener" "http_forward" {
  count             = var.domain_name == "" ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

resource "aws_route53_record" "app" {
  count   = var.route53_zone_id != "" ? 1 : 0
  zone_id = var.route53_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}
