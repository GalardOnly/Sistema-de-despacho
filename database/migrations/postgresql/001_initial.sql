CREATE SCHEMA IF NOT EXISTS despacho;
REVOKE ALL ON SCHEMA despacho FROM PUBLIC;
SET search_path TO despacho, public;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    aplicado_em TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS unidades (
    id BIGSERIAL PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS usuarios (
    id BIGSERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL,
    papel TEXT NOT NULL CHECK (papel IN ('admin', 'solicitante', 'entregador')),
    unidade_id BIGINT REFERENCES unidades(id),
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
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_usuarios_codigo_ref_ativo
    ON usuarios(codigo_ref)
    WHERE codigo_ref IS NOT NULL AND papel <> 'admin' AND ativo = 1;

CREATE TABLE IF NOT EXISTS operadores_solicitante (
    id BIGSERIAL PRIMARY KEY,
    unidade_id BIGINT NOT NULL REFERENCES unidades(id),
    nome TEXT NOT NULL,
    codigo TEXT NOT NULL,
    ativo SMALLINT NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    criado_em BIGINT NOT NULL,
    UNIQUE (unidade_id, codigo)
);

CREATE TABLE IF NOT EXISTS pedidos (
    id BIGSERIAL PRIMARY KEY,
    protocolo TEXT UNIQUE,
    origem_id BIGINT NOT NULL REFERENCES unidades(id),
    destino_id BIGINT NOT NULL REFERENCES unidades(id),
    tipo TEXT NOT NULL,
    urgencia TEXT NOT NULL CHECK (urgencia IN ('rotina', 'urgente', 'emergencia')),
    urgencia_mista SMALLINT NOT NULL DEFAULT 0 CHECK (urgencia_mista IN (0, 1)),
    tipo_veiculo TEXT NOT NULL CHECK (tipo_veiculo IN ('moto', 'carro')),
    sla_limite_min INTEGER NOT NULL CHECK (sla_limite_min > 0),
    status TEXT NOT NULL DEFAULT 'solicitado',
    entregador_id BIGINT REFERENCES usuarios(id),
    criado_por BIGINT REFERENCES usuarios(id),
    operador_id BIGINT REFERENCES operadores_solicitante(id),
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
);

CREATE TABLE IF NOT EXISTS tipos_coleta_unidade (
    id BIGSERIAL PRIMARY KEY,
    unidade_id BIGINT NOT NULL REFERENCES unidades(id),
    nome TEXT NOT NULL,
    nome_normalizado TEXT NOT NULL,
    ativo SMALLINT NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    criado_em BIGINT NOT NULL,
    UNIQUE (unidade_id, nome_normalizado)
);

CREATE TABLE IF NOT EXISTS localizacoes_pedido (
    id BIGSERIAL PRIMARY KEY,
    pedido_id BIGINT NOT NULL REFERENCES pedidos(id) ON DELETE CASCADE,
    entregador_id BIGINT NOT NULL REFERENCES usuarios(id),
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    precisao DOUBLE PRECISION CHECK (precisao IS NULL OR precisao >= 0),
    ts BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS indisponibilidades_entregador (
    id BIGSERIAL PRIMARY KEY,
    entregador_id BIGINT NOT NULL REFERENCES usuarios(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('clt_desconto', 'recusa_padrao')),
    justificativa TEXT NOT NULL,
    ts BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_mensagens (
    id BIGSERIAL PRIMARY KEY,
    solicitante_id BIGINT NOT NULL REFERENCES usuarios(id),
    unidade_id BIGINT NOT NULL REFERENCES unidades(id),
    remetente_id BIGINT NOT NULL REFERENCES usuarios(id),
    remetente_papel TEXT NOT NULL CHECK (remetente_papel IN ('admin', 'solicitante')),
    texto TEXT NOT NULL,
    ts BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS notificacoes (
    id BIGSERIAL PRIMARY KEY,
    papel_destino TEXT NOT NULL CHECK (papel_destino IN ('admin', 'solicitante', 'entregador')),
    usuario_id BIGINT REFERENCES usuarios(id),
    unidade_id BIGINT REFERENCES unidades(id),
    pedido_id BIGINT REFERENCES pedidos(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    lida SMALLINT NOT NULL DEFAULT 0 CHECK (lida IN (0, 1)),
    criado_em BIGINT NOT NULL,
    lida_em BIGINT
);

CREATE INDEX IF NOT EXISTS idx_pedidos_fila
    ON pedidos(status, urgencia, ts_solicitado);
CREATE INDEX IF NOT EXISTS idx_pedidos_origem
    ON pedidos(origem_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_pedidos_entregador
    ON pedidos(entregador_id, status, id DESC);
CREATE INDEX IF NOT EXISTS idx_localizacoes_pedido_ts
    ON localizacoes_pedido(pedido_id, ts);
CREATE INDEX IF NOT EXISTS idx_operadores_solicitante_unidade
    ON operadores_solicitante(unidade_id, ativo, nome);
CREATE INDEX IF NOT EXISTS idx_tipos_coleta_unidade
    ON tipos_coleta_unidade(unidade_id, ativo, nome);
CREATE INDEX IF NOT EXISTS idx_chat_solicitante_ts
    ON chat_mensagens(solicitante_id, ts);
CREATE INDEX IF NOT EXISTS idx_chat_unidade_ts
    ON chat_mensagens(unidade_id, ts);
CREATE INDEX IF NOT EXISTS idx_notificacoes_destino
    ON notificacoes(papel_destino, usuario_id, unidade_id, lida, criado_em);

REVOKE ALL ON ALL TABLES IN SCHEMA despacho FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA despacho FROM PUBLIC;

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
$bloqueio_api$;

INSERT INTO schema_migrations(version)
VALUES ('001_initial')
ON CONFLICT (version) DO NOTHING;
