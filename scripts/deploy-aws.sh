#!/usr/bin/env bash
# One-shot AWS deployment orchestrator for the loan-eligibility-engine.
# Prereqs: .env filled per the ".env.example AWS deployment inputs" section
# (except values this script fills for you: *_SECRET_ARN, UPLOAD_*), AWS creds
# activated for every service, and serverless CLI available. Safe to re-run;
# it skips work that already exists.

set -eu

set -a
source .env
set +a
# .env may carry AWS_PROFILE=clickpe (the unactivated account); the deployment
# must always run against the activated default/login credentials.
unset AWS_PROFILE

# Serverless' bundled SDK cannot resolve `aws login`'s `login_session` credential
# source in ~/.aws/config. Ask the AWS CLI to resolve/refresh that session, then
# export its current temporary credentials for Serverless and Docker tooling.
DEPLOY_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
eval "$(aws configure export-credentials --profile default --format env)"
aws sts get-caller-identity --output json | head -c 200 && echo

REGION="${AWS_REGION:-ap-south-1}"
STAGE="${STAGE:-dev}"
STACK="loan-eligibility-engine-${STAGE}"
KEY_NAME="loan-engine-${STAGE}"

out () { aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null; }

_step () { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }
refresh_aws_credentials () {
  eval "$(aws configure export-credentials --profile default --format env)"
}
SSH_INGRESS_CIDR=""
cleanup_ssh_ingress () {
  if [ -n "$SSH_INGRESS_CIDR" ] && [ -n "${N8N_SG_ID:-}" ]; then
    aws ec2 revoke-security-group-ingress --region "$REGION" --group-id "$N8N_SG_ID" \
      --protocol tcp --port 22 --cidr "$SSH_INGRESS_CIDR" >/dev/null 2>&1 || true
  fi
}
trap cleanup_ssh_ingress EXIT

# ── requirement checks ────────────────────────────────────────────────────────
for v in FIREBASE_PROJECT_ID FIREBASE_BOOTSTRAP_ADMIN_EMAIL N8N_SERVICE_TOKEN N8N_ENCRYPTION_KEY SES_FROM_EMAIL; do
  if [ -z "$(printenv "$v" || true)" ]; then echo "Missing required env: $v (see .env.example AWS section)"; exit 1; fi
done

# ── 1. Secrets Manager ────────────────────────────────────────────────────────
_step "1/8 Secrets Manager"
if [ -z "${FIREBASE_SERVICE_ACCOUNT_SECRET_ARN:-}" ]; then
  NAME="loan-engine-${STAGE}/firebase-service-account"
  AUX=$(aws secretsmanager describe-secret --region "$REGION" --secret-id "$NAME" --query ARN --output text 2>/dev/null) || AUX=""
  [ -z "$AUX" ] && AUX=$(aws secretsmanager create-secret --region "$REGION" \
    --name "$NAME" --secret-string "file://secrets/firebase-service-account.json" \
    --tags Key=project,Value=loan-eligibility-engine Key=stage,Value="$STAGE" --query ARN --output text)
  FIREBASE_SERVICE_ACCOUNT_SECRET_ARN="$AUX"
  sed -i.bak '/^FIREBASE_SERVICE_ACCOUNT_SECRET_ARN=/d' .env
  echo "FIREBASE_SERVICE_ACCOUNT_SECRET_ARN=${FIREBASE_SERVICE_ACCOUNT_SECRET_ARN}" >> .env
  echo "created/verified: $FIREBASE_SERVICE_ACCOUNT_SECRET_ARN"
fi

if [ -z "${N8N_SERVICE_TOKEN_SECRET_ARN:-}" ]; then
  NAME="loan-engine-${STAGE}/n8n-service-token"
  AUX=$(aws secretsmanager describe-secret --region "$REGION" --secret-id "$NAME" --query ARN --output text 2>/dev/null) || AUX=""
  [ -z "$AUX" ] && AUX=$(aws secretsmanager create-secret --region "$REGION" \
    --name "$NAME" --secret-string "$N8N_SERVICE_TOKEN" \
    --tags Key=project,Value=loan-eligibility-engine Key=stage,Value="$STAGE" --query ARN --output text)
  N8N_SERVICE_TOKEN_SECRET_ARN="$AUX"
  sed -i.bak '/^N8N_SERVICE_TOKEN_SECRET_ARN=/d' .env
  echo "N8N_SERVICE_TOKEN_SECRET_ARN=${N8N_SERVICE_TOKEN_SECRET_ARN}" >> .env
  echo "created/verified: $N8N_SERVICE_TOKEN_SECRET_ARN"
fi
LN_ENV="FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID}
FIREBASE_SERVICE_ACCOUNT_SECRET_ARN=${FIREBASE_SERVICE_ACCOUNT_SECRET_ARN}
N8N_SERVICE_TOKEN_SECRET_ARN=${N8N_SERVICE_TOKEN_SECRET_ARN}
N8N_MATCH_WEBHOOK=${N8N_MATCH_WEBHOOK:-}
APP_ORIGIN=${APP_ORIGIN:-}
SES_FROM_EMAIL=${SES_FROM_EMAIL}"

# ── 2. SES identity ───────────────────────────────────────────────────────────
_step "2/8 SES"
aws sesv2 create-email-identity --region "$REGION" --email-identity "$SES_FROM_EMAIL" >/dev/null 2>&1 || true
echo "SES identity ensured: ${SES_FROM_EMAIL}. Sandbox mode: also verify every recipient you test with."

# ── 3. Serverless deploy ──────────────────────────────────────────────────────
_step "3/8 Serverless deploy"
ECR_REPOSITORY="serverless-${STACK}"
ECR_REGISTRY="${DEPLOY_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
ECR_IMAGE_URI="${ECR_REGISTRY}/${ECR_REPOSITORY}:upload-worker"
aws ecr describe-repositories --region "$REGION" --repository-names "$ECR_REPOSITORY" >/dev/null 2>&1 || \
  aws ecr create-repository --region "$REGION" --repository-name "$ECR_REPOSITORY" \
    --image-scanning-configuration scanOnPush=true >/dev/null
IMAGE_MEDIA_TYPE=$(aws ecr describe-images --region "$REGION" --repository-name "$ECR_REPOSITORY" \
  --image-ids imageTag=upload-worker --query 'imageDetails[0].imageManifestMediaType' --output text 2>/dev/null) || IMAGE_MEDIA_TYPE=""
if [ "$IMAGE_MEDIA_TYPE" != "application/vnd.docker.distribution.manifest.v2+json" ] && \
   [ "$IMAGE_MEDIA_TYPE" != "application/vnd.oci.image.manifest.v1+json" ]; then
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
  docker buildx build --platform linux/amd64 --provenance=false \
    --output "type=docker,dest=/tmp/loan-engine-upload-worker.tar" \
    --tag loan-engine-upload-worker:deploy \
    --file backend/Dockerfile.lambda backend
  docker load --input /tmp/loan-engine-upload-worker.tar >/dev/null
  docker tag loan-engine-upload-worker:deploy "$ECR_IMAGE_URI"
  docker push "$ECR_IMAGE_URI"
  IMAGE_MEDIA_TYPE=$(aws ecr describe-images --region "$REGION" --repository-name "$ECR_REPOSITORY" \
    --image-ids imageTag=upload-worker --query 'imageDetails[0].imageManifestMediaType' --output text)
else
  echo "Reusing existing Lambda-compatible ECR image: ${IMAGE_MEDIA_TYPE}"
fi
if [ "$IMAGE_MEDIA_TYPE" = "application/vnd.oci.image.index.v1+json" ] || \
   [ "$IMAGE_MEDIA_TYPE" = "application/vnd.docker.distribution.manifest.list.v2+json" ]; then
  echo "Refusing Lambda multi-platform image manifest: $IMAGE_MEDIA_TYPE"; exit 1
fi
export UPLOAD_WORKER_IMAGE_URI="$ECR_IMAGE_URI"
echo "Lambda image: ${ECR_IMAGE_URI} (${IMAGE_MEDIA_TYPE})"
refresh_aws_credentials
npx serverless deploy --stage "$STAGE" --region "$REGION"

# ── 4. Stack outputs ──────────────────────────────────────────────────────────
_step "4/8 Stack outputs"
UPLOAD_BUCKET=$(out UploadBucketName)
DB_HOST=$(out DatabaseHost)
N8N_SG_ID=$(out N8nWorkflowSecurityGroupId)
VPC_ID=$(out VpcId)
PUBLIC_SUBNET=$(out PublicSubnetId)
PROFILE_ARN=$(out N8nInstanceProfileArn)
PROFILE_NAME=${PROFILE_ARN##*/}
N8N_SES_ROLE_ARN=$(out N8nSesRoleArn)
N8N_SES_EXTERNAL_ID=$(out N8nSystemCredentialsExternalId)
PRESIGN_URL=$(out HttpApiEndpoint)
echo "bucket:  $UPLOAD_BUCKET"; echo "db host: $DB_HOST"; echo "sg:      $N8N_SG_ID"; echo "presign: $PRESIGN_URL"

# ── 5. EC2 n8n host ───────────────────────────────────────────────────────────
_step "5/8 EC2 host (t3.micro, public subnet, n8n instance profile)"
aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" >/dev/null 2>&1 || \
  aws ec2 create-key-pair --key-name "$KEY_NAME" --region "$REGION" \
  --query KeyMaterial --output text > "scripts/${KEY_NAME}.pem"
chmod 600 "scripts/${KEY_NAME}.pem" 2>/dev/null || true

IID=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:project,Values=loan-eligibility-engine Name=tag:stage,Values="$STAGE" \
            Name=instance-state-name,Values=running,pending \
  --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null) || IID=""
if [ -z "$IID" ]; then
  AMI=$(aws ssm get-parameters --region "$REGION" \
    --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
    --query 'Parameters[0].Value' --output text)
  IID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI" --instance-type t3.micro \
    --key-name "$KEY_NAME" --subnet-id "$PUBLIC_SUBNET" --security-group-ids "$N8N_SG_ID" \
    --associate-public-ip-address \
    --iam-instance-profile Name="$PROFILE_NAME" \
    --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=20,VolumeType=gp3}" \
    --metadata-options "HttpTokens=required,HttpPutResponseHopLimit=2" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=project,Value=loan-eligibility-engine},{Key=stage,Value=$STAGE}]" \
    --query 'Instances[0].InstanceId' --output text)
fi
aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[].Instances[].PublicIpAddress' --output text)
PRIV_IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[].Instances[].PrivateIpAddress' --output text)
echo "instance: $IID  public: $IP  private: $PRIV_IP"

# ── 6. Host env + Lambda webhook├── one redeploy for the Lambda-side value ────
_step "6/8 Host .env + final Lambda env pass"
sed -i.bak '/^N8N_MATCH_WEBHOOK=/d' .env
echo "N8N_MATCH_WEBHOOK=http://${PRIV_IP}:5678/webhook/loan-match" >> .env
sed -i.bak '/^APP_ORIGIN=/d' .env
echo "APP_ORIGIN=https://app.${IP}.nip.io" >> .env
refresh_aws_credentials
npx serverless deploy --stage "$STAGE" --region "$REGION"
cat > .env.aws <<EOF
DB_HOST=${DB_HOST}
DB_PORT=5432
DB_NAME=${DB_NAME:-loan_engine}
DB_MASTER_USERNAME=${DB_MASTER_USERNAME:-loan_engine}
DB_PASSWORD=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$(out DatabaseSecretArn)" --query SecretString --output text | python3 -c "import json,sys; print(json.load(sys.stdin)['password'])")
APP_ORIGIN=https://app.${IP}.nip.io
LLM_API_KEY=${LLM_API_KEY:-}
LLM_MODEL=${LLM_MODEL:-gemini-flash-latest}
FIREBASE_BOOTSTRAP_ADMIN_EMAIL=${FIREBASE_BOOTSTRAP_ADMIN_EMAIL}
SESSION_COOKIE_DOMAIN=${IP}.nip.io
APP_DOMAIN=app.${IP}.nip.io
API_DOMAIN=api.${IP}.nip.io
N8N_DOMAIN=n8n.${IP}.nip.io
UPLOAD_API_DOMAIN=${PRESIGN_URL#https://}
VITE_UPLOAD_PRESIGN_URL=https://api.${IP}.nip.io/uploads/presign
FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID}
VITE_FIREBASE_PROJECT_ID=${VITE_FIREBASE_PROJECT_ID:-${FIREBASE_PROJECT_ID}}
VITE_FIREBASE_API_KEY=${VITE_FIREBASE_API_KEY:-}
VITE_FIREBASE_AUTH_DOMAIN=${VITE_FIREBASE_AUTH_DOMAIN:-}
VITE_FIREBASE_STORAGE_BUCKET=${VITE_FIREBASE_STORAGE_BUCKET:-}
VITE_FIREBASE_MESSAGING_SENDER_ID=${VITE_FIREBASE_MESSAGING_SENDER_ID:-}
VITE_FIREBASE_APP_ID=${VITE_FIREBASE_APP_ID:-}
N8N_SERVICE_TOKEN=${N8N_SERVICE_TOKEN}
N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}
N8N_SES_ROLE_ARN=${N8N_SES_ROLE_ARN}
N8N_SES_EXTERNAL_ID=${N8N_SES_EXTERNAL_ID}
NOTIFICATION_TEST_RECIPIENT=${NOTIFICATION_TEST_RECIPIENT:-${SES_FROM_EMAIL}}
SES_FROM_EMAIL=${SES_FROM_EMAIL}
EOF
chmod 600 .env.aws

# ── 7. Ship project to host and bring the stack up ────────────────────────────
_step "7/8 Ship project + docker compose up (this builds the frontend; takes ~10 min on t3.micro)"
MY_PUBLIC_IP=$(curl -fsS --max-time 10 https://checkip.amazonaws.com | tr -d '\r\n')
SSH_INGRESS_CIDR="${MY_PUBLIC_IP}/32"
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$N8N_SG_ID" \
  --protocol tcp --port 22 --cidr "$SSH_INGRESS_CIDR" >/dev/null 2>&1 || true
TARBALL=/tmp/${STAGE}-loan-engine.tar.gz
COPYFILE_DISABLE=1 tar --exclude='**/node_modules' --exclude='.git' --exclude='.serverless' \
  --exclude='uploads/*' --exclude='*.pem' --exclude='.env*' \
  -czf "$TARBALL" backend frontend workflows scripts docker-compose.aws.yml Caddyfile
scp -o StrictHostKeyChecking=accept-new -i "scripts/${KEY_NAME}.pem" "$TARBALL" "ec2-user@${IP}:/tmp/bootstrap.tar.gz"
scp -o StrictHostKeyChecking=accept-new -i "scripts/${KEY_NAME}.pem" .env.aws "ec2-user@${IP}:/tmp/bootstrap.env"
scp -o StrictHostKeyChecking=accept-new -i "scripts/${KEY_NAME}.pem" \
  "secrets/firebase-service-account.json" "ec2-user@${IP}:/tmp/firebase.json"
ssh -o StrictHostKeyChecking=accept-new -i "scripts/${KEY_NAME}.pem" "ec2-user@${IP}" sh -s <<'REMOTE'
set -eu
sudo dnf install -y docker amazon-cloudwatch-agent >/dev/null
sudo systemctl enable --now docker
sudo install -d -m 0755 /usr/local/lib/docker/cli-plugins
sudo curl -fsSL https://github.com/docker/compose/releases/download/v2.39.4/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
sudo mkdir -p /opt/loan-engine
sudo tar -xzf /tmp/bootstrap.tar.gz -C /opt/loan-engine
sudo mv /tmp/bootstrap.env /opt/loan-engine/.env
sudo mkdir -p /opt/loan-engine/secrets
sudo mv /tmp/firebase.json /opt/loan-engine/secrets/firebase-service-account.json
sudo chmod 600 /opt/loan-engine/.env /opt/loan-engine/secrets/firebase-service-account.json
cd /opt/loan-engine
sudo docker compose -f docker-compose.aws.yml up -d --build
echo "STACK UP"
REMOTE

# ── 8. Import n8n workflows + wrap-up ─────────────────────────────────────────
_step "8/8 n8n workflow import + URLs"
ssh -o StrictHostKeyChecking=accept-new -i "scripts/${KEY_NAME}.pem" "ec2-user@${IP}" \
  "cd /opt/loan-engine && sudo sh scripts/import-n8n-workflows.sh"

cat <<EOF

Deployment complete.

  App:   https://app.${IP}.nip.io
  API:   https://api.${IP}.nip.io
  n8n:   https://n8n.${IP}.nip.io
  Presign (API Gateway): ${PRESIGN_URL}
  EC2 instance: ${IID}

  Remaining manual bits:
   - Approve the SES verification email sent to ${SES_FROM_EMAIL}
   - In the Firebase console (or identitytoolkit admin API), authorize domains:
     app.${IP}.nip.io and n8n.${IP}.nip.io
EOF
