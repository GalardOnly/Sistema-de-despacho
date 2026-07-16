from alembic import op


revision = "002_urgencia_mista"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE despacho.pedidos
            ADD COLUMN IF NOT EXISTS urgencia_mista SMALLINT NOT NULL DEFAULT 0
            CHECK (urgencia_mista IN (0, 1))
        """
    )


def downgrade():
    op.execute("ALTER TABLE despacho.pedidos DROP COLUMN IF EXISTS urgencia_mista")
