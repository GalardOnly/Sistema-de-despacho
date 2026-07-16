from alembic import op


revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


TABLES = (
    """
    CREATE TABLE IF NOT EXISTS despacho.unidades (
        id BIGSERIAL PRIMARY KEY,
        nome TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS despacho.usuarios (
        id BIGSERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        username TEXT NOT NULL UNIQUE,
        senha_hash TEXT NOT NULL,
        papel TEXT NOT NULL CHECK (papel IN ('admin', 'solicitante', 'entregador')),
        unidade_id BIGINT REFERENCES despacho.unidades(id),
        ativo SMALLINT NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
        disponivel SMALLINT NOT NULL DEFAULT 1 CHECK (disponivel IN (0, 1)),
        codigo_ref TEXT,
        tipo_veiculo TEXT CHECK (tipo_veiculo IS NULL OR tipo_veiculo IN ('moto', 'carro')),
        indisponibilidade_justificativa TEXT,
        indisponibilidade_tipo TEXT CHECK (
            indisponibilidade_tipo IS NULL
            OR indisponibilidade_tipo IN ('clt_desconto', 'recusa_padrao')
        ),
        indisponibilidade_ts BIGINT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS despacho.operadores_solicitante (
        id BIGSERIAL PRIMARY KEY,
        unidade_id BIGINT NOT NULL REFERENCES despacho.unidades(id),
        nome TEXT NOT NULL,
        codigo TEXT NOT NULL,
        ativo SMALLINT NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
        criado_em BIGINT NOT NULL,
        UNIQUE (unidade_id, codigo)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS despacho.pedidos (
        id BIGSERIAL PRIMARY KEY,
        protocolo TEXT UNIQUE,
        origem_id BIGINT NOT NULL REFERENCES despacho.unidades(id),
        destino_id BIGINT NOT NULL REFERENCES despacho.unidades(id),
        tipo TEXT NOT NULL,
        urgencia TEXT NOT NULL CHECK (urgencia IN ('rotina', 'urgente', 'emergencia')),
        urgencia_mista SMALLINT NOT NULL DEFAULT 0 CHECK (urgencia_mista IN (0, 1)),
        tipo_veiculo TEXT NOT NULL CHECK (tipo_veiculo IN ('moto', 'carro')),
        sla_limite_min INTEGER NOT NULL CHECK (sla_limite_min > 0),
        status TEXT NOT NULL DEFAULT 'solicitado',
        entregador_id BIGINT REFERENCES despacho.usuarios(id),
        criado_por BIGINT REFERENCES despacho.usuarios(id),
        operador_id BIGINT REFERENCES despacho.operadores_solicitante(id),
        ts_solicitado BIGINT NOT NULL,
        ts_aceito_admin BIGINT,
        ts_despachado BIGINT,
        ts_aceito_entregador BIGINT,
        ts_coletado BIGINT,
        ts_entregue BIGINT,
        ts_cancelado BIGINT,
        motivo_cancelamento TEXT,
        justificativa_atraso TEXT,
        CHECK (origem_id <> destino_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS despacho.tipos_coleta_unidade (
        id BIGSERIAL PRIMARY KEY,
        unidade_id BIGINT NOT NULL REFERENCES despacho.unidades(id),
        nome TEXT NOT NULL,
        nome_normalizado TEXT NOT NULL,
        ativo SMALLINT NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
        criado_em BIGINT NOT NULL,
        UNIQUE (unidade_id, nome_normalizado)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS despacho.localizacoes_pedido (
        id BIGSERIAL PRIMARY KEY,
        pedido_id BIGINT NOT NULL REFERENCES despacho.pedidos(id) ON DELETE CASCADE,
        entregador_id BIGINT NOT NULL REFERENCES despacho.usuarios(id),
        latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
        longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
        precisao DOUBLE PRECISION CHECK (precisao IS NULL OR precisao >= 0),
        ts BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS despacho.indisponibilidades_entregador (
        id BIGSERIAL PRIMARY KEY,
        entregador_id BIGINT NOT NULL REFERENCES despacho.usuarios(id),
        tipo TEXT NOT NULL CHECK (tipo IN ('clt_desconto', 'recusa_padrao')),
        justificativa TEXT NOT NULL,
        ts BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS despacho.chat_mensagens (
        id BIGSERIAL PRIMARY KEY,
        solicitante_id BIGINT NOT NULL REFERENCES despacho.usuarios(id),
        unidade_id BIGINT NOT NULL REFERENCES despacho.unidades(id),
        remetente_id BIGINT NOT NULL REFERENCES despacho.usuarios(id),
        remetente_papel TEXT NOT NULL CHECK (remetente_papel IN ('admin', 'solicitante')),
        texto TEXT NOT NULL,
        ts BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS despacho.notificacoes (
        id BIGSERIAL PRIMARY KEY,
        papel_destino TEXT NOT NULL CHECK (
            papel_destino IN ('admin', 'solicitante', 'entregador')
        ),
        usuario_id BIGINT REFERENCES despacho.usuarios(id),
        unidade_id BIGINT REFERENCES despacho.unidades(id),
        pedido_id BIGINT REFERENCES despacho.pedidos(id) ON DELETE CASCADE,
        tipo TEXT NOT NULL,
        titulo TEXT NOT NULL,
        mensagem TEXT NOT NULL,
        lida SMALLINT NOT NULL DEFAULT 0 CHECK (lida IN (0, 1)),
        criado_em BIGINT NOT NULL,
        lida_em BIGINT
    )
    """,
)


INDEXES = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_usuarios_codigo_ref_ativo
        ON despacho.usuarios(codigo_ref)
        WHERE codigo_ref IS NOT NULL AND papel <> 'admin' AND ativo = 1
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pedidos_fila
        ON despacho.pedidos(status, urgencia, ts_solicitado)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pedidos_origem
        ON despacho.pedidos(origem_id, id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pedidos_entregador
        ON despacho.pedidos(entregador_id, status, id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_localizacoes_pedido_ts
        ON despacho.localizacoes_pedido(pedido_id, ts)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_operadores_solicitante_unidade
        ON despacho.operadores_solicitante(unidade_id, ativo, nome)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tipos_coleta_unidade
        ON despacho.tipos_coleta_unidade(unidade_id, ativo, nome)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_solicitante_ts
        ON despacho.chat_mensagens(solicitante_id, ts)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_unidade_ts
        ON despacho.chat_mensagens(unidade_id, ts)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_notificacoes_destino
        ON despacho.notificacoes(papel_destino, usuario_id, unidade_id, lida, criado_em)
    """,
)


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS despacho")
    op.execute("REVOKE ALL ON SCHEMA despacho FROM PUBLIC")
    for statement in TABLES:
        op.execute(statement)
    for statement in INDEXES:
        op.execute(statement)

    op.execute(
        """
        INSERT INTO despacho.unidades(nome)
        VALUES
            ('Santa Casa'),
            ('Unimed-Lar'),
            ('Unimed-Camu 1'),
            ('Unimed-Camu 2'),
            ('Unimed Farmais')
        ON CONFLICT (nome) DO NOTHING
        """
    )
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA despacho FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA despacho FROM PUBLIC")
    op.execute(
        """
        DO $bloqueio_api$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                EXECUTE 'REVOKE ALL ON SCHEMA despacho FROM anon';
                EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA despacho FROM anon';
                EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA despacho FROM anon';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                EXECUTE 'REVOKE ALL ON SCHEMA despacho FROM authenticated';
                EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA despacho FROM authenticated';
                EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA despacho FROM authenticated';
            END IF;
        END
        $bloqueio_api$
        """
    )


def downgrade():
    for table in (
        "notificacoes",
        "chat_mensagens",
        "indisponibilidades_entregador",
        "localizacoes_pedido",
        "tipos_coleta_unidade",
        "pedidos",
        "operadores_solicitante",
        "usuarios",
        "unidades",
    ):
        op.execute(f"DROP TABLE IF EXISTS despacho.{table} CASCADE")
