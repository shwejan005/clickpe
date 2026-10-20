from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from fastapi import BackgroundTasks, Body, Cookie, Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, create_engine, func, select, text, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from .discovery import SOURCES, discover_product
from . import firebase_auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("loan-engine")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://loan_engine:local-dev-password@localhost:5432/loan_engine")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
N8N_MATCH_WEBHOOK = os.getenv("N8N_MATCH_WEBHOOK", "http://localhost:5678/webhook/loan-match")
N8N_NOTIFICATION_WEBHOOK = os.getenv("N8N_NOTIFICATION_WEBHOOK", "http://localhost:5678/webhook/loan-notify")
NOTIFICATION_TEST_RECIPIENT = os.getenv("NOTIFICATION_TEST_RECIPIENT", "").strip()
MAIL_FROM = os.getenv("MAIL_FROM", "loan-engine@localhost")
N8N_SERVICE_TOKEN = os.getenv("N8N_SERVICE_TOKEN", "").strip()
FIREBASE_BOOTSTRAP_ADMIN_EMAIL = os.getenv("FIREBASE_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
DEFAULT_WORKSPACE_NAME = os.getenv("DEFAULT_WORKSPACE_NAME", "ClickPe Local Workspace").strip()
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MATCH_INSERT_CHUNK_SIZE = 1000
NOTIFICATION_PAGE_SIZE = max(1, min(int(os.getenv("NOTIFICATION_PAGE_SIZE", "10")), 25))
NOTIFICATION_MAX_PER_BATCH = max(int(os.getenv("NOTIFICATION_MAX_PER_BATCH", "25")), NOTIFICATION_PAGE_SIZE)
NOTIFICATION_CLAIM_TTL = timedelta(minutes=15)
LEGACY_WORKSPACE_ID = "local-default"
CSRF_COOKIE_NAME = "clickpe_csrf"
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
REQUIRED_COLUMNS = {
    "user_id", "name", "email", "monthly_income", "credit_score", "employment_status", "age"
}

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    disabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_role: Mapped[str] = mapped_column(String(32), nullable=False, default="analyst")
    platform_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class WorkspaceInvite(Base):
    __tablename__ = "workspace_invites"
    __table_args__ = (UniqueConstraint("workspace_id", "email", name="uq_workspace_invite_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    workspace_role: Mapped[str] = mapped_column(String(32), nullable=False, default="analyst")
    invited_by: Mapped[int] = mapped_column(ForeignKey("app_users.id"), nullable=False)
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey("app_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class DashboardEvent(Base):
    __tablename__ = "dashboard_events"
    __table_args__ = (Index("ix_dashboard_events_workspace_batch_time", "workspace_id", "batch_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_users.id"), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)


class UploadBatch(Base):
    __tablename__ = "upload_batches"
    __table_args__ = (UniqueConstraint("workspace_id", "content_sha256", name="uq_upload_workspace_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class Applicant(Base):
    __tablename__ = "applicants"
    __table_args__ = (UniqueConstraint("batch_id", "external_user_id", name="uq_applicant_batch_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    external_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    monthly_income: Mapped[float] = mapped_column(Float, nullable=False)
    credit_score: Mapped[int] = mapped_column(Integer, nullable=False)
    employment_status: Mapped[str] = mapped_column(String(80), nullable=False)
    employment_segment: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")


class LoanProduct(Base):
    __tablename__ = "loan_products"
    __table_args__ = (UniqueConstraint("lender", "product_name", name="uq_product_lender_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lender: Mapped[str] = mapped_column(String(120), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    product_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    rate_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    eligibility_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    rate_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    criteria_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="needs_review")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class Match(Base):
    __tablename__ = "loan_matches"
    __table_args__ = (UniqueConstraint("applicant_id", "product_id", "criteria_version", name="uq_match_applicant_product_criteria"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("applicants.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("loan_products.id", ondelete="CASCADE"), nullable=False)
    criteria_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    batch_id: Mapped[str] = mapped_column(ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    match_status: Mapped[str] = mapped_column(String(32), nullable=False, default="potential")
    notification_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    notification_claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    notification_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class MatchJob(Base):
    __tablename__ = "match_jobs"
    __table_args__ = (UniqueConstraint("batch_id", "criteria_fingerprint", name="uq_match_job_batch_criteria"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    batch_id: Mapped[str] = mapped_column(ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    criteria_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_pairs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_matches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_information_pairs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class AuthContext:
    def __init__(self, user: AppUser, membership: WorkspaceMembership):
        self.user = user
        self.membership = membership
        self.workspace_id = membership.workspace_id
        self.workspace_role = membership.workspace_role
        self.platform_role = membership.platform_role


def record_event(
    db: Session,
    workspace_id: str,
    stage: str,
    status: str,
    message: str,
    batch_id: str | None = None,
    job_id: str | None = None,
    details: dict[str, Any] | None = None,
    actor_user_id: int | None = None,
) -> None:
    db.add(DashboardEvent(
        workspace_id=workspace_id,
        batch_id=batch_id,
        job_id=job_id,
        stage=stage,
        status=status,
        message=message[:500],
        details=details or {},
        actor_user_id=actor_user_id,
    ))


def check_csrf(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin", "")
    allowed_origins = {origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")}
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    header_token = request.headers.get("x-csrf-token", "")
    if origin not in allowed_origins or not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed; refresh the page and retry")


def require_workflow_token(x_workflow_token: str | None = Header(default=None)) -> None:
    if not N8N_SERVICE_TOKEN or not x_workflow_token or not hmac.compare_digest(x_workflow_token, N8N_SERVICE_TOKEN):
        raise HTTPException(status_code=401, detail="Workflow service authentication failed")


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    clickpe_session: str | None = Cookie(default=None, alias=firebase_auth.SESSION_COOKIE_NAME),
    x_workspace_id: str | None = Header(default=None),
    x_workflow_token: str | None = Header(default=None),
) -> AuthContext:
    # Internal n8n calls use a separate high-entropy service credential. This
    # identity is never persisted or exposed to browser sessions.
    if N8N_SERVICE_TOKEN and x_workflow_token and hmac.compare_digest(x_workflow_token, N8N_SERVICE_TOKEN):
        service_user = AppUser(id=0, firebase_uid="n8n-service", email="n8n-service@localhost", display_name="Workflow service")
        service_membership = WorkspaceMembership(
            workspace_id=x_workspace_id or LEGACY_WORKSPACE_ID, user_id=0,
            workspace_role="workspace_admin", platform_role="platform_admin",
        )
        return AuthContext(service_user, service_membership)
    check_csrf(request)
    if not clickpe_session:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    try:
        decoded = firebase_auth.verify_session_cookie(clickpe_session)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.info("Firebase session rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="Your session expired; sign in again") from exc

    uid = str(decoded.get("uid", ""))
    email = str(decoded.get("email", "")).strip().lower()
    email_verified = bool(decoded.get("email_verified", False))
    if not uid or not email or not email_verified:
        raise HTTPException(status_code=403, detail="A verified email account is required")
    user = db.scalar(select(AppUser).where(AppUser.firebase_uid == uid))
    if user is None:
        workspace: Workspace | None = None
        workspace_role = "analyst"
        platform_role = None
        if FIREBASE_BOOTSTRAP_ADMIN_EMAIL and email == FIREBASE_BOOTSTRAP_ADMIN_EMAIL:
            workspace = db.get(Workspace, LEGACY_WORKSPACE_ID)
            workspace_role = "workspace_admin"
            platform_role = "platform_admin"
        else:
            invite = db.scalar(select(WorkspaceInvite).where(
                func.lower(WorkspaceInvite.email) == email,
                WorkspaceInvite.accepted_by.is_(None),
            ).order_by(WorkspaceInvite.created_at.desc()))
            if invite:
                workspace = db.get(Workspace, invite.workspace_id)
                workspace_role = invite.workspace_role
        if workspace is None:
            raise HTTPException(status_code=403, detail="Your Firebase account is valid but has not been invited to a workspace")
        user = AppUser(
            firebase_uid=uid,
            email=email,
            display_name=decoded.get("name"),
            email_verified=True,
            last_login_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            workspace_role=workspace_role,
            platform_role=platform_role,
        )
        db.add(membership)
        if workspace_role != "workspace_admin":
            invite.accepted_by = user.id
        db.commit()
        db.refresh(user)
    else:
        if user.disabled:
            raise HTTPException(status_code=403, detail="This dashboard account is disabled")
        user.email = email
        user.display_name = decoded.get("name") or user.display_name
        user.email_verified = True
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()

    memberships = db.scalars(select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id).order_by(WorkspaceMembership.created_at)).all()
    if not memberships:
        raise HTTPException(status_code=403, detail="No workspace access is assigned to this account")
    membership = next((m for m in memberships if m.workspace_id == x_workspace_id), None) if x_workspace_id else memberships[0]
    if membership is None:
        raise HTTPException(status_code=403, detail="You do not have access to the selected workspace")
    return AuthContext(user, membership)


def require_analyst(context: AuthContext) -> None:
    if context.workspace_role not in {"workspace_admin", "analyst"}:
        raise HTTPException(status_code=403, detail="Your workspace role does not allow this action")


def require_workspace_admin(context: AuthContext) -> None:
    if context.workspace_role != "workspace_admin":
        raise HTTPException(status_code=403, detail="Workspace administrator access is required")


def require_platform_reviewer(context: AuthContext) -> None:
    if context.platform_role not in {"criteria_reviewer", "platform_admin"}:
        raise HTTPException(status_code=403, detail="Criteria review access is not assigned to this account")


def trigger_matching(batch_id: str) -> None:
    try:
        response = requests.post(N8N_MATCH_WEBHOOK, json={"batch_id": batch_id},
                                 headers={"X-Workflow-Token": N8N_SERVICE_TOKEN}, timeout=5)
        response.raise_for_status()
        logger.info("Triggered n8n matching for batch %s", batch_id)
    except requests.RequestException as exc:
        logger.warning("Could not trigger n8n for batch %s: %s", batch_id, exc)


def validate_row(row: dict[str, str | None], row_number: int) -> tuple[dict[str, Any] | None, str | None]:
    try:
        user_id = (row.get("user_id") or "").strip()
        name = (row.get("name") or "").strip()
        email = (row.get("email") or "").strip()
        employment_status = (row.get("employment_status") or "").strip()
        if not all((user_id, name, email, employment_status)):
            raise ValueError("user_id, name, email, and employment_status are required")
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            raise ValueError("email is not valid")
        monthly_income = float(row.get("monthly_income", ""))
        credit_score = int(row.get("credit_score", ""))
        age = int(row.get("age", ""))
        if monthly_income <= 0:
            raise ValueError("monthly_income must be greater than zero")
        if not 0 <= credit_score <= 1000:
            raise ValueError("credit_score must be between 0 and 1000")
        if not 18 <= age <= 100:
            raise ValueError("age must be between 18 and 100")
        return ({
            "external_user_id": user_id,
            "name": name,
            "email": email,
            "monthly_income": monthly_income,
            "credit_score": credit_score,
            "employment_status": employment_status,
            "employment_segment": _normalized_segment(employment_status),
            "age": age,
        }, None)
    except (TypeError, ValueError) as exc:
        return None, f"Row {row_number}: {exc}"


app = FastAPI(title="Loan Eligibility Engine API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def initialize_database() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    # create_all does not update existing local volumes; add/backfill these fields safely.
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO workspaces (id, name, created_at) VALUES (:id, :name, :created_at) "
            "ON CONFLICT (id) DO NOTHING"
        ), {"id": LEGACY_WORKSPACE_ID, "name": DEFAULT_WORKSPACE_NAME, "created_at": datetime.now(timezone.utc)})
        connection.execute(text(
            "ALTER TABLE upload_batches ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(36)"
        ))
        connection.execute(text(
            "UPDATE upload_batches SET workspace_id = :workspace_id WHERE workspace_id IS NULL"
        ), {"workspace_id": LEGACY_WORKSPACE_ID})
        connection.execute(text("ALTER TABLE upload_batches ALTER COLUMN workspace_id SET NOT NULL"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_upload_batches_workspace_id ON upload_batches (workspace_id)"))
        connection.execute(text("""
            DO $$ DECLARE c RECORD; BEGIN
              FOR c IN SELECT conname FROM pg_constraint
                WHERE conrelid = 'upload_batches'::regclass AND contype = 'u'
                  AND pg_get_constraintdef(oid) = 'UNIQUE (content_sha256)'
              LOOP EXECUTE format('ALTER TABLE upload_batches DROP CONSTRAINT %I', c.conname); END LOOP;
              IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_upload_workspace_hash') THEN
                ALTER TABLE upload_batches ADD CONSTRAINT uq_upload_workspace_hash UNIQUE (workspace_id, content_sha256);
              END IF;
            END $$;
        """))
        connection.execute(text("""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_upload_batches_workspace_id') THEN
                ALTER TABLE upload_batches ADD CONSTRAINT fk_upload_batches_workspace_id
                  FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE;
              END IF;
            END $$;
        """))
        connection.execute(text(
            "ALTER TABLE applicants ADD COLUMN IF NOT EXISTS employment_segment VARCHAR(40)"
        ))
        connection.execute(text(
            "UPDATE applicants SET employment_segment = CASE "
            "WHEN lower(employment_status) LIKE '%self%' "
            "OR lower(employment_status) LIKE '%business%' THEN 'self-employed' "
            "WHEN lower(employment_status) LIKE '%unemploy%' "
            "OR lower(employment_status) LIKE '%student%' "
            "OR lower(employment_status) LIKE '%retired%' THEN lower(replace(trim(employment_status), '_', '-')) "
            "WHEN lower(employment_status) LIKE '%salary%' "
            "OR lower(employment_status) LIKE '%salaried%' "
            "OR lower(employment_status) LIKE '%employ%' THEN 'salaried' "
            "ELSE lower(replace(trim(employment_status), '_', '-')) END "
            "WHERE employment_segment IS NULL"
        ))
        connection.execute(text(
            "ALTER TABLE loan_matches ADD COLUMN IF NOT EXISTS match_status VARCHAR(32) NOT NULL DEFAULT 'potential'"
        ))
        connection.execute(text(
            "ALTER TABLE loan_products ADD COLUMN IF NOT EXISTS criteria_version INTEGER NOT NULL DEFAULT 1"
        ))
        connection.execute(text(
            "ALTER TABLE loan_matches ADD COLUMN IF NOT EXISTS criteria_version INTEGER NOT NULL DEFAULT 1"
        ))
        connection.execute(text(
            "ALTER TABLE loan_matches ADD COLUMN IF NOT EXISTS notification_claim_id VARCHAR(36)"
        ))
        connection.execute(text(
            "ALTER TABLE loan_matches ADD COLUMN IF NOT EXISTS notification_claimed_at TIMESTAMPTZ"
        ))
        connection.execute(text("""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_match_applicant_product') THEN
                ALTER TABLE loan_matches DROP CONSTRAINT uq_match_applicant_product;
              END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_match_applicant_product_criteria') THEN
                ALTER TABLE loan_matches ADD CONSTRAINT uq_match_applicant_product_criteria
                  UNIQUE (applicant_id, product_id, criteria_version);
              END IF;
            END $$;
        """))
        connection.execute(text("ALTER TABLE match_jobs ADD COLUMN IF NOT EXISTS progress_pairs INTEGER NOT NULL DEFAULT 0"))
        connection.execute(text("ALTER TABLE match_jobs ADD COLUMN IF NOT EXISTS created_matches INTEGER NOT NULL DEFAULT 0"))
        connection.execute(text("ALTER TABLE match_jobs ADD COLUMN IF NOT EXISTS needs_information_pairs INTEGER NOT NULL DEFAULT 0"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_applicants_batch_segment "
            "ON applicants (batch_id, employment_segment)"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_loan_matches_pending_notifications "
            "ON loan_matches (batch_id, notification_status, applicant_id)"
        ))


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(select(func.count()).select_from(UploadBatch))
    return {
        "status": "ok",
        "service": "loan-engine-api",
        "firebase_project_configured": "true" if firebase_auth.FIREBASE_PROJECT_ID else "false",
        "firebase_admin_ready": "true" if firebase_auth.firebase_auth_ready() else "false",
    }


@app.get("/api/operations/health")
def operations_health(context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    """Authenticated, lightweight dependency checks for the local operations dashboard."""
    statuses: dict[str, str] = {"database_status": "ok"}
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        statuses["database_status"] = "unavailable"
    statuses["firebase_status"] = "ok" if firebase_auth.firebase_auth_ready() else "not_configured"
    for name, url in (
        ("n8n_status", "http://n8n:5678/healthz"),
        ("mailpit_status", "http://mailpit:8025/api/v1/info"),
    ):
        try:
            response = requests.get(url, timeout=2)
            statuses[name] = "ok" if response.ok else "unavailable"
        except requests.RequestException:
            statuses[name] = "unavailable"
    return statuses


@app.get("/api/auth/csrf")
def auth_csrf(response: Response) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        httponly=False,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return {"csrf_token": token}


@app.post("/api/auth/session")
def auth_session(
    request: Request,
    response: Response,
    payload: dict[str, Any] = Body(...),
    x_csrf_token: str | None = Header(default=None),
) -> dict[str, str]:
    check_csrf(request)
    id_token = str(payload.get("id_token", ""))
    if not id_token:
        raise HTTPException(status_code=400, detail="Firebase ID token is required")
    try:
        decoded = firebase_auth.verify_client_id_token(id_token)
        if not decoded.get("email_verified"):
            raise HTTPException(status_code=403, detail="Verify your Firebase account email before signing in")
        session_cookie = firebase_auth.create_session_cookie(id_token)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.info("Firebase sign-in rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="Firebase sign-in could not be verified") from exc
    response.set_cookie(
        firebase_auth.SESSION_COOKIE_NAME,
        session_cookie,
        max_age=int(firebase_auth.SESSION_DURATION.total_seconds()),
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return {"status": "signed_in"}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, str]:
    check_csrf(request)
    response.delete_cookie(firebase_auth.SESSION_COOKIE_NAME, path="/", secure=SESSION_COOKIE_SECURE, httponly=True, samesite="lax")
    return {"status": "signed_out"}


@app.get("/api/me")
def current_profile(context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    workspace = db.get(Workspace, context.workspace_id)
    memberships = db.execute(select(WorkspaceMembership, Workspace).join(
        Workspace, Workspace.id == WorkspaceMembership.workspace_id
    ).where(WorkspaceMembership.user_id == context.user.id)).all()
    return {
        "user": {"id": context.user.id, "email": context.user.email, "display_name": context.user.display_name},
        "workspace": {"id": workspace.id, "name": workspace.name} if workspace else None,
        "workspaces": [{"id": membership.workspace_id, "name": linked_workspace.name, "role": membership.workspace_role}
                       for membership, linked_workspace in memberships],
        "workspace_role": context.workspace_role,
        "platform_role": context.platform_role,
        "capabilities": {
            "can_upload": context.workspace_role in {"workspace_admin", "analyst"},
            "can_manage_workspace": context.workspace_role == "workspace_admin",
            "can_review_criteria": context.platform_role in {"criteria_reviewer", "platform_admin"},
        },
    }


@app.get("/api/workspaces/members")
def workspace_members(context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_workspace_admin(context)
    members = db.execute(select(AppUser, WorkspaceMembership).join(
        WorkspaceMembership, WorkspaceMembership.user_id == AppUser.id
    ).where(WorkspaceMembership.workspace_id == context.workspace_id).order_by(AppUser.email)).all()
    invites = db.scalars(select(WorkspaceInvite).where(
        WorkspaceInvite.workspace_id == context.workspace_id, WorkspaceInvite.accepted_by.is_(None)
    ).order_by(WorkspaceInvite.created_at.desc())).all()
    return {
        "members": [{"user_id": user.id, "email": user.email, "display_name": user.display_name,
                     "role": membership.workspace_role, "platform_role": membership.platform_role,
                     "created_at": membership.created_at.isoformat()}
                    for user, membership in members],
        "invites": [{"email": invite.email, "role": invite.workspace_role, "created_at": invite.created_at.isoformat()}
                    for invite in invites],
    }


@app.post("/api/workspaces/invites")
def create_workspace_invite(
    payload: dict[str, Any] = Body(...),
    context: AuthContext = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_workspace_admin(context)
    email = str(payload.get("email", "")).strip().lower()
    role = str(payload.get("role", "analyst"))
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if role not in {"analyst", "viewer"}:
        raise HTTPException(status_code=400, detail="Invited members can be analysts or viewers")
    existing_user = db.scalar(select(AppUser).where(func.lower(AppUser.email) == email))
    if existing_user and db.scalar(select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == context.workspace_id, WorkspaceMembership.user_id == existing_user.id
    )):
        raise HTTPException(status_code=409, detail="This user already belongs to the workspace")
    invite = db.scalar(select(WorkspaceInvite).where(
        WorkspaceInvite.workspace_id == context.workspace_id, func.lower(WorkspaceInvite.email) == email
    ))
    if invite:
        invite.workspace_role = role
        invite.invited_by = context.user.id
        invite.accepted_by = None
    else:
        invite = WorkspaceInvite(workspace_id=context.workspace_id, email=email, workspace_role=role,
                                 invited_by=context.user.id)
        db.add(invite)
    record_event(db, context.workspace_id, "workspace_access", "invited", "Workspace invitation created",
                 details={"role": role}, actor_user_id=context.user.id)
    db.commit()
    return {"status": "invited", "email": email, "role": role,
            "note": "The invitee can sign in with this verified Firebase email to join the workspace."}


@app.post("/api/platform/reviewers/{user_id}")
def set_criteria_reviewer(
    user_id: int,
    payload: dict[str, Any] = Body(...),
    context: AuthContext = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if context.user.id == 0 or context.platform_role != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform administrator access is required")
    member = db.scalar(select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == context.workspace_id,
        WorkspaceMembership.user_id == user_id,
    ))
    if member is None:
        raise HTTPException(status_code=404, detail="User is not a member of this workspace")
    enabled = bool(payload.get("enabled"))
    memberships = db.scalars(select(WorkspaceMembership).where(WorkspaceMembership.user_id == user_id)).all()
    for membership in memberships:
        if membership.platform_role != "platform_admin":
            membership.platform_role = "criteria_reviewer" if enabled else None
    target = db.get(AppUser, user_id)
    record_event(db, context.workspace_id, "platform_access", "reviewer_enabled" if enabled else "reviewer_disabled",
                 "Criteria reviewer access updated", details={"user_id": user_id, "email": target.email if target else None},
                 actor_user_id=context.user.id)
    db.commit()
    return {"user_id": user_id, "criteria_reviewer": any(m.platform_role in {"criteria_reviewer", "platform_admin"} for m in memberships)}


@app.post("/api/uploads")
async def upload_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    context: AuthContext = Depends(get_current_user),
) -> dict[str, Any]:
    require_analyst(context)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Choose a .csv file")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The CSV file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The local upload limit is 25 MB")
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(select(UploadBatch).where(
        UploadBatch.content_sha256 == digest,
        UploadBatch.workspace_id == context.workspace_id,
    ))
    if existing:
        return batch_payload(existing, duplicate=True)
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded") from exc
    reader = csv.DictReader(io.StringIO(decoded))
    headers = {header.strip() for header in (reader.fieldnames or []) if header}
    missing = sorted(REQUIRED_COLUMNS - headers)
    if missing:
        raise HTTPException(status_code=400, detail="Missing required columns: " + ", ".join(missing))

    batch_id = str(uuid.uuid4())
    errors: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    rows_total = 0
    for row_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values() if value is not None):
            continue
        rows_total += 1
        normalized = {str(k).strip(): v for k, v in row.items() if k is not None}
        item, error = validate_row(normalized, row_number)
        if error:
            errors.append({"row": row_number, "message": error})
        elif item:
            if item["external_user_id"] in seen:
                errors.append({"row": row_number, "message": f"Row {row_number}: duplicate user_id in this CSV"})
            else:
                seen.add(item["external_user_id"])
                accepted.append(item)
    if rows_total == 0:
        raise HTTPException(status_code=400, detail="The CSV has a header but no user rows")

    stored_path = UPLOAD_DIR / f"{batch_id}.csv"
    stored_path.write_bytes(content)
    batch = UploadBatch(
        id=batch_id,
        workspace_id=context.workspace_id,
        file_name=Path(file.filename).name[:255],
        content_sha256=digest,
        status="completed_with_errors" if errors else "completed",
        rows_total=rows_total,
        rows_imported=len(accepted),
        rows_rejected=len(errors),
        errors=errors[:200],
    )
    db.add(batch)
    db.flush()
    record_event(db, context.workspace_id, "upload", "completed", "CSV uploaded and validated", batch_id=batch_id,
                 details={"rows_total": rows_total, "rows_imported": len(accepted), "rows_rejected": len(errors)},
                 actor_user_id=context.user.id)
    for item in accepted:
        db.add(Applicant(batch_id=batch_id, **item))
    db.commit()
    if accepted:
        background_tasks.add_task(trigger_matching, batch_id)
    return batch_payload(batch, duplicate=False)


def batch_payload(batch: UploadBatch, duplicate: bool) -> dict[str, Any]:
    return {
        "batch_id": batch.id,
        "file_name": batch.file_name,
        "status": batch.status,
        "rows_total": batch.rows_total,
        "rows_imported": batch.rows_imported,
        "rows_rejected": batch.rows_rejected,
        "errors": batch.errors,
        "duplicate_upload": duplicate,
        "created_at": batch.created_at.isoformat(),
    }


def _run_summary(batch: UploadBatch, db: Session) -> dict[str, Any]:
    match_counts = {status: count for status, count in db.execute(
        select(Match.match_status, func.count()).where(Match.batch_id == batch.id).group_by(Match.match_status)
    ).all()}
    notification_counts = {status: count for status, count in db.execute(
        select(Match.notification_status, func.count()).where(Match.batch_id == batch.id).group_by(Match.notification_status)
    ).all()}
    jobs = db.scalars(select(MatchJob).where(MatchJob.batch_id == batch.id).order_by(MatchJob.created_at.desc()).limit(1)).all()
    job = jobs[0] if jobs else None
    return {
        **batch_payload(batch, duplicate=False),
        "match_counts": match_counts,
        "notification_counts": notification_counts,
        "match_job": {
            "job_id": job.id,
            "status": job.status,
            "attempt": job.attempt,
            "progress_pairs": job.progress_pairs,
            "created_matches": job.created_matches,
            "needs_information_pairs": job.needs_information_pairs,
            "error": job.error,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        } if job else None,
    }


@app.get("/api/dashboard/summary")
def dashboard_summary(context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    workspace_batches = select(UploadBatch.id).where(UploadBatch.workspace_id == context.workspace_id)
    batches_total = db.scalar(select(func.count()).select_from(UploadBatch).where(UploadBatch.workspace_id == context.workspace_id)) or 0
    applicants_total = db.scalar(select(func.coalesce(func.sum(UploadBatch.rows_imported), 0)).where(UploadBatch.workspace_id == context.workspace_id)) or 0
    match_total = db.scalar(select(func.count()).select_from(Match).where(Match.batch_id.in_(workspace_batches))) or 0
    needs_info_total = db.scalar(select(func.count()).select_from(Match).where(
        Match.batch_id.in_(workspace_batches), Match.match_status == "needs_information"
    )) or 0
    pending_notifications = db.scalar(select(func.count()).select_from(Match).where(
        Match.batch_id.in_(workspace_batches), Match.notification_status.in_(["pending", "sending"])
    )) or 0
    active_jobs = db.scalars(select(MatchJob).join(UploadBatch, UploadBatch.id == MatchJob.batch_id).where(
        UploadBatch.workspace_id == context.workspace_id, MatchJob.status.in_(["queued", "running"])
    ).order_by(MatchJob.created_at.desc()).limit(5)).all()
    recent_batches = db.scalars(select(UploadBatch).where(
        UploadBatch.workspace_id == context.workspace_id
    ).order_by(UploadBatch.created_at.desc()).limit(6)).all()
    pending_review = 0
    if context.platform_role in {"criteria_reviewer", "platform_admin"}:
        pending_review = db.scalar(select(func.count()).select_from(LoanProduct).where(
            LoanProduct.provenance["llm_proposal"]["status"].astext == "pending_human_review"
        )) or 0
    stale_products = db.scalar(select(func.count()).select_from(LoanProduct).where(
        (LoanProduct.status != "verified") | (LoanProduct.last_checked_at < datetime.now(timezone.utc) - timedelta(hours=48))
    )) or 0
    return {
        "workspace": {"id": context.workspace_id, "role": context.workspace_role},
        "metrics": {
            "runs": batches_total,
            "applicants_imported": int(applicants_total),
            "potential_matches": match_total,
            "needs_information": needs_info_total,
            "pending_notifications": pending_notifications,
            "criteria_awaiting_review": pending_review,
            "stale_or_unverified_products": stale_products,
        },
        "active_jobs": [{"batch_id": job.batch_id, "job_id": job.id, "status": job.status,
                         "attempt": job.attempt, "progress_pairs": job.progress_pairs,
                         "created_at": job.created_at.isoformat()} for job in active_jobs],
        "recent_runs": [_run_summary(batch, db) for batch in recent_batches],
    }


@app.get("/api/runs")
def list_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    query: str | None = Query(default=None, max_length=200),
    context: AuthContext = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filters = [UploadBatch.workspace_id == context.workspace_id]
    if status:
        filters.append(UploadBatch.status == status)
    if query:
        filters.append(UploadBatch.file_name.ilike(f"%{query}%"))
    total = db.scalar(select(func.count()).select_from(UploadBatch).where(*filters)) or 0
    batches = db.scalars(select(UploadBatch).where(*filters).order_by(
        UploadBatch.created_at.desc(), UploadBatch.id.desc()
    ).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_run_summary(batch, db) for batch in batches], "page": page,
            "page_size": page_size, "total": total, "pages": (total + page_size - 1) // page_size}


@app.get("/api/runs/{batch_id}")
def run_detail(batch_id: str, context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    batch = db.scalar(select(UploadBatch).where(
        UploadBatch.id == batch_id, UploadBatch.workspace_id == context.workspace_id
    ))
    if batch is None:
        raise HTTPException(status_code=404, detail="Run not found")
    jobs = db.scalars(select(MatchJob).where(MatchJob.batch_id == batch_id).order_by(MatchJob.created_at.desc())).all()
    events = db.scalars(select(DashboardEvent).where(
        DashboardEvent.workspace_id == context.workspace_id, DashboardEvent.batch_id == batch_id
    ).order_by(DashboardEvent.occurred_at)).all()
    match_rows = db.execute(select(
        LoanProduct.id, LoanProduct.lender, LoanProduct.product_name, LoanProduct.rate_min,
        LoanProduct.rate_max, LoanProduct.rate_label, LoanProduct.rate_url,
        LoanProduct.eligibility_url, LoanProduct.status, Match.criteria_version,
        Match.match_status, func.count(Match.id),
    ).join(Match, Match.product_id == LoanProduct.id).where(Match.batch_id == batch_id).group_by(
        LoanProduct.id, Match.criteria_version, Match.match_status
    ).order_by(LoanProduct.lender)).all()
    return {
        **_run_summary(batch, db),
        "jobs": [{"job_id": job.id, "status": job.status, "attempt": job.attempt,
                  "progress_pairs": job.progress_pairs, "created_matches": job.created_matches,
                  "needs_information_pairs": job.needs_information_pairs, "criteria_fingerprint": job.criteria_fingerprint,
                  "error": job.error, "created_at": job.created_at.isoformat(),
                  "started_at": job.started_at.isoformat() if job.started_at else None,
                  "finished_at": job.finished_at.isoformat() if job.finished_at else None} for job in jobs],
        "products": [{"product_id": row[0], "lender": row[1], "product_name": row[2], "rate_min": row[3],
                      "rate_max": row[4], "rate_label": row[5], "rate_url": row[6],
                      "eligibility_url": row[7], "product_status": row[8], "criteria_version": row[9],
                      "match_status": row[10], "match_count": row[11]} for row in match_rows],
        "events": [{"id": event.id, "stage": event.stage, "status": event.status, "message": event.message,
                    "details": event.details, "occurred_at": event.occurred_at.isoformat(), "job_id": event.job_id}
                   for event in events],
        "row_errors": batch.errors,
    }


@app.get("/api/uploads")
def list_uploads(context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    batches = db.scalars(select(UploadBatch).where(UploadBatch.workspace_id == context.workspace_id).order_by(UploadBatch.created_at.desc()).limit(20)).all()
    return [batch_payload(batch, duplicate=False) for batch in batches]


@app.get("/api/uploads/{batch_id}")
def get_upload(batch_id: str, context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    batch = db.scalar(select(UploadBatch).where(UploadBatch.id == batch_id, UploadBatch.workspace_id == context.workspace_id))
    if batch is None:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    return batch_payload(batch, duplicate=False)


@app.post("/api/discovery/run")
def run_discovery(context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if context.platform_role != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform administrator access is required to start lender discovery")
    outcomes: list[dict[str, Any]] = []
    for source in SOURCES:
        existing = db.scalar(
            select(LoanProduct).where(
                LoanProduct.lender == source["lender"],
                LoanProduct.product_name == source["product_name"],
            )
        )
        cached_proposal = (existing.provenance or {}).get("llm_proposal", {}) if existing else {}
        result = discover_product(source, cached_proposal.get("source_sha256"))
        if existing is None:
            existing = LoanProduct(
                lender=result["lender"],
                product_name=result["product_name"],
                product_url=result["product_url"],
                rate_url=result["rate_url"],
                eligibility_url=result["eligibility_url"],
                rate_min=result["rate_min"],
                rate_max=result["rate_max"],
                rate_label=result["rate_label"],
                criteria=result["criteria"],
                provenance=result["provenance"],
                status=result["status"],
                last_checked_at=result["last_checked_at"] if result["status"] == "verified" else None,
            )
            db.add(existing)
        else:
            if result["status"] == "verified":
                if (existing.criteria or {}) != (result["criteria"] or {}):
                    existing.criteria_version += 1
                existing.provenance = result["provenance"]
                existing.product_url = result["product_url"]
                existing.rate_url = result["rate_url"]
                existing.eligibility_url = result["eligibility_url"]
                existing.rate_min = result["rate_min"]
                existing.rate_max = result["rate_max"]
                existing.rate_label = result["rate_label"]
                existing.criteria = result["criteria"]
                existing.status = "verified"
                existing.last_checked_at = result["last_checked_at"]
                existing.updated_at = datetime.now(timezone.utc)
            else:
                # Preserve the last successful evidence while recording this failed re-check.
                previous_provenance = existing.provenance or {}
                failed_provenance = {
                    **previous_provenance,
                    "last_failed_check": result["provenance"],
                }
                if result["provenance"].get("llm_proposal"):
                    failed_provenance["llm_proposal"] = result["provenance"]["llm_proposal"]
                existing.provenance = failed_provenance
                existing.status = "stale"
        db.flush()
        outcomes.append({
            "product_id": existing.id,
            "lender": result["lender"],
            "product_name": result["product_name"],
            "status": result["status"],
            "rate_min": result["rate_min"],
            "rate_max": result["rate_max"],
            "failures": result["provenance"]["failures"],
            "llm_review_required": bool(
                (result["provenance"].get("llm_proposal") or {}).get("status") == "pending_human_review"
            ),
        })
    db.commit()
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "products": outcomes}


@app.get("/api/products/review-queue")
def product_criteria_review_queue(context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_platform_reviewer(context)
    products = db.scalars(select(LoanProduct).order_by(LoanProduct.updated_at.desc())).all()
    items = []
    for product in products:
        provenance = product.provenance or {}
        proposal = provenance.get("llm_proposal")
        if proposal and proposal.get("status") == "pending_human_review":
            proposal_digest = hashlib.sha256(
                json.dumps(proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            items.append({
                "product_id": product.id,
                "lender": product.lender,
                "product_name": product.product_name,
                "product_status": product.status,
                "proposal_sha256": proposal_digest,
                "proposal": proposal,
            })
    return {"count": len(items), "items": items}


@app.post("/api/products/{product_id}/criteria-review")
def review_product_criteria(
    product_id: int,
    payload: dict[str, Any] = Body(...),
    context: AuthContext = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_platform_reviewer(context)
    decision = payload.get("decision")
    proposal_sha256 = str(payload.get("proposal_sha256", ""))
    if decision not in {"approve", "reject"} or not re.fullmatch(r"[a-f0-9]{64}", proposal_sha256):
        raise HTTPException(status_code=400, detail="Provide decision=approve|reject and the displayed proposal_sha256")
    product = db.get(LoanProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Loan product not found")
    provenance = product.provenance or {}
    proposal = provenance.get("llm_proposal")
    if not proposal or proposal.get("status") != "pending_human_review":
        raise HTTPException(status_code=409, detail="There is no pending criteria proposal for this product")
    current_digest = hashlib.sha256(
        json.dumps(proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if current_digest != proposal_sha256:
        raise HTTPException(status_code=409, detail="The proposal changed; reload the review queue before deciding")
    if decision == "reject":
        proposal["status"] = "rejected_by_human"
        proposal["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        provenance["llm_proposal"] = proposal
        product.provenance = provenance
        record_event(db, LEGACY_WORKSPACE_ID, "criteria_review", "rejected", f"{product.lender} criteria proposal rejected",
                     details={"product_id": product.id, "proposal_sha256": proposal_sha256}, actor_user_id=context.user.id)
        db.commit()
        return {"product_id": product.id, "decision": "reject", "product_status": product.status}

    failed_check = provenance.get("last_failed_check", provenance)
    sources = failed_check.get("sources", {})
    eligibility_source = sources.get("eligibility_url", {})
    rate_source = sources.get("rate_url", {})
    expected_source_hash = eligibility_source.get("sha256")
    proposal_source = proposal.get("source_sha256")
    if not expected_source_hash or expected_source_hash != proposal_source:
        raise HTTPException(status_code=409, detail="The official lender page changed; run discovery and review the new proposal")
    if product.rate_min is None or product.rate_max is None or not rate_source.get("sha256"):
        raise HTTPException(status_code=409, detail="A current, validated rate range is required before criteria can be approved")
    checked_at = max(
        datetime.fromisoformat(eligibility_source["fetched_at"]),
        datetime.fromisoformat(rate_source["fetched_at"]),
    )
    if checked_at < datetime.now(timezone.utc) - timedelta(hours=48):
        raise HTTPException(status_code=409, detail="Source evidence is stale; run discovery before approving")

    approved_criteria = payload.get("criteria", proposal.get("criteria"))
    if not isinstance(approved_criteria, dict) or not approved_criteria.get("segments"):
        raise HTTPException(status_code=409, detail="The proposal has no validated eligibility segments")
    allowed_fields = {
        "minimum_monthly_income_inr", "min_age", "max_age", "min_credit_score", "max_credit_score",
        "credit_score_preferred", "salary_account_required", "total_work_experience_years", "current_organisation_years",
    }
    if set(approved_criteria) - {"currency", "country_code", "segments"} or not isinstance(approved_criteria["segments"], dict):
        raise HTTPException(status_code=422, detail="Criteria payload has an unsupported structure")
    for segment_name, rules in approved_criteria["segments"].items():
        if segment_name not in {"salaried", "self-employed"} or not isinstance(rules, dict) or set(rules) - allowed_fields:
            raise HTTPException(status_code=422, detail="Criteria contain an unsupported segment or field")
        for field, value in rules.items():
            if field == "salary_account_required":
                valid = isinstance(value, bool)
            elif isinstance(value, int) and not isinstance(value, bool):
                bounds = {
                    "minimum_monthly_income_inr": (1, 100_000_000),
                    "min_age": (18, 100), "max_age": (18, 100),
                    "min_credit_score": (0, 1000), "max_credit_score": (0, 1000), "credit_score_preferred": (0, 1000),
                    "total_work_experience_years": (0, 60), "current_organisation_years": (0, 60),
                }
                low, high = bounds[field]
                valid = low <= value <= high
            else:
                valid = False
            if not valid:
                raise HTTPException(status_code=422, detail=f"Invalid value for criteria field: {field}")
        if rules.get("min_age") is not None and rules.get("max_age") is not None and rules["min_age"] > rules["max_age"]:
            raise HTTPException(status_code=422, detail=f"Minimum age cannot exceed maximum age for {segment_name}")
    if (product.criteria or {}) != approved_criteria:
        product.criteria_version += 1
    product.criteria = approved_criteria
    provenance["evidence"] = {
        **(provenance.get("evidence") or {}),
        "llm_criteria": proposal.get("evidence", []),
    }
    proposal["status"] = "approved_by_human"
    proposal["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    proposal["reviewed_criteria"] = approved_criteria
    provenance["llm_proposal"] = proposal
    provenance["criteria_approval"] = {
        "decision": "approved",
        "reviewed_at": proposal["reviewed_at"],
        "source_sha256": proposal_source,
        "manual_override": approved_criteria != proposal.get("criteria"),
        "reviewer_user_id": context.user.id,
        "reviewer_firebase_uid": context.user.firebase_uid,
    }
    product.provenance = provenance
    product.status = "verified"
    product.last_checked_at = checked_at
    product.updated_at = datetime.now(timezone.utc)
    record_event(db, LEGACY_WORKSPACE_ID, "criteria_review", "approved", f"{product.lender} criteria proposal approved",
                 details={"product_id": product.id, "proposal_sha256": proposal_sha256,
                          "criteria_version": product.criteria_version}, actor_user_id=context.user.id)
    db.commit()
    return {"product_id": product.id, "decision": "approve", "product_status": product.status}


@app.get("/api/products")
def list_products(context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    products = db.scalars(select(LoanProduct).order_by(LoanProduct.lender)).all()
    now = datetime.now(timezone.utc)
    return [{
        "id": product.id,
        "lender": product.lender,
        "product_name": product.product_name,
        "product_url": product.product_url,
        "rate_url": product.rate_url,
        "eligibility_url": product.eligibility_url,
        "rate_min": product.rate_min,
        "rate_max": product.rate_max,
        "rate_label": product.rate_label,
        "criteria": product.criteria,
        "criteria_version": product.criteria_version,
        "provenance": product.provenance,
        "status": product.status,
        "last_checked_at": product.last_checked_at.isoformat() if product.last_checked_at else None,
        "stale": product.status != "verified" or not product.last_checked_at or product.last_checked_at < now - timedelta(hours=48),
    } for product in products]


def _normalized_segment(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if "self" in normalized or "business" in normalized:
        return "self-employed"
    if any(term in normalized for term in ("unemploy", "student", "retired")):
        return normalized
    if "salary" in normalized or "salaried" in normalized or "employ" in normalized:
        return "salaried"
    return normalized


def _match_reasons(lender: str, rules: dict[str, Any]) -> tuple[list[str], str]:
    missing_checks: list[str] = []
    min_income = rules.get("minimum_monthly_income_inr")
    if rules.get("salary_account_required"):
        missing_checks.append("Confirm you have the required lender salary account")
        missing_checks.append("The lender does not publish a numeric credit-score cutoff; confirm its internal policy")
    income_thresholds = rules.get("income_thresholds_by_sector_inr")
    if income_thresholds:
        sector_thresholds = ", ".join(
            f"{label}: INR {amount:,} net monthly salary"
            for key, amount in income_thresholds.items()
            for label in [
                "Government/Defence" if "government" in key or "defence" in key
                else "Corporate" if "corporate" in key
                else key.replace("_", " ").title()
            ]
        )
        service_months = rules.get("minimum_service_months_by_sector", [])
        service_note = ""
        if service_months:
            service_note = (
                f" Minimum service is {service_months[0]} months for Government/Defence "
                f"and {service_months[-1]} months for Corporate applicants."
            )
        missing_checks.append(
            "Confirm your sector and applicable threshold (" + sector_thresholds + ")." + service_note +
            " The CSV does not identify sector or whether income is net salary."
        )
    if rules.get("total_work_experience_years"):
        if lender == "HDFC Bank":
            missing_checks.append(
                "Confirm an eligible employer, at least 2 years of total work experience, "
                "and at least 1 year with the current employer"
            )
        else:
            missing_checks.append("Confirm the lender's required work experience")
    reasons = ["Potential fit against available published criteria; this is not lender approval."]
    if min_income is not None:
        if lender == "HDFC Bank":
            reasons.append(
                f"Reported monthly income is at or above INR {int(min_income):,}; "
                "HDFC specifies net monthly income, which this file does not distinguish from gross income."
            )
        else:
            reasons.append(f"Monthly income meets the published minimum of INR {int(min_income):,}.")
    preferred_score = rules.get("credit_score_preferred")
    if preferred_score:
        reasons.append(f"A credit score of {preferred_score} or above is preferred, not a hard minimum.")
    reasons.extend(missing_checks)
    return reasons, "needs_information" if missing_checks else "potential"


@app.post("/api/workflows/match")
def run_matching(
    batch_id: str = Body(embed=True),
    db: Session = Depends(get_db),
    _service: None = Depends(require_workflow_token),
) -> dict[str, Any]:
    batch = db.get(UploadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    active_products = db.scalars(select(LoanProduct).where(
        LoanProduct.status == "verified", LoanProduct.last_checked_at >= cutoff
    ).order_by(LoanProduct.id)).all()
    product_count = len(active_products)
    criteria_fingerprint = hashlib.sha256(json.dumps([
        {"id": product.id, "version": product.criteria_version, "criteria": product.criteria}
        for product in active_products
    ], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:job_key))"), {
        "job_key": f"{batch_id}:{criteria_fingerprint}"
    })
    job = db.scalar(select(MatchJob).where(
        MatchJob.batch_id == batch_id,
        MatchJob.criteria_fingerprint == criteria_fingerprint,
    ).with_for_update())
    if job and job.status == "completed":
        return {**job.result, "job_id": job.id, "idempotent_replay": True}
    if job and job.status == "running" and job.started_at and job.started_at >= datetime.now(timezone.utc) - timedelta(minutes=30):
        raise HTTPException(status_code=409, detail="A matching job for this batch and criteria version is already running")
    if job is None:
        job = MatchJob(batch_id=batch_id, criteria_fingerprint=criteria_fingerprint)
        db.add(job)
        db.flush()
    job.status = "running"
    job.attempt += 1
    job.error = None
    job.started_at = datetime.now(timezone.utc)
    job.finished_at = None
    record_event(db, batch.workspace_id, "matching", "running", "Matching started", batch_id=batch_id,
                 job_id=job.id, details={"attempt": job.attempt})
    db.commit()

    applicant_count = db.scalar(select(func.count()).select_from(Applicant).where(Applicant.batch_id == batch_id)) or 0

    candidate_query = text("""
        SELECT a.id AS applicant_id, p.id AS product_id, p.criteria_version, p.lender,
               segment.rules AS segment_rules
        FROM applicants AS a
        JOIN loan_products AS p
          ON p.status = 'verified'
         AND p.last_checked_at >= :cutoff
         AND a.country_code = COALESCE(p.criteria->>'country_code', a.country_code)
         AND a.currency = COALESCE(p.criteria->>'currency', a.currency)
        CROSS JOIN LATERAL jsonb_each(
          CASE WHEN jsonb_typeof(p.criteria->'segments') = 'object'
               THEN p.criteria->'segments' ELSE '{}'::jsonb END
        )
          AS segment(segment_name, rules)
        WHERE a.batch_id = :batch_id
          AND a.employment_segment = CASE
                WHEN lower(replace(segment.segment_name, '_', '-')) LIKE '%self%' THEN 'self-employed'
                WHEN lower(replace(segment.segment_name, '_', '-')) LIKE '%salary%'
                  OR lower(replace(segment.segment_name, '_', '-')) LIKE '%salaried%'
                  OR lower(replace(segment.segment_name, '_', '-')) LIKE '%employ%' THEN 'salaried'
                ELSE lower(replace(segment.segment_name, '_', '-'))
              END
          AND a.monthly_income >= COALESCE(
                NULLIF(segment.rules->>'minimum_monthly_income_inr', '')::numeric,
                (SELECT MIN(threshold.value::numeric)
                 FROM jsonb_each_text(
                     CASE WHEN jsonb_typeof(segment.rules->'income_thresholds_by_sector_inr') = 'object'
                          THEN segment.rules->'income_thresholds_by_sector_inr' ELSE '{}'::jsonb END
                 ) AS threshold(key, value)),
                0
              )
          AND (NULLIF(segment.rules->>'min_age', '') IS NULL
               OR a.age >= (segment.rules->>'min_age')::integer)
          AND (NULLIF(segment.rules->>'max_age', '') IS NULL
               OR a.age <= (segment.rules->>'max_age')::integer)
          AND (NULLIF(segment.rules->>'min_credit_score', '') IS NULL
               OR a.credit_score >= (segment.rules->>'min_credit_score')::integer)
          AND (NULLIF(segment.rules->>'max_credit_score', '') IS NULL
               OR a.credit_score <= (segment.rules->>'max_credit_score')::integer)
          AND NOT EXISTS (
              SELECT 1 FROM loan_matches AS existing
              WHERE existing.applicant_id = a.id AND existing.product_id = p.id
                AND existing.criteria_version = p.criteria_version
          )
    """)
    rows: list[dict[str, Any]] = []
    try:
        with engine.connect().execution_options(stream_results=True) as candidate_connection:
            candidate_rows = candidate_connection.execute(
                candidate_query, {"batch_id": batch_id, "cutoff": cutoff}
            ).mappings()
            for candidate in candidate_rows:
                reasons, match_status = _match_reasons(
                    candidate["lender"],
                    candidate["segment_rules"] or {},
                )
                rows.append({
                    "applicant_id": candidate["applicant_id"],
                    "product_id": candidate["product_id"],
                    "criteria_version": candidate["criteria_version"],
                    "batch_id": batch_id,
                    "reasons": reasons,
                    "match_status": match_status,
                    "notification_status": "pending",
                })
                if len(rows) >= MATCH_INSERT_CHUNK_SIZE:
                    chunk_size = len(rows)
                    result = db.execute(
                        pg_insert(Match).values(rows).on_conflict_do_nothing(
                            index_elements=[Match.applicant_id, Match.product_id, Match.criteria_version]
                        )
                    )
                    created_in_chunk = result.rowcount or 0
                    job.progress_pairs += chunk_size
                    job.created_matches += created_in_chunk
                    job.needs_information_pairs += sum(row["match_status"] == "needs_information" for row in rows)
                    db.commit()
                    rows.clear()
        if rows:
            chunk_size = len(rows)
            result = db.execute(
                pg_insert(Match).values(rows).on_conflict_do_nothing(
                    index_elements=[Match.applicant_id, Match.product_id, Match.criteria_version]
                )
            )
            created_in_chunk = result.rowcount or 0
            job.progress_pairs += chunk_size
            job.created_matches += created_in_chunk
            job.needs_information_pairs += sum(row["match_status"] == "needs_information" for row in rows)
        db.commit()
    except Exception as exc:
        db.rollback()
        failed_job = db.get(MatchJob, job.id)
        if failed_job:
            failed_job.status = "failed"
            failed_job.error = str(exc)[:1000]
            failed_job.finished_at = datetime.now(timezone.utc)
            record_event(db, batch.workspace_id, "matching", "failed", "Matching failed", batch_id=batch_id,
                         job_id=job.id, details={"attempt": failed_job.attempt, "error": failed_job.error})
            db.commit()
        raise
    result_payload = {
        "job_id": job.id,
        "batch_id": batch_id,
        "status": "completed",
        "attempt": job.attempt,
        "criteria_fingerprint": criteria_fingerprint,
        "applicants_checked": applicant_count,
        "verified_products_checked": product_count,
        "candidate_pairs_evaluated": job.progress_pairs,
        "potential_matches_created": job.created_matches,
        "needs_information_pairs": job.needs_information_pairs,
        "note": "Potential matches are informational only; lenders make final eligibility and pricing decisions.",
    }
    job.status = "completed"
    job.result = result_payload
    job.finished_at = datetime.now(timezone.utc)
    record_event(db, batch.workspace_id, "matching", "completed", "Matching completed", batch_id=batch_id,
                 job_id=job.id, details={
                     "candidate_pairs_evaluated": job.progress_pairs,
                     "matches_created": job.created_matches,
                     "needs_information": job.needs_information_pairs,
                     "attempt": job.attempt,
                 })
    db.commit()
    return result_payload


@app.get("/api/workflows/match-jobs")
def list_match_jobs(batch_id: str, context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    batch = db.scalar(select(UploadBatch).where(UploadBatch.id == batch_id, UploadBatch.workspace_id == context.workspace_id))
    if batch is None:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    jobs = db.scalars(
        select(MatchJob).where(MatchJob.batch_id == batch_id).order_by(MatchJob.created_at.desc())
    ).all()
    return {
        "batch_id": batch_id,
        "jobs": [{
            "job_id": job.id,
            "status": job.status,
            "attempt": job.attempt,
            "progress_pairs": job.progress_pairs,
            "created_matches": job.created_matches,
            "needs_information_pairs": job.needs_information_pairs,
            "criteria_fingerprint": job.criteria_fingerprint,
            "result": job.result,
            "error": job.error,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        } for job in jobs],
    }


def _render_notification(applicant: Applicant, products: list[tuple[LoanProduct, Match]], claim_id: str, batch_id: str) -> dict[str, Any]:
    product_lines = []
    match_ids = []
    for product, match in products:
        rate = f"{product.rate_min:.2f}%-{product.rate_max:.2f}% p.a." if product.rate_min is not None and product.rate_max is not None else "See lender page for current rate"
        checked_at = product.last_checked_at.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y %I:%M %p IST")
        product_lines.append(
            f"- {product.lender} - {product.product_name}: {rate}\n"
            f"  Rate details: {product.rate_url}\n"
            f"  Eligibility details: {product.eligibility_url}\n"
            f"  Product page: {product.product_url}\n"
            f"  Source pages checked: {checked_at}"
        )
        if match.reasons:
            product_lines.append("  Notes: " + " ".join(reason.rstrip(".") + "." for reason in match.reasons))
        match_ids.append(match.id)
    return {
        "to": NOTIFICATION_TEST_RECIPIENT,
        "from": MAIL_FROM,
        "subject": "Your potential personal-loan matches (test delivery)",
        "text": f"Hello {applicant.name},\n\nThese are preliminary potential matches based only on the fields in your file. They are not confirmed eligibility results. Review each product's notes and verify missing details with the lender.\n\n" + "\n".join(product_lines) + "\n\nThis is not an approval or offer. Eligibility and rates are confirmed only by the lender. This message was routed to a controlled test inbox; your CSV email address was not used.",
        "batch_id": batch_id,
        "applicant_id": applicant.id,
        "claim_id": claim_id,
        "match_ids": match_ids,
    }


@app.post("/api/workflows/notification-messages")
def notification_messages(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _service: None = Depends(require_workflow_token),
) -> dict[str, Any]:
    batch_id = str(payload.get("batch_id", ""))
    batch = db.get(UploadBatch, batch_id) if batch_id else None
    if not batch:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    if not NOTIFICATION_TEST_RECIPIENT:
        return {"batch_id": batch_id, "messages": [], "has_more": False, "note": "Set NOTIFICATION_TEST_RECIPIENT to a controlled local test inbox before enabling demo notifications."}

    requested_size = int(payload.get("page_size", NOTIFICATION_PAGE_SIZE))
    if not 1 <= requested_size <= 25:
        raise HTTPException(status_code=400, detail="page_size must be between 1 and 25")
    now = datetime.now(timezone.utc)
    stale_claim_cutoff = now - NOTIFICATION_CLAIM_TTL
    delivered_count = db.scalar(
        select(func.count(func.distinct(Match.applicant_id))).where(
            Match.batch_id == batch_id,
            Match.notification_status == "sent_to_local_test_inbox",
        )
    ) or 0
    remaining_cap = max(0, NOTIFICATION_MAX_PER_BATCH - delivered_count)
    page_size = min(requested_size, NOTIFICATION_PAGE_SIZE, remaining_cap)

    eligible_applicants = (
        select(Applicant.id.label("applicant_id"))
        .join(Match, Match.applicant_id == Applicant.id)
        .join(LoanProduct, LoanProduct.id == Match.product_id)
        .where(
            Match.batch_id == batch_id,
            (Match.notification_status == "pending") | (
                (Match.notification_status == "sending") & (Match.notification_claimed_at < stale_claim_cutoff)
            ),
            LoanProduct.status == "verified",
            LoanProduct.last_checked_at >= now - timedelta(hours=48),
        )
        .group_by(Applicant.id)
        .order_by(Applicant.id)
        .limit(page_size)
        .subquery()
    )
    applicants = db.scalars(
        select(Applicant)
        .where(Applicant.id.in_(select(eligible_applicants.c.applicant_id)))
        .order_by(Applicant.id)
        .with_for_update(skip_locked=True)
    ).all() if page_size else []

    messages = []
    for applicant in applicants:
        claim_id = str(uuid.uuid4())
        matches = db.execute(
            select(LoanProduct, Match)
            .join(Match, Match.product_id == LoanProduct.id)
            .where(
                Match.applicant_id == applicant.id,
                Match.batch_id == batch_id,
                (Match.notification_status == "pending") | (
                    (Match.notification_status == "sending") & (Match.notification_claimed_at < stale_claim_cutoff)
                ),
                LoanProduct.status == "verified",
                LoanProduct.last_checked_at >= now - timedelta(hours=48),
            )
            .order_by(LoanProduct.lender)
            .with_for_update(of=Match, skip_locked=True)
        ).all()
        if not matches:
            continue
        for _, match in matches:
            match.notification_status = "sending"
            match.notification_claim_id = claim_id
            match.notification_claimed_at = now
        messages.append(_render_notification(applicant, matches, claim_id, batch_id))

    db.commit()
    pending_applicants = db.scalar(
        select(func.count(func.distinct(Applicant.id)))
        .select_from(Applicant)
        .join(Match, Match.applicant_id == Applicant.id)
        .join(LoanProduct, LoanProduct.id == Match.product_id)
        .where(
            Match.batch_id == batch_id,
            Match.notification_status == "pending",
            LoanProduct.status == "verified",
            LoanProduct.last_checked_at >= datetime.now(timezone.utc) - timedelta(hours=48),
        )
    ) or 0
    continue_after_page = bool(
        messages and pending_applicants and delivered_count + len(messages) < NOTIFICATION_MAX_PER_BATCH
    )
    if messages:
        record_event(db, batch.workspace_id, "notifications", "running", "Notification page claimed",
                     batch_id=batch_id, details={"claimed_recipients": len(messages), "page_size": page_size,
                                                 "pending_recipients": pending_applicants})
        db.commit()
    return {
        "batch_id": batch_id,
        "messages": messages,
        "page_size": page_size,
        "claimed": len(messages),
        "pending_applicants": pending_applicants,
        "delivered_count": delivered_count,
        "batch_cap": NOTIFICATION_MAX_PER_BATCH,
        "has_more": pending_applicants > 0,
        "continue_after_page": continue_after_page,
    }


@app.post("/api/workflows/notifications/complete")
def complete_notifications(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _service: None = Depends(require_workflow_token),
) -> dict[str, Any]:
    batch_id = str(payload.get("batch_id", ""))
    applicant_id = payload.get("applicant_id")
    claim_id = str(payload.get("claim_id", ""))
    delivered = bool(payload.get("delivered", True))
    if not batch_id or not applicant_id or not claim_id:
        raise HTTPException(status_code=400, detail="batch_id, applicant_id, and claim_id are required")
    batch = db.get(UploadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    matches = db.scalars(select(Match).where(
        Match.batch_id == batch_id,
        Match.applicant_id == int(applicant_id),
        Match.notification_status == "sending",
        Match.notification_claim_id == claim_id,
    ).with_for_update()).all()
    for match in matches:
        match.notification_status = "sent_to_local_test_inbox" if delivered else "pending"
        match.notification_claim_id = None
        match.notification_claimed_at = None
    if matches:
        record_event(db, batch.workspace_id, "notifications", "delivered" if delivered else "retry_pending",
                     "Test notification delivered" if delivered else "Test notification failed and returned to pending",
                     batch_id=batch_id, details={"match_rows_updated": len(matches)})
    db.commit()
    pending_count = db.scalar(select(func.count()).select_from(Match).where(
        Match.batch_id == batch_id,
        Match.notification_status == "pending",
    )) or 0
    return {
        "batch_id": batch_id,
        "applicant_id": int(applicant_id),
        "claim_id": claim_id,
        "updated": len(matches),
        "delivered": delivered,
        "pending_matches": pending_count,
        "is_last": bool(payload.get("is_last", False)),
        "continue_after_page": bool(payload.get("continue_after_page", False)),
    }


@app.get("/api/workflows/notifications/status")
def notification_status(batch_id: str, context: AuthContext = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    batch = db.scalar(select(UploadBatch).where(UploadBatch.id == batch_id, UploadBatch.workspace_id == context.workspace_id))
    if batch is None:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    rows = db.execute(
        select(Match.notification_status, func.count())
        .where(Match.batch_id == batch_id)
        .group_by(Match.notification_status)
    ).all()
    sent_recipients = db.scalar(select(func.count(func.distinct(Match.applicant_id))).where(
        Match.batch_id == batch_id,
        Match.notification_status == "sent_to_local_test_inbox",
    )) or 0
    return {
        "batch_id": batch_id,
        "counts": {status: count for status, count in rows},
        "sent_recipients": sent_recipients,
        "pending_cap": NOTIFICATION_MAX_PER_BATCH,
        "test_recipient_configured": bool(NOTIFICATION_TEST_RECIPIENT),
    }


@app.post("/api/workflows/notifications/start")
def start_notifications(
    batch_id: str = Body(embed=True),
    context: AuthContext = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    require_analyst(context)
    batch = db.scalar(select(UploadBatch).where(UploadBatch.id == batch_id, UploadBatch.workspace_id == context.workspace_id))
    if batch is None:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    if not NOTIFICATION_TEST_RECIPIENT:
        raise HTTPException(status_code=409, detail="Local test recipient is not configured")
    try:
        response = requests.post(
            N8N_NOTIFICATION_WEBHOOK,
            json={"batch_id": batch_id},
            headers={"X-Workflow-Token": N8N_SERVICE_TOKEN},
            timeout=5,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Could not start test notifications for batch %s: %s", batch_id, exc)
        raise HTTPException(status_code=503, detail="Could not start the local notification workflow") from exc
    record_event(db, batch.workspace_id, "notifications", "queued", "Local test notification workflow started",
                 batch_id=batch_id, actor_user_id=context.user.id)
    db.commit()
    return {
        "batch_id": batch_id,
        "status": "started",
        "recipient": "controlled local test inbox",
        "page_size": NOTIFICATION_PAGE_SIZE,
        "per_batch_cap": NOTIFICATION_MAX_PER_BATCH,
    }
