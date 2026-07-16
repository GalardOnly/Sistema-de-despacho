from alembic import op


revision = "004_revogacao_sessao"
down_revision = "003_despacho_atomico"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE despacho.usuarios
        ADD COLUMN IF NOT EXISTS sessao_versao BIGINT NOT NULL DEFAULT 1
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE despacho.usuarios
        DROP COLUMN IF EXISTS sessao_versao
        """
    )
