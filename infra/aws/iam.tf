# Two roles: the execution role (pulls images, injects secrets) and the task role (what the
# application itself may do). The task role mirrors the on-prem rule: read and write the
# object store, never delete or weaken the originals.

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "secrets-and-keys"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_db_instance.main.master_user_secret[0].secret_arn
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.main.arn
      }
    ]
  })
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task_storage" {
  name = "object-store"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadWrite"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:GetObjectRetention", "s3:GetObjectVersion"]
        Resource = [
          "${local.bucket_arns["originals"]}/*",
          "${local.bucket_arns["derived"]}/*",
          "${local.bucket_arns["exports"]}/*",
          "${local.bucket_arns["audit_archive"]}/*",
        ]
      },
      {
        Sid    = "List"
        Effect = "Allow"
        Action = ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetBucketObjectLockConfiguration"]
        Resource = [
          local.bucket_arns["originals"],
          local.bucket_arns["derived"],
          local.bucket_arns["exports"],
          local.bucket_arns["audit_archive"],
        ]
      },
      {
        Sid      = "RebuildableMayBeDeleted"
        Effect   = "Allow"
        Action   = ["s3:DeleteObject"]
        Resource = ["${local.bucket_arns["derived"]}/*", "${local.bucket_arns["exports"]}/*"]
      },
      {
        Sid      = "Encryption"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt", "kms:DescribeKey"]
        Resource = aws_kms_key.main.arn
      }
    ]
  })
}

# A separate policy, with only known values, so tests can verify the guarantee: the
# application can never delete, shorten or bypass retention on the originals or the
# audit archive, whatever else is granted.
resource "aws_iam_role_policy" "task_originals_guard" {
  name = "never-touch-the-originals"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "NeverTouchTheOriginals"
        Effect = "Deny"
        Action = [
          "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutBucketPolicy", "s3:PutObjectRetention",
          "s3:BypassGovernanceRetention", "s3:DeleteBucket", "s3:PutBucketObjectLockConfiguration",
          "s3:PutBucketVersioning", "s3:PutLifecycleConfiguration",
        ]
        Resource = [
          local.bucket_arns["originals"],
          "${local.bucket_arns["originals"]}/*",
          local.bucket_arns["audit_archive"],
          "${local.bucket_arns["audit_archive"]}/*",
        ]
      },
    ]
  })
}
