from alembic import op


revision = "005_operadores_normalizados"
down_revision = "004_revogacao_sessao"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE despacho.operadores_solicitante
        ADD COLUMN IF NOT EXISTS nome_normalizado TEXT
        """
    )
    op.execute(
        """
        UPDATE despacho.operadores_solicitante
        SET nome_normalizado = LOWER(
            REGEXP_REPLACE(BTRIM(nome), '[[:space:]]+', ' ', 'g')
        )
        WHERE nome_normalizado IS NULL
        """
    )
    op.execute(
        """
        WITH duplicados AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY unidade_id, nome_normalizado
                       ORDER BY id
                   ) AS posicao
            FROM despacho.operadores_solicitante
            WHERE ativo=1
        )
        UPDATE despacho.operadores_solicitante AS operador
        SET ativo=0
        FROM duplicados
        WHERE operador.id=duplicados.id AND duplicados.posicao>1
        """
    )
    op.execute(
        """
        ALTER TABLE despacho.operadores_solicitante
        ALTER COLUMN nome_normalizado SET NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_operadores_unidade_nome_ativo
        ON despacho.operadores_solicitante(unidade_id, nome_normalizado)
        WHERE ativo=1
        """
    )


def downgrade():
    op.execute(
        "DROP INDEX IF EXISTS despacho.ux_operadores_unidade_nome_ativo"
    )
    op.execute(
        """
        ALTER TABLE despacho.operadores_solicitante
        DROP COLUMN IF EXISTS nome_normalizado
        """
    )
