#!/usr/bin/env bash
# Bootstrap a short-lived GitHub Actions deploy role for the existing dev stack.
# Run once locally after `aws login`; no AWS access key is written to GitHub.

set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
STAGE="${STAGE:-dev}"
APP_STACK="loan-eligibility-engine-${STAGE}"
ROLE_STACK="loan-eligibility-engine-github-actions-${STAGE}"
REPOSITORY="${GITHUB_REPOSITORY_NAME:-shwejan005/clickpe}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$APP_STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='UploadBucketName'].OutputValue" --output text)
INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:project,Values=loan-eligibility-engine Name=tag:stage,Values="$STAGE" \
            Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
ECR_REPOSITORY="serverless-${APP_STACK}"

if [ -z "$BUCKET" ] || [ "$BUCKET" = None ] || [ -z "$INSTANCE_ID" ] || \
   [ "$(printf '%s\n' "$INSTANCE_ID" | wc -w | tr -d ' ')" -ne 1 ]; then
  echo "Could not resolve the deployed app bucket or running tagged EC2 instance." >&2
  exit 1
fi

aws cloudformation deploy \
  --region "$REGION" \
  --template-file infra/github-actions-deploy-role.yml \
  --stack-name "$ROLE_STACK" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    "GitHubRepository=${REPOSITORY}" \
    "Stage=${STAGE}" \
    "DeploymentBucketName=${BUCKET}" \
    "EcrRepositoryName=${ECR_REPOSITORY}" \
    "Ec2InstanceId=${INSTANCE_ID}" \
    "LambdaNamePrefix=${APP_STACK}-"

LIFECYCLE_FILE=$(mktemp)
trap 'rm -f "$LIFECYCLE_FILE"' EXIT
cat > "$LIFECYCLE_FILE" <<'JSON'
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Expire untagged Lambda build images after 14 days",
      "selection": {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 14
      },
      "action": {"type": "expire"}
    }
  ]
}
JSON
aws ecr put-lifecycle-policy --region "$REGION" --repository-name "$ECR_REPOSITORY" \
  --lifecycle-policy-text "file://${LIFECYCLE_FILE}" >/dev/null

ROLE_ARN=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$ROLE_STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='GitHubActionsDeployRoleArn'].OutputValue" --output text)
echo "GitHub Actions OIDC role is ready: ${ROLE_ARN}"
echo "Trust is restricted to ${REPOSITORY}:ref:refs/heads/main."
