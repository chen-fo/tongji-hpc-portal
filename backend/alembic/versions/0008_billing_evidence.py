"""Preserve immutable evidence for new job charges."""

import sqlalchemy as sa

from alembic import op

revision = "0008_billing_evidence"
down_revision = "0007_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "billing_entry_id",
            sa.Integer(),
            sa.ForeignKey("billing_entries.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("evidence_json", sa.Text(), nullable=False),
    )
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER immutable_billing_evidence_{action.lower()} BEFORE {action} ON billing_evidence "
            "BEGIN SELECT RAISE(ABORT, 'immutable billing evidence'); END"
        )


def downgrade():
    op.drop_table("billing_evidence")
