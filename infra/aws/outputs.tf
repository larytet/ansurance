output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "ecr_api_repository" {
  value = aws_ecr_repository.r["api"].repository_url
}

output "ecr_web_repository" {
  value = aws_ecr_repository.r["web"].repository_url
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "migrate_task_definition" {
  value = aws_ecs_task_definition.migrate.family
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "migrate_security_group_ids" {
  description = "Run the migrate task with the worker's security group (it may reach the database)."
  value       = [aws_security_group.worker.id]
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "originals_bucket" {
  value = aws_s3_bucket.b["originals"].bucket
}
