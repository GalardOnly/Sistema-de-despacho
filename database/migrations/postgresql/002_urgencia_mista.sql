SET search_path TO despacho, public;

ALTER TABLE pedidos
    ADD COLUMN IF NOT EXISTS urgencia_mista SMALLINT NOT NULL DEFAULT 0
    CHECK (urgencia_mista IN (0, 1));

INSERT INTO schema_migrations(version)
VALUES ('002_urgencia_mista')
ON CONFLICT (version) DO NOTHING;
