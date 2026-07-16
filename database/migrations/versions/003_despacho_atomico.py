from alembic import op


revision = "003_despacho_atomico"
down_revision = "002_urgencia_mista"
branch_labels = None
depends_on = None


ACTIVE_STATUSES = (
    "aguardando_entregador",
    "em_rota_retirada",
    "em_rota",
    "despachado",
    "coletado",
)


def upgrade():
    statuses = ", ".join(f"'{status}'" for status in ACTIVE_STATUSES)
    op.execute(
        f"""
        DO $validar_entregadores_ativos$
        BEGIN
            IF EXISTS (
                SELECT entregador_id
                FROM despacho.pedidos
                WHERE entregador_id IS NOT NULL
                  AND status IN ({statuses})
                GROUP BY entregador_id
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Existem entregadores vinculados a mais de um pedido ativo. Corrija os dados antes da migration.';
            END IF;
        END
        $validar_entregadores_ativos$
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pedidos_entregador_ativo
            ON despacho.pedidos(entregador_id)
            WHERE entregador_id IS NOT NULL AND status IN ({statuses})
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS despacho.uq_pedidos_entregador_ativo")
