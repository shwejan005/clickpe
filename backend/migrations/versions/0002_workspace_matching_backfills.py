"""Upgrade legacy local tables and add workspace and matching indexes.

Revision ID: 0002_workspace_matching
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_workspace_matching"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO workspaces (id, name, created_at) VALUES (:id, :name, NOW()) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": "local-default", "name": "ClickPe Local Workspace"})
    bind.execute(sa.text("ALTER TABLE upload_batches ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(36)"))
    bind.execute(sa.text(
        "UPDATE upload_batches SET workspace_id = 'local-default' WHERE workspace_id IS NULL"
    ))
    bind.execute(sa.text("ALTER TABLE upload_batches ALTER COLUMN workspace_id SET NOT NULL"))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_upload_batches_workspace_id ON upload_batches (workspace_id)"
    ))
    bind.execute(sa.text("""
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
    bind.execute(sa.text("""
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'upload_batches'::regclass AND contype = 'f'
              AND confrelid = 'workspaces'::regclass
          ) THEN
            ALTER TABLE upload_batches ADD CONSTRAINT fk_upload_batches_workspace_id
              FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE;
          END IF;
        END $$;
    """))
    bind.execute(sa.text(
        "ALTER TABLE applicants ADD COLUMN IF NOT EXISTS employment_segment VARCHAR(40)"
    ))
    bind.execute(sa.text("""
        UPDATE applicants SET employment_segment = CASE
          WHEN lower(employment_status) LIKE '%self%' OR lower(employment_status) LIKE '%business%' THEN 'self-employed'
          WHEN lower(employment_status) LIKE '%unemploy%' OR lower(employment_status) LIKE '%student%'
            OR lower(employment_status) LIKE '%retired%' THEN lower(replace(trim(employment_status), '_', '-'))
          WHEN lower(employment_status) LIKE '%salary%' OR lower(employment_status) LIKE '%salaried%'
            OR lower(employment_status) LIKE '%employ%' THEN 'salaried'
          ELSE lower(replace(trim(employment_status), '_', '-')) END
        WHERE employment_segment IS NULL
    """))
    bind.execute(sa.text("ALTER TABLE applicants ALTER COLUMN employment_segment SET NOT NULL"))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_applicants_employment_segment ON applicants (employment_segment)"
    ))
    bind.execute(sa.text(
        "ALTER TABLE loan_matches ADD COLUMN IF NOT EXISTS match_status VARCHAR(32) NOT NULL DEFAULT 'potential'"
    ))
    bind.execute(sa.text(
        "ALTER TABLE loan_products ADD COLUMN IF NOT EXISTS criteria_version INTEGER NOT NULL DEFAULT 1"
    ))
    bind.execute(sa.text(
        "ALTER TABLE loan_matches ADD COLUMN IF NOT EXISTS criteria_version INTEGER NOT NULL DEFAULT 1"
    ))
    bind.execute(sa.text(
        "ALTER TABLE loan_matches ADD COLUMN IF NOT EXISTS notification_claim_id VARCHAR(36)"
    ))
    bind.execute(sa.text(
        "ALTER TABLE loan_matches ADD COLUMN IF NOT EXISTS notification_claimed_at TIMESTAMPTZ"
    ))
    bind.execute(sa.text("""
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
    bind.execute(sa.text(
        "ALTER TABLE match_jobs ADD COLUMN IF NOT EXISTS progress_pairs INTEGER NOT NULL DEFAULT 0"
    ))
    bind.execute(sa.text(
        "ALTER TABLE match_jobs ADD COLUMN IF NOT EXISTS created_matches INTEGER NOT NULL DEFAULT 0"
    ))
    bind.execute(sa.text(
        "ALTER TABLE match_jobs ADD COLUMN IF NOT EXISTS needs_information_pairs INTEGER NOT NULL DEFAULT 0"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_applicants_batch_segment "
        "ON applicants (batch_id, employment_segment)"
    ))
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_loan_matches_pending_notifications "
        "ON loan_matches (batch_id, notification_status, applicant_id)"
    ))


def downgrade() -> None:
    # Backfills and newly stored values are retained to avoid data loss.
    pass
