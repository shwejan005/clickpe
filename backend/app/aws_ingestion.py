"""Authenticated S3 upload and event-driven PostgreSQL ingestion handlers."""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote_plus
from urllib.request import Request, urlopen

import boto3
import psycopg2
from firebase_admin import auth, credentials, get_app, initialize_app
from psycopg2.extras import Json, execute_values

from .validation import validate_row

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
REQUIRED_COLUMNS = {
    "user_id", "name", "email", "monthly_income", "credit_score", "employment_status", "age"
}
INSERT_APPLICANTS_SQL = """
    INSERT INTO applicants (
        batch_id, external_user_id, name, email, monthly_income, credit_score,
        employment_status, employment_segment, age, currency, country_code
    ) VALUES %s
    ON CONFLICT (batch_id, external_user_id) DO NOTHING
"""

_s3 = boto3.client("s3")
_secrets = boto3.client("secretsmanager")
_db_secret_cache: dict[str, str] | None = None


def _headers(event: dict[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in (event.get("headers") or {}).items()}


def _cookie_values(event: dict[str, Any]) -> str:
    headers = _headers(event)
    values = [headers.get("cookie", "")]
    values.extend(event.get("cookies") or [])
    return "; ".join(value for value in values if value)


def _cookie(event: dict[str, Any], name: str) -> str:
    parsed = SimpleCookie()
    try:
        parsed.load(_cookie_values(event))
    except Exception:
        return ""
    return parsed[name].value if name in parsed else ""


def _firebase_app():
    try:
        return get_app("loan-engine-upload")
    except ValueError:
        pass
    secret_arn = os.environ["FIREBASE_SERVICE_ACCOUNT_SECRET_ARN"]
    secret = _secrets.get_secret_value(SecretId=secret_arn)["SecretString"]
    service_account = json.loads(secret)
    project_id = os.environ["FIREBASE_PROJECT_ID"]
    if service_account.get("project_id") != project_id:
        raise ValueError("Firebase service account project does not match configuration")
    return initialize_app(
        credentials.Certificate(service_account),
        {"projectId": project_id},
        name="loan-engine-upload",
    )


def authorizer_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Validate the dashboard's HttpOnly Firebase session cookie for API Gateway."""
    session_cookie = _cookie(event, "clickpe_session")
    if not session_cookie:
        return {"isAuthorized": False}
    try:
        claims = auth.verify_session_cookie(session_cookie, app=_firebase_app(), check_revoked=True)
        uid = str(claims.get("uid", ""))
        email = str(claims.get("email", "")).strip().lower()
        if not uid or not email or not claims.get("email_verified"):
            return {"isAuthorized": False}
        return {"isAuthorized": True, "context": {"uid": uid, "email": email}}
    except Exception as exc:
        logger.info("Upload session rejected: %s", type(exc).__name__)
        return {"isAuthorized": False}


def _db_connect():
    global _db_secret_cache
    if _db_secret_cache is None:
        secret_response = _secrets.get_secret_value(SecretId=os.environ["DB_SECRET_ARN"])
        _db_secret_cache = json.loads(secret_response["SecretString"])
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.environ["DB_NAME"],
        user=_db_secret_cache["username"],
        password=_db_secret_cache["password"],
        sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=8,
        application_name="loan-engine-s3-ingestion",
    )


@contextmanager
def _managed_connection():
    connection = _db_connect()
    try:
        yield connection
    finally:
        connection.close()


def _cors_origin(event: dict[str, Any]) -> str | None:
    origin = _headers(event).get("origin", "")
    configured = os.getenv("APP_ORIGIN", "").rstrip("/")
    return origin if configured and origin.rstrip("/") == configured else None


def _api_response(event: dict[str, Any], status: int, body: dict[str, Any]) -> dict[str, Any]:
    origin = _cors_origin(event)
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    if origin:
        headers.update({
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        })
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def _safe_filename(value: Any) -> str:
    name = PurePosixPath(str(value or "")).name
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("._")[:120]
    return name if name.lower().endswith(".csv") else ""


def presign_upload_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Issue a short-lived S3 PUT URL only to an authenticated workspace analyst."""
    origin = _cors_origin(event)
    if not origin:
        return _api_response(event, 403, {"detail": "Upload origin is not allowed"})

    csrf_cookie = _cookie(event, "clickpe_csrf")
    csrf_header = _headers(event).get("x-csrf-token", "")
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        return _api_response(event, 403, {"detail": "CSRF validation failed; refresh the page and retry"})

    request_context = (event.get("requestContext") or {}).get("authorizer") or {}
    auth_context = request_context.get("lambda") or {}
    firebase_uid = str(auth_context.get("uid", ""))
    if not firebase_uid:
        return _api_response(event, 401, {"detail": "Sign in to continue"})

    try:
        raw_body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        payload = json.loads(raw_body)
        workspace_id = str(payload.get("workspace_id", ""))
        file_name = _safe_filename(payload.get("file_name"))
        content_length = int(payload.get("content_length", 0))
        if not workspace_id or not file_name:
            return _api_response(event, 400, {"detail": "A workspace and .csv file are required"})
        if not 0 < content_length <= MAX_UPLOAD_BYTES:
            return _api_response(event, 413, {"detail": "The upload size must be between 1 byte and 25 MB"})

        with _managed_connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.workspace_role
                FROM app_users AS u
                JOIN workspace_memberships AS m ON m.user_id = u.id
                WHERE u.firebase_uid = %s AND u.disabled = FALSE AND m.workspace_id = %s
                """,
                (firebase_uid, workspace_id),
            )
            membership = cursor.fetchone()
        if not membership:
            return _api_response(event, 403, {"detail": "You do not have access to this workspace"})
        if membership[0] not in {"workspace_admin", "analyst"}:
            return _api_response(event, 403, {"detail": "Your workspace role does not allow uploads"})

        upload_id = str(uuid.uuid4())
        object_key = f"incoming/{workspace_id}/{firebase_uid}/{upload_id}/{file_name}"
        upload = _s3.generate_presigned_post(
            Bucket=os.environ["UPLOAD_BUCKET"],
            Key=object_key,
            Fields={"Content-Type": "text/csv"},
            Conditions=[
                {"Content-Type": "text/csv"},
                ["content-length-range", 1, MAX_UPLOAD_BYTES],
            ],
            ExpiresIn=600,
        )
        return _api_response(event, 200, {
            "upload_id": upload_id,
            "object_key": object_key,
            "upload_url": upload["url"],
            "upload_fields": upload["fields"],
            "upload_method": "POST",
            "content_type": "text/csv",
            "expires_in_seconds": 600,
        })
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return _api_response(event, 400, {"detail": "Invalid upload request"})
    except Exception as exc:
        logger.error("Could not issue an S3 upload URL: %s", type(exc).__name__)
        return _api_response(event, 503, {"detail": "Upload service is temporarily unavailable"})


def _record_event(cursor: Any, workspace_id: str, batch_id: str, stage: str, status: str,
                  message: str, actor_user_id: int, details: dict[str, Any] | None = None) -> None:
    cursor.execute(
        """
        INSERT INTO dashboard_events (
            workspace_id, batch_id, stage, status, message, details, actor_user_id, occurred_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (workspace_id, batch_id, stage, status, message[:500], Json(details or {}), actor_user_id),
    )


def _write_applicant_chunk(cursor: Any, batch_id: str, accepted: list[dict[str, Any]]) -> None:
    if not accepted:
        return
    values = [(
        batch_id,
        item["external_user_id"],
        item["name"],
        item["email"],
        item["monthly_income"],
        item["credit_score"],
        item["employment_status"],
        item["employment_segment"],
        item["age"],
        "INR",
        "IN",
    ) for item in accepted]
    execute_values(cursor, INSERT_APPLICANTS_SQL, values, page_size=1000)


def _stage_error_batch(cursor: Any, workspace_id: str, batch_id: str, file_name: str,
                       digest: str, actor_user_id: int, message: str) -> None:
    errors = [{"row": 1, "message": message}]
    cursor.execute(
        """
        INSERT INTO upload_batches (
            id, workspace_id, file_name, content_sha256, status, rows_total,
            rows_imported, rows_rejected, errors, created_at
        ) VALUES (%s, %s, %s, %s, 'completed_with_errors', 0, 0, 1, %s, NOW())
        ON CONFLICT (id) DO UPDATE SET
            status = EXCLUDED.status,
            rows_total = EXCLUDED.rows_total,
            rows_imported = EXCLUDED.rows_imported,
            rows_rejected = EXCLUDED.rows_rejected,
            errors = EXCLUDED.errors
        """,
        (batch_id, workspace_id, file_name[:255], digest, Json(errors)),
    )
    _record_event(cursor, workspace_id, batch_id, "upload", "completed_with_errors",
                  "CSV was received but could not be imported", actor_user_id,
                  {"rows_total": 0, "rows_imported": 0, "rows_rejected": 1})


def _insert_upload(connection: Any, workspace_id: str, firebase_uid: str, upload_id: str,
                   file_name: str, digest: str, local_path: str) -> tuple[str, int, bool]:
    """Atomically validate, persist the batch and applicant chunks, and record row errors."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.id
            FROM app_users AS u
            JOIN workspace_memberships AS m ON m.user_id = u.id
            WHERE u.firebase_uid = %s AND u.disabled = FALSE AND m.workspace_id = %s
            """,
            (firebase_uid, workspace_id),
        )
        user = cursor.fetchone()
        if not user:
            raise ValueError("The uploaded object does not belong to an active workspace member")
        actor_user_id = int(user[0])

        cursor.execute(
            "SELECT id, status, rows_imported FROM upload_batches WHERE workspace_id = %s AND content_sha256 = %s",
            (workspace_id, digest),
        )
        existing = cursor.fetchone()
        if existing:
            connection.commit()
            return str(existing[0]), int(existing[2]), True

        cursor.execute("SELECT content_sha256 FROM upload_batches WHERE id = %s", (upload_id,))
        reused_id = cursor.fetchone()
        if reused_id:
            if reused_id[0] != digest:
                raise ValueError("An upload id cannot be reused for different file contents")
            cursor.execute("SELECT rows_imported FROM upload_batches WHERE id = %s", (upload_id,))
            imported = int(cursor.fetchone()[0])
            connection.commit()
            return upload_id, imported, True

        cursor.execute(
            """
            INSERT INTO upload_batches (
                id, workspace_id, file_name, content_sha256, status, rows_total,
                rows_imported, rows_rejected, errors, created_at
            ) VALUES (%s, %s, %s, %s, 'processing', 0, 0, 0, '[]'::jsonb, NOW())
            ON CONFLICT (workspace_id, content_sha256) DO NOTHING
            RETURNING id
            """,
            (upload_id, workspace_id, file_name[:255], digest),
        )
        created = cursor.fetchone()
        if not created:
            cursor.execute(
                "SELECT id, rows_imported FROM upload_batches WHERE workspace_id = %s AND content_sha256 = %s",
                (workspace_id, digest),
            )
            existing = cursor.fetchone()
            connection.commit()
            return str(existing[0]), int(existing[1]), True

        try:
            with open(local_path, "r", encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source)
                if not reader.fieldnames:
                    _stage_error_batch(cursor, workspace_id, upload_id, file_name, digest, actor_user_id,
                                       "CSV must include a header row")
                    connection.commit()
                    return upload_id, 0, False
                reader.fieldnames = [header.strip() if header else "" for header in reader.fieldnames]
                missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames))
                if missing:
                    _stage_error_batch(cursor, workspace_id, upload_id, file_name, digest, actor_user_id,
                                       "Missing required columns: " + ", ".join(missing))
                    connection.commit()
                    return upload_id, 0, False

                errors: list[dict[str, Any]] = []
                accepted: list[dict[str, Any]] = []
                seen: set[str] = set()
                rows_total = 0
                rows_rejected = 0
                rows_imported = 0
                for row_number, row in enumerate(reader, start=2):
                    if not any((value or "").strip() for value in row.values() if value is not None):
                        continue
                    rows_total += 1
                    normalized = {str(key).strip(): value for key, value in row.items() if key is not None}
                    item, error = validate_row(normalized, row_number)
                    if item and item["external_user_id"] in seen:
                        item = None
                        error = f"Row {row_number}: duplicate user_id in this CSV"
                    if error:
                        rows_rejected += 1
                        if len(errors) < 200:
                            errors.append({"row": row_number, "message": error})
                    elif item:
                        seen.add(item["external_user_id"])
                        accepted.append(item)
                        if len(accepted) == 1000:
                            _write_applicant_chunk(cursor, upload_id, accepted)
                            rows_imported += len(accepted)
                            accepted.clear()
                if accepted:
                    _write_applicant_chunk(cursor, upload_id, accepted)
                    rows_imported += len(accepted)

                if rows_total == 0:
                    rows_rejected = 1
                    errors.append({"row": 1, "message": "The CSV has a header but no user rows"})
                status = "completed_with_errors" if errors else "completed"
                cursor.execute(
                    """
                    UPDATE upload_batches
                    SET status = %s, rows_total = %s, rows_imported = %s,
                        rows_rejected = %s, errors = %s
                    WHERE id = %s
                    """,
                    (status, rows_total, rows_imported, rows_rejected, Json(errors), upload_id),
                )
                _record_event(cursor, workspace_id, upload_id, "upload", status,
                              "S3 CSV was uploaded, validated, and committed", actor_user_id,
                              {"rows_total": rows_total, "rows_imported": rows_imported,
                               "rows_rejected": rows_rejected})
            connection.commit()
            return upload_id, rows_imported, False
        except UnicodeDecodeError:
            connection.rollback()
            with connection.cursor() as error_cursor:
                _stage_error_batch(error_cursor, workspace_id, upload_id, file_name, digest, actor_user_id,
                                   "CSV must be UTF-8 encoded")
            connection.commit()
            return upload_id, 0, False
        except Exception:
            connection.rollback()
            raise


def _trigger_matching_if_needed(batch_id: str) -> None:
    with _managed_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM match_jobs WHERE batch_id = %s AND status IN ('queued', 'running', 'completed') LIMIT 1",
            (batch_id,),
        )
        already_dispatched = cursor.fetchone() is not None
    if already_dispatched:
        return
    webhook = os.environ["N8N_MATCH_WEBHOOK"]
    token = _secrets.get_secret_value(SecretId=os.environ["N8N_SERVICE_TOKEN_SECRET_ARN"])["SecretString"]
    request = Request(
        webhook,
        data=json.dumps({"batch_id": batch_id}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Workflow-Token": token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Workflow webhook returned HTTP {response.status}")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("The upload was saved, but n8n did not accept the matching webhook") from exc


def _parse_object_key(event: dict[str, Any]) -> tuple[str, str, str, str, str]:
    detail = event.get("detail") or {}
    key = detail.get("object", {}).get("key")
    if not key and event.get("Records"):
        key = event["Records"][0].get("s3", {}).get("object", {}).get("key")
    decoded = unquote_plus(str(key or ""))
    parts = decoded.split("/", 4)
    if len(parts) != 5 or parts[0] != "incoming" or not all(parts[1:4]):
        raise ValueError("Unexpected upload object key")
    workspace_id, firebase_uid, upload_id, file_name = parts[1:]
    uuid.UUID(upload_id)
    safe_name = _safe_filename(file_name)
    if not safe_name:
        raise ValueError("Uploaded object must have a .csv file name")
    bucket = (detail.get("bucket") or {}).get("name")
    if not bucket and event.get("Records"):
        bucket = event["Records"][0].get("s3", {}).get("bucket", {}).get("name")
    if bucket != os.environ["UPLOAD_BUCKET"]:
        raise ValueError("Unexpected upload bucket")
    return bucket, workspace_id, firebase_uid, upload_id, safe_name


def ingest_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Consume S3 Object Created events, persist valid rows, then notify n8n."""
    bucket, workspace_id, firebase_uid, upload_id, file_name = _parse_object_key(event)
    key = unquote_plus(str((event.get("detail") or {}).get("object", {}).get("key") or ""))
    if not key and event.get("Records"):
        key = unquote_plus(str(event["Records"][0]["s3"]["object"]["key"]))
    response = _s3.get_object(Bucket=bucket, Key=key)
    content_length = int(response.get("ContentLength", 0))
    if not 0 < content_length <= MAX_UPLOAD_BYTES:
        response["Body"].close()
        _s3.delete_object(Bucket=bucket, Key=key)
        logger.info("Rejected S3 upload upload_id=%s bytes=%s", upload_id, content_length)
        return {"upload_id": upload_id, "rejected": True, "reason": "size_limit"}

    digest = hashlib.sha256()
    with tempfile.NamedTemporaryFile(prefix="loan-upload-", suffix=".csv", dir="/tmp") as staged:
        body = response["Body"]
        try:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                staged.write(chunk)
        finally:
            body.close()
        staged.flush()
        content_sha256 = digest.hexdigest()
        with _managed_connection() as connection:
            batch_id, rows_imported, duplicate = _insert_upload(
                connection, workspace_id, firebase_uid, upload_id, file_name, content_sha256, staged.name
            )

    if rows_imported:
        _trigger_matching_if_needed(batch_id)
    logger.info(
        "Processed S3 upload batch=%s rows_imported=%s duplicate=%s",
        batch_id,
        rows_imported,
        duplicate,
    )
    return {"batch_id": batch_id, "rows_imported": rows_imported, "duplicate_upload": duplicate}
