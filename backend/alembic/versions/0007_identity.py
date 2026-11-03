"""Keep official SSH identities unique, including hostname case variants."""

import sqlalchemy as sa

from alembic import context, op

revision = "0007_identity"
down_revision = "0006_job_history"
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        duplicate = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT 1 FROM official_accounts GROUP BY lower(ssh_host), ssh_port, ssh_username "
                    "HAVING count(*) > 1 LIMIT 1"
                )
            )
            .first()
        )
        if duplicate:
            raise RuntimeError(
                "存在重复的官方 SSH 连接身份；请先核对并处理重复账号，再执行数据库迁移"
            )
    # In --sql mode the generated UNIQUE INDEX itself rejects duplicates on execution.
    op.create_index(
        "uq_official_ssh_identity",
        "official_accounts",
        [sa.text("lower(ssh_host)"), "ssh_port", "ssh_username"],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_official_ssh_identity", table_name="official_accounts")
