#!/bin/sh
set -eu

workflow_dir=/opt/loan-engine/workflows
compose_file=${COMPOSE_FILE:-docker-compose.yml}
if [ ! -f "$compose_file" ] && [ -f docker-compose.aws.yml ]; then
  compose_file=docker-compose.aws.yml
fi

read_env_value() {
  [ -f .env ] || return 0
  awk -F= -v key="$1" '$1 == key { value=substr($0, index($0, "=") + 1) } END { print value }' .env
}
N8N_SES_ROLE_ARN=${N8N_SES_ROLE_ARN:-$(read_env_value N8N_SES_ROLE_ARN)}
N8N_SES_EXTERNAL_ID=${N8N_SES_EXTERNAL_ID:-$(read_env_value N8N_SES_EXTERNAL_ID)}

tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/loan-engine-n8n.XXXXXX")
chmod 755 "$tmp_dir"
stopped=false
cleanup() {
  if [ "$stopped" = true ]; then
    docker compose -f "$compose_file" up -d n8n >/dev/null 2>&1 || true
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

import_dir=$workflow_dir
if [ -n "$N8N_SES_ROLE_ARN" ] && [ -n "$N8N_SES_EXTERNAL_ID" ]; then
  import_dir="$tmp_dir/workflows"
  mkdir -p "$import_dir"
  cp "$workflow_dir"/*.json "$import_dir/"
  python3 - "$import_dir" "$tmp_dir/aws-ses-credential.json" "$N8N_SES_ROLE_ARN" "$N8N_SES_EXTERNAL_ID" <<'PY'
import json
import pathlib
import sys

directory = pathlib.Path(sys.argv[1])
credential_path = pathlib.Path(sys.argv[2])
role_arn, external_id = sys.argv[3:]
credential_id = "f12e4a9b-7a04-41fb-9fe7-0d64bb48f748"
credential_name = "ClickPe AWS SES via EC2 role"

credential = [{
    "id": credential_id,
    "name": credential_name,
    "type": "awsAssumeRole",
    "data": {
        "region": "ap-south-1",
        "useSystemCredentialsForRole": True,
        "stsAccessKeyId": "",
        "stsSecretAccessKey": "",
        "stsSessionToken": "",
        "roleArn": role_arn,
        "externalId": external_id,
        "roleSessionName": "n8n-clickpe-ses",
    },
}]
credential_path.write_text(json.dumps(credential) + "\n")
credential_path.chmod(0o644)

workflow_path = directory / "user-notifications.json"
workflow = json.loads(workflow_path.read_text())
node = next((item for item in workflow["nodes"] if item.get("name") == "Send through AWS SES"), None)
if node is None:
    raise SystemExit("Could not find the AWS SES node in Workflow C")
node.setdefault("parameters", {})["authentication"] = "assumeRole"
node["credentials"] = {
    "awsAssumeRole": {"id": credential_id, "name": credential_name}
}
workflow_path.write_text(json.dumps(workflow, indent=2) + "\n")
PY
  chmod 644 "$tmp_dir"/*.json
fi

docker compose -f "$compose_file" stop n8n
stopped=true
if [ -n "$N8N_SES_ROLE_ARN" ] && [ -n "$N8N_SES_EXTERNAL_ID" ]; then
  docker compose -f "$compose_file" run --rm --no-deps \
    -v "$tmp_dir:/tmp/loan-engine-import:ro" n8n \
    import:credentials --input=/tmp/loan-engine-import/aws-ses-credential.json
fi
if [ "$import_dir" = "$workflow_dir" ]; then
  docker compose -f "$compose_file" run --rm --no-deps n8n import:workflow --separate --input="$workflow_dir"
else
  docker compose -f "$compose_file" run --rm --no-deps \
    -v "$tmp_dir:/tmp/loan-engine-import:ro" n8n \
    import:workflow --separate --input=/tmp/loan-engine-import/workflows
fi

for workflow_id in \
  nxrGzgC0uehL66j4 \
  qyxDzuhqde7gQ9gz \
  iAdA74x2KVehY7fB
do
  docker compose -f "$compose_file" run --rm --no-deps n8n publish:workflow --id="$workflow_id"
done

docker compose -f "$compose_file" up -d n8n
stopped=false
trap - EXIT
rm -rf "$tmp_dir"
