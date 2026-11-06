# Container registry, ECS (Fargate) services, and the load balancer.

locals {
  service_dns = "${local.name}.local"
  api_image   = "${aws_ecr_repository.r["api"].repository_url}:${var.image_tag}"
  web_image   = "${aws_ecr_repository.r["web"].repository_url}:${var.image_tag}"

  common_env = [
    { name = "ANS_PROFILE", value = "cloud" },
    { name = "ANS_DB_HOST", value = aws_db_instance.main.address },
    { name = "ANS_DB_NAME", value = "ansurance" },
    { name = "ANS_DB_USER", value = "ansurance" },
    { name = "ANS_S3_REGION", value = var.region },
    { name = "ANS_S3_BUCKET_ORIGINALS", value = aws_s3_bucket.b["originals"].bucket },
    { name = "ANS_S3_BUCKET_AUDIT_ARCHIVE", value = aws_s3_bucket.b["audit_archive"].bucket },
    { name = "ANS_CLAMD_HOST", value = "clamav.${local.service_dns}" },
  ]

  common_secrets = [
    { name = "ANS_DB_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" },
  ]
}

resource "aws_ecr_repository" "r" {
  for_each             = toset(["api", "web"])
  name                 = "${var.name_prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }
}

resource "aws_ecr_lifecycle_policy" "r" {
  for_each   = aws_ecr_repository.r
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 30 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name = local.service_dns
  vpc  = aws_vpc.main.id
}

resource "aws_service_discovery_service" "clamav" {
  name = "clamav"
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }
  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = toset(["api", "worker", "web", "clamav", "migrate"])
  name              = "/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

# --- task definitions ---------------------------------------------------------------------
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name                   = "api"
    image                  = local.api_image
    essential              = true
    command                = ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
    portMappings           = [{ containerPort = 8000, protocol = "tcp" }]
    environment            = local.common_env
    secrets                = local.common_secrets
    readonlyRootFilesystem = true
    healthCheck = {
      command     = ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/ready').status==200 else 1)"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options   = { "awslogs-group" = aws_cloudwatch_log_group.svc["api"].name, "awslogs-region" = var.region, "awslogs-stream-prefix" = "api" }
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name        = "worker"
    image       = local.api_image
    essential   = true
    command     = ["python", "-m", "app.jobs.worker"]
    environment = local.common_env
    secrets     = local.common_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options   = { "awslogs-group" = aws_cloudwatch_log_group.svc["worker"].name, "awslogs-region" = var.region, "awslogs-stream-prefix" = "worker" }
    }
  }])
}

# Run once per release, before the services are updated: schema migrations and seed data.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name        = "migrate"
    image       = local.api_image
    essential   = true
    command     = ["sh", "-c", "alembic upgrade head && python -m app.cli seed-processes"]
    environment = local.common_env
    secrets     = local.common_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options   = { "awslogs-group" = aws_cloudwatch_log_group.svc["migrate"].name, "awslogs-region" = var.region, "awslogs-stream-prefix" = "migrate" }
    }
  }])
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn

  container_definitions = jsonencode([{
    name         = "web"
    image        = local.web_image
    essential    = true
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options   = { "awslogs-group" = aws_cloudwatch_log_group.svc["web"].name, "awslogs-region" = var.region, "awslogs-stream-prefix" = "web" }
    }
  }])
}

resource "aws_ecs_task_definition" "clamav" {
  family                   = "${local.name}-clamav"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 3072
  execution_role_arn       = aws_iam_role.execution.arn

  container_definitions = jsonencode([{
    name         = "clamav"
    image        = var.clamav_image
    essential    = true
    portMappings = [{ containerPort = 3310, protocol = "tcp" }]
    healthCheck = {
      command     = ["CMD", "clamdcheck.sh"]
      interval    = 30
      timeout     = 10
      retries     = 5
      startPeriod = 180
    }
    logConfiguration = {
      logDriver = "awslogs"
      options   = { "awslogs-group" = aws_cloudwatch_log_group.svc["clamav"].name, "awslogs-region" = var.region, "awslogs-stream-prefix" = "clamav" }
    }
  }])
}

# --- services -----------------------------------------------------------------------------
resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  health_check_grace_period_seconds = 60
  propagate_tags                    = "SERVICE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"
  propagate_tags  = "SERVICE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"
  propagate_tags  = "SERVICE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8080
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "clamav" {
  name            = "clamav"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.clamav.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  propagate_tags  = "SERVICE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.clamav.id]
    assign_public_ip = false
  }
  service_registries {
    registry_arn = aws_service_discovery_service.clamav.arn
  }
}

# --- load balancer --------------------------------------------------------------------------
resource "aws_lb" "main" {
  name                       = local.name
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection
  idle_timeout               = 120

  access_logs {
    bucket  = aws_s3_bucket.b["alb_logs"].bucket
    prefix  = "alb"
    enabled = true
  }
  depends_on = [aws_s3_bucket_policy.b]
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/api/ready"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  deregistration_delay = 30
}

resource "aws_lb_target_group" "web" {
  name        = "${local.name}-web"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path    = "/"
    matcher = "200"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  condition {
    path_pattern {
      values = ["/api/*"]
    }
  }
}

resource "aws_lb_listener" "http" {
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

# Optional web application firewall (decision D10: needed when the system is not behind a VPN).
resource "aws_wafv2_web_acl" "main" {
  count = var.enable_waf ? 1 : 0
  name  = local.name
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "common"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "rate-limit"
    priority = 2
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-rate"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = local.name
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "main" {
  count        = var.enable_waf ? 1 : 0
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main[0].arn
}
