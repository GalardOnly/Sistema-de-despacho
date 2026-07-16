SET search_path TO despacho, public;

DO $validar_entregadores_ativos$
BEGIN
    IF EXISTS (
        SELECT entregador_id
        FROM pedidos
        WHERE entregador_id IS NOT NULL
          AND status IN (
              'aguardando_entregador',
              'em_rota_retirada',
              'em_rota',
              'despachado',
              'coletado'
          )
        GROUP BY entregador_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Existem entregadores vinculados a mais de um pedido ativo. Corrija os dados antes da migration.';
    END IF;
END
$validar_entregadores_ativos$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pedidos_entregador_ativo
    ON pedidos(entregador_id)
    WHERE entregador_id IS NOT NULL
      AND status IN (
          'aguardando_entregador',
          'em_rota_retirada',
          'em_rota',
          'despachado',
          'coletado'
      );

INSERT INTO schema_migrations(version)
VALUES ('003_despacho_atomico')
ON CONFLICT (version) DO NOTHING;
