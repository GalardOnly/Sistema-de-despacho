"""Schema e ciclo de vida do banco usado pelo módulo de despacho."""

import os
import sys

from flask import g
from werkzeug.security import generate_password_hash

from .. import config
from ..config import POSTGRES_SCHEMA_REVISION, SQLITE_SCHEMA_OBRIGATORIO, URGENCIA_META
from ..config import senha_admin_inicial_configurada
from ..extensions import despacho_bp
from .runtime import abrir_conexao, banco_postgres_configurado
from ..validation import texto


DESP_DB_PATH = config.DESP_DB_PATH


def _db_path():
    for nome_modulo in ("corridas.despacho", "despacho"):
        fachada = sys.modules.get(nome_modulo)
        if fachada is not None and hasattr(fachada, "DESP_DB_PATH"):
            return fachada.DESP_DB_PATH
    return config.DESP_DB_PATH


def _protocolo(pedido_id):
    return f"COL-{pedido_id:05d}"


def get_db_desp():
    if "db_desp" not in g:
        g.db_desp = abrir_conexao(_db_path())
    return g.db_desp


def verificar_db_desp():
    if not banco_postgres_configurado() and not os.path.isfile(_db_path()):
        raise RuntimeError("Banco SQLite não inicializado. Execute scripts/init_sqlite.py.")
    con = get_db_desp()
    if banco_postgres_configurado():
        _validar_schema_postgres(con)
    else:
        _validar_schema_sqlite(con)
    return True


@despacho_bp.teardown_app_request
def close_db_desp(exc):
    d = g.pop("db_desp", None)
    if d is not None:
        if exc is not None:
            d.rollback()
        d.close()


def _validar_schema_sqlite(con):
    for tabela, colunas_obrigatorias in SQLITE_SCHEMA_OBRIGATORIO.items():
        colunas = {row[1] for row in con.execute(f"PRAGMA table_info({tabela})")}
        ausentes = colunas_obrigatorias - colunas
        if ausentes:
            raise RuntimeError(
                f"Schema SQLite desatualizado em {tabela}. Execute scripts/init_sqlite.py."
            )


def _ensure_column(con, table, column, definition):
    columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _normalizar_veiculo(valor, padrao=None):
    normalizado = " ".join(texto(valor).strip().split()).casefold()
    return normalizado or padrao


def init_db_desp():
    if banco_postgres_configurado():
        raise RuntimeError(
            "A inicialização do PostgreSQL é externa ao servidor. "
            "Execute scripts/init_supabase.py."
        )

    con = abrir_conexao(_db_path())
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS unidades(
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usuarios(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nome        TEXT NOT NULL,
            username    TEXT UNIQUE NOT NULL,
            senha_hash  TEXT NOT NULL,
            papel       TEXT NOT NULL,
            unidade_id  INTEGER REFERENCES unidades(id),
            ativo       INTEGER NOT NULL DEFAULT 1,
            disponivel  INTEGER NOT NULL DEFAULT 1,
            codigo_ref  TEXT,
            tipo_veiculo TEXT,
            indisponibilidade_justificativa TEXT,
            indisponibilidade_tipo TEXT,
            indisponibilidade_ts INTEGER,
            sessao_versao INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS pedidos(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            protocolo   TEXT UNIQUE,
            origem_id   INTEGER NOT NULL REFERENCES unidades(id),
            destino_id  INTEGER NOT NULL REFERENCES unidades(id),
            tipo        TEXT NOT NULL,
            urgencia    TEXT NOT NULL,
            urgencia_mista INTEGER NOT NULL DEFAULT 0,
            tipo_veiculo TEXT,
            sla_limite_min INTEGER,
            status      TEXT NOT NULL DEFAULT 'solicitado',
            entregador_id INTEGER REFERENCES usuarios(id),
            criado_por  INTEGER REFERENCES usuarios(id),
            operador_id INTEGER REFERENCES operadores_solicitante(id),
            ts_solicitado INTEGER NOT NULL,
            ts_aceito_admin INTEGER,
            ts_despachado INTEGER,
            ts_aceito_entregador INTEGER,
            ts_coletado   INTEGER,
            ts_entregue   INTEGER,
            ts_cancelado  INTEGER,
            motivo_cancelamento TEXT,
            justificativa_atraso TEXT
        );
        CREATE TABLE IF NOT EXISTS operadores_solicitante(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            unidade_id  INTEGER NOT NULL REFERENCES unidades(id),
            nome        TEXT NOT NULL,
            nome_normalizado TEXT NOT NULL,
            codigo      TEXT NOT NULL,
            ativo       INTEGER NOT NULL DEFAULT 1,
            criado_em   INTEGER NOT NULL,
            UNIQUE(unidade_id, codigo)
        );
        CREATE TABLE IF NOT EXISTS tipos_coleta_unidade(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            unidade_id  INTEGER NOT NULL REFERENCES unidades(id),
            nome        TEXT NOT NULL,
            nome_normalizado TEXT NOT NULL,
            ativo       INTEGER NOT NULL DEFAULT 1,
            criado_em   INTEGER NOT NULL,
            UNIQUE(unidade_id, nome_normalizado)
        );
        CREATE TABLE IF NOT EXISTS localizacoes_pedido(
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id      INTEGER NOT NULL REFERENCES pedidos(id),
            entregador_id  INTEGER NOT NULL REFERENCES usuarios(id),
            latitude       REAL NOT NULL,
            longitude      REAL NOT NULL,
            precisao       REAL,
            ts             INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS indisponibilidades_entregador(
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            entregador_id  INTEGER NOT NULL REFERENCES usuarios(id),
            tipo           TEXT NOT NULL,
            justificativa  TEXT NOT NULL,
            ts             INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chat_mensagens(
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            solicitante_id INTEGER NOT NULL REFERENCES usuarios(id),
            unidade_id     INTEGER REFERENCES unidades(id),
            remetente_id   INTEGER NOT NULL REFERENCES usuarios(id),
            remetente_papel TEXT NOT NULL,
            texto          TEXT NOT NULL,
            ts             INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notificacoes(
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            papel_destino  TEXT NOT NULL,
            usuario_id     INTEGER REFERENCES usuarios(id),
            unidade_id     INTEGER REFERENCES unidades(id),
            pedido_id      INTEGER REFERENCES pedidos(id),
            tipo           TEXT NOT NULL,
            titulo         TEXT NOT NULL,
            mensagem       TEXT NOT NULL,
            lida           INTEGER NOT NULL DEFAULT 0,
            criado_em      INTEGER NOT NULL,
            lida_em        INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_localizacoes_pedido_ts
            ON localizacoes_pedido(pedido_id, ts);
        CREATE INDEX IF NOT EXISTS idx_operadores_solicitante_unidade
            ON operadores_solicitante(unidade_id, ativo, nome);
        CREATE INDEX IF NOT EXISTS idx_tipos_coleta_unidade
            ON tipos_coleta_unidade(unidade_id, ativo, nome);
        CREATE INDEX IF NOT EXISTS idx_chat_solicitante_ts
            ON chat_mensagens(solicitante_id, ts);
        CREATE INDEX IF NOT EXISTS idx_notificacoes_destino
            ON notificacoes(papel_destino, usuario_id, unidade_id, lida, criado_em);
        """
    )

    for column, definition in (
        ("disponivel", "INTEGER NOT NULL DEFAULT 1"),
        ("codigo_ref", "TEXT"),
        ("tipo_veiculo", "TEXT"),
        ("indisponibilidade_justificativa", "TEXT"),
        ("indisponibilidade_tipo", "TEXT"),
        ("indisponibilidade_ts", "INTEGER"),
        ("sessao_versao", "INTEGER NOT NULL DEFAULT 1"),
    ):
        _ensure_column(con, "usuarios", column, definition)

    for column, definition in (
        ("ts_aceito_admin", "INTEGER"),
        ("ts_aceito_entregador", "INTEGER"),
        ("ts_cancelado", "INTEGER"),
        ("tipo_veiculo", "TEXT"),
        ("sla_limite_min", "INTEGER"),
        ("urgencia_mista", "INTEGER NOT NULL DEFAULT 0"),
        ("justificativa_atraso", "TEXT"),
        ("operador_id", "INTEGER REFERENCES operadores_solicitante(id)"),
    ):
        _ensure_column(con, "pedidos", column, definition)

    _ensure_column(con, "chat_mensagens", "unidade_id", "INTEGER REFERENCES unidades(id)")
    _ensure_column(con, "operadores_solicitante", "nome_normalizado", "TEXT")
    operadores = con.execute(
        "SELECT id, unidade_id, nome, ativo FROM operadores_solicitante ORDER BY id"
    ).fetchall()
    nomes_ativos = set()
    for operador in operadores:
        nome_normalizado = " ".join(texto(operador["nome"]).strip().split()).casefold()
        ativo = int(operador["ativo"])
        chave = (operador["unidade_id"], nome_normalizado)
        if ativo and chave in nomes_ativos:
            ativo = 0
        elif ativo:
            nomes_ativos.add(chave)
        con.execute(
            "UPDATE operadores_solicitante SET nome_normalizado=?, ativo=? WHERE id=?",
            (nome_normalizado, ativo, operador["id"]),
        )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_operadores_unidade_nome_ativo "
        "ON operadores_solicitante(unidade_id, nome_normalizado) WHERE ativo=1"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_unidade_ts ON chat_mensagens(unidade_id, ts)"
    )

    try:
        _popular_dados_iniciais(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _validar_schema_postgres(con):
    try:
        versao = con.execute(
            "SELECT version_num FROM alembic_version WHERE version_num=?",
            (POSTGRES_SCHEMA_REVISION,),
        ).fetchone()
    except Exception as exc:
        raise RuntimeError(
            "O schema do Supabase ainda não foi preparado pelo Alembic. "
            "Execute scripts/init_supabase.py antes de iniciar a aplicação."
        ) from exc
    if not versao:
        raise RuntimeError(
            f"A revisão Alembic {POSTGRES_SCHEMA_REVISION} não foi aplicada no Supabase. "
            "Execute scripts/init_supabase.py."
        )


def _popular_dados_iniciais(con):

    default_units = ["Santa Casa", "Unimed-Lar", "Unimed-Camu 1", "Unimed-Camu 2", "Unimed Farmais"]
    for nome in default_units:
        con.execute("INSERT OR IGNORE INTO unidades(nome) VALUES(?)", (nome,))

    con.execute(
        "UPDATE pedidos SET ts_aceito_admin=ts_despachado "
        "WHERE ts_aceito_admin IS NULL AND ts_despachado IS NOT NULL"
    )
    con.execute(
        "UPDATE pedidos SET status='aguardando_entregador' "
        "WHERE status='despachado' AND ts_coletado IS NULL AND ts_entregue IS NULL"
    )
    con.execute(
        "UPDATE pedidos SET tipo_veiculo='moto' WHERE tipo_veiculo IS NULL OR TRIM(tipo_veiculo)=''"
    )
    con.execute(
        "UPDATE usuarios SET tipo_veiculo='moto' "
        "WHERE papel='entregador' AND (tipo_veiculo IS NULL OR TRIM(tipo_veiculo)='')"
    )
    con.execute("UPDATE pedidos SET tipo_veiculo=LOWER(TRIM(tipo_veiculo)) WHERE tipo_veiculo IS NOT NULL")
    con.execute("UPDATE usuarios SET tipo_veiculo=LOWER(TRIM(tipo_veiculo)) WHERE tipo_veiculo IS NOT NULL")
    for urgencia, meta in URGENCIA_META.items():
        con.execute(
            "UPDATE pedidos SET sla_limite_min=? WHERE urgencia=? AND sla_limite_min IS NULL",
            (meta["sla_min"], urgencia),
        )
    for row in con.execute("SELECT id FROM pedidos WHERE protocolo IS NULL OR protocolo=''").fetchall():
        con.execute("UPDATE pedidos SET protocolo=? WHERE id=?", (_protocolo(row["id"]), row["id"]))

    existe = con.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()["n"]
    if existe == 0:
        try:
            senha_admin = senha_admin_inicial_configurada()
        except RuntimeError:
            raise
        con.execute(
            "INSERT INTO usuarios(nome,username,senha_hash,papel,unidade_id) VALUES(?,?,?,?,?)",
            ("Administrador", "admin", generate_password_hash(senha_admin), "admin", None),
        )

    con.execute(
        """
        UPDATE chat_mensagens
        SET unidade_id = (
            SELECT unidade_id FROM usuarios WHERE usuarios.id = chat_mensagens.solicitante_id
        )
        WHERE unidade_id IS NULL
        """
    )
