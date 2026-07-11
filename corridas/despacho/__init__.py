"""Rotas do sistema de despacho de coletas."""

import calendar
import hashlib
import hmac
import math
import os
import secrets
import sqlite3
import time
from datetime import datetime, time as dt_time
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

if "." in (__package__ or ""):
    from ..security import limitar_falhas_login
else:
    from security import limitar_falhas_login


TZ = ZoneInfo("America/Sao_Paulo")
DESP_DB_PATH = os.environ.get(
    "DESPACHO_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "despacho.db"),
)

TIPOS_EXAME = ["Sangue", "Urina", "Gasometria", "Microbiologia/Cultura", "Anatomia patológica", "Outro"]
URGENCIAS = ["rotina", "urgente", "emergencia"]
URGENCIA_META = {
    "rotina": {"prioridade": 2, "sla_min": 720, "label": "ROTINA (12 horas)"},
    "urgente": {"prioridade": 1, "sla_min": 40, "label": "URGENTE (40 min)"},
    "emergencia": {"prioridade": 0, "sla_min": 15, "label": "EMERGÊNCIA (15 min)"},
}
PRIORIDADE = {k: v["prioridade"] for k, v in URGENCIA_META.items()}
PAPEIS = ["admin", "solicitante", "entregador"]
TIPOS_VEICULO = ["moto", "carro"]
TIPOS_INDISPONIBILIDADE = ["clt_desconto", "recusa_padrao"]

STATUS_ATIVOS_ENTREGADOR = (
    "aguardando_entregador",
    "em_rota_retirada",
    "em_rota",
    "despachado",
    "coletado",
)
STATUS_RASTREAMENTO = ("em_rota_retirada", "em_rota", "despachado", "coletado")
STATUS_EM_ANDAMENTO = STATUS_ATIVOS_ENTREGADOR
CSRF_SESSION_KEY = "desp_csrf_token"
CSRF_HEADER = "X-CSRF-Token"
METODOS_COM_MUTACAO = {"POST", "PUT", "PATCH", "DELETE"}

SENHAS_ADMIN_INSEGURAS = {
    "mudar123",
    "admin",
    "admin123",
    "senha",
    "password",
    "troque-a-senha-inicial",
}


def parece_senha_admin_insegura(valor):
    texto = valor.casefold()
    return texto in SENHAS_ADMIN_INSEGURAS or any(
        termo in texto for termo in ("troque", "change", "cole_aqui", "defina_", "placeholder")
    )


def senha_admin_inicial_configurada():
    senha = (os.environ.get("DESPACHO_ADMIN_SENHA_INICIAL") or "").strip()
    if not senha:
        raise RuntimeError(
            "Defina DESPACHO_ADMIN_SENHA_INICIAL antes de criar o primeiro administrador."
        )
    if len(senha) < 8 or parece_senha_admin_insegura(senha):
        raise RuntimeError(
            "DESPACHO_ADMIN_SENHA_INICIAL precisa ter pelo menos 8 caracteres e não pode ser padrão."
        )
    return senha

despacho_bp = Blueprint(
    "despacho",
    __name__,
    url_prefix="/despacho",
    template_folder="templates",
)


def get_db_desp():
    if "db_desp" not in g:
        g.db_desp = sqlite3.connect(DESP_DB_PATH)
        g.db_desp.row_factory = sqlite3.Row
    return g.db_desp


def verificar_db_desp():
    con = get_db_desp()
    con.execute("SELECT 1").fetchone()
    return True


@despacho_bp.teardown_app_request
def close_db_desp(exc):
    d = g.pop("db_desp", None)
    if d is not None:
        d.close()


def _ensure_column(con, table, column, definition):
    columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _normalizar_veiculo(valor, padrao=None):
    texto = " ".join((valor or "").strip().split()).casefold()
    return texto or padrao


def init_db_desp():
    con = sqlite3.connect(DESP_DB_PATH)
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
            indisponibilidade_ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS pedidos(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            protocolo   TEXT UNIQUE,
            origem_id   INTEGER NOT NULL REFERENCES unidades(id),
            destino_id  INTEGER NOT NULL REFERENCES unidades(id),
            tipo        TEXT NOT NULL,
            urgencia    TEXT NOT NULL,
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
    ):
        _ensure_column(con, "usuarios", column, definition)

    for column, definition in (
        ("ts_aceito_admin", "INTEGER"),
        ("ts_aceito_entregador", "INTEGER"),
        ("ts_cancelado", "INTEGER"),
        ("tipo_veiculo", "TEXT"),
        ("sla_limite_min", "INTEGER"),
        ("justificativa_atraso", "TEXT"),
        ("operador_id", "INTEGER REFERENCES operadores_solicitante(id)"),
    ):
        _ensure_column(con, "pedidos", column, definition)

    _ensure_column(con, "chat_mensagens", "unidade_id", "INTEGER REFERENCES unidades(id)")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_unidade_ts ON chat_mensagens(unidade_id, ts)"
    )

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
        con.execute("UPDATE pedidos SET protocolo=? WHERE id=?", (_protocolo(row[0]), row[0]))

    existe = con.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()[0]
    if existe == 0:
        try:
            senha_admin = senha_admin_inicial_configurada()
        except RuntimeError:
            con.close()
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

    con.commit()
    con.close()


def agora_ms():
    return int(time.time() * 1000)


def _protocolo(pid):
    return f"COL-{pid:05d}"


def _sla_limite_min(urgencia):
    return URGENCIA_META.get(urgencia, URGENCIA_META["rotina"])["sla_min"]


def _status_placeholders(statuses):
    return ",".join("?" for _ in statuses)


def _as_dict(row):
    return dict(row) if row is not None else {}


def _nome_unidade(con, unidade_id):
    row = con.execute("SELECT nome FROM unidades WHERE id=?", (unidade_id,)).fetchone()
    return row["nome"] if row else "?"


def _nome_usuario(con, usuario_id):
    if not usuario_id:
        return None
    row = con.execute("SELECT nome FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
    return row["nome"] if row else None


def _normalizar_tipo_coleta(nome):
    return " ".join((nome or "").strip().split()).casefold()


def _tipos_base_sem_outro():
    return [tipo for tipo in TIPOS_EXAME if tipo != "Outro"]


def _tipos_exame_da_unidade(con, unidade_id):
    tipos = list(_tipos_base_sem_outro())
    vistos = {_normalizar_tipo_coleta(tipo) for tipo in tipos}
    rows = con.execute(
        """
        SELECT nome
        FROM tipos_coleta_unidade
        WHERE unidade_id=? AND ativo=1
        ORDER BY nome
        """,
        (unidade_id,),
    ).fetchall()
    for row in rows:
        normalizado = _normalizar_tipo_coleta(row["nome"])
        if normalizado and normalizado not in vistos:
            tipos.append(row["nome"])
            vistos.add(normalizado)
    tipos.append("Outro")
    return tipos


def _buscar_tipo_custom(con, unidade_id, nome):
    normalizado = _normalizar_tipo_coleta(nome)
    if not normalizado:
        return None
    return con.execute(
        """
        SELECT nome
        FROM tipos_coleta_unidade
        WHERE unidade_id=? AND nome_normalizado=? AND ativo=1
        """,
        (unidade_id, normalizado),
    ).fetchone()


def _salvar_tipo_custom(con, unidade_id, nome):
    nome_limpo = " ".join((nome or "").strip().split())
    normalizado = _normalizar_tipo_coleta(nome_limpo)
    if len(nome_limpo) < 2 or normalizado == "outro":
        return None

    tipos_base = {_normalizar_tipo_coleta(tipo): tipo for tipo in _tipos_base_sem_outro()}
    if normalizado in tipos_base:
        return tipos_base[normalizado]

    con.execute(
        """
        INSERT OR IGNORE INTO tipos_coleta_unidade(unidade_id,nome,nome_normalizado,ativo,criado_em)
        VALUES(?,?,?,?,?)
        """,
        (unidade_id, nome_limpo, normalizado, 1, agora_ms()),
    )
    row = _buscar_tipo_custom(con, unidade_id, nome_limpo)
    return row["nome"] if row else nome_limpo


def _resolver_tipo_pedido(con, unidade_id, tipo, tipo_outro=None):
    tipo_limpo = " ".join((tipo or "").strip().split())
    normalizado = _normalizar_tipo_coleta(tipo_limpo)
    tipos_base = {_normalizar_tipo_coleta(t): t for t in _tipos_base_sem_outro()}

    if normalizado == "outro":
        return _salvar_tipo_custom(con, unidade_id, tipo_outro)
    if normalizado in tipos_base:
        return tipos_base[normalizado]

    row = _buscar_tipo_custom(con, unidade_id, tipo_limpo)
    return row["nome"] if row else None


def _gerar_codigo_operador(con, unidade_id, nome):
    for _ in range(20):
        base = f"{unidade_id}:{nome}:{agora_ms()}:{secrets.token_hex(16)}"
        codigo = hashlib.sha256(base.encode("utf-8")).hexdigest()
        existe = con.execute(
            "SELECT 1 FROM operadores_solicitante WHERE unidade_id=? AND codigo=? LIMIT 1",
            (unidade_id, codigo),
        ).fetchone()
        if not existe:
            return codigo
    raise RuntimeError("não foi possível gerar o identificador interno")


def _normalizar_nome_operador(nome):
    return " ".join((nome or "").strip().split()).casefold()


def _buscar_ou_criar_operador(con, unidade_id, nome):
    nome_limpo = " ".join((nome or "").strip().split())
    if len(nome_limpo) < 2:
        return None

    nome_normalizado = _normalizar_nome_operador(nome_limpo)
    rows = con.execute(
        """
        SELECT id, unidade_id, nome, codigo, ativo, criado_em
        FROM operadores_solicitante
        WHERE unidade_id=? AND ativo=1
        ORDER BY id
        """,
        (unidade_id,),
    ).fetchall()
    for row in rows:
        if _normalizar_nome_operador(row["nome"]) == nome_normalizado:
            return row

    codigo = _gerar_codigo_operador(con, unidade_id, nome_limpo)
    cur = con.execute(
        "INSERT INTO operadores_solicitante(unidade_id,nome,codigo,ativo,criado_em) "
        "VALUES(?,?,?,?,?)",
        (unidade_id, nome_limpo, codigo, 1, agora_ms()),
    )
    return con.execute(
        "SELECT id, unidade_id, nome, codigo, ativo, criado_em FROM operadores_solicitante WHERE id=?",
        (cur.lastrowid,),
    ).fetchone()


def _operador_linha(row):
    return {
        "id": row["id"],
        "unidade_id": row["unidade_id"],
        "nome": row["nome"],
        "ativo": bool(row["ativo"]),
        "criado_em": row["criado_em"],
    }


def _operador_do_pedido(con, operador_id):
    if not operador_id:
        return None
    return con.execute(
        "SELECT id, unidade_id, nome, codigo, ativo, criado_em FROM operadores_solicitante WHERE id=?",
        (operador_id,),
    ).fetchone()


def _sla_do_pedido(r, referencia_ms=None):
    d = _as_dict(r)
    limite_min = d.get("sla_limite_min") or _sla_limite_min(d.get("urgencia"))
    limite_ms = int(limite_min) * 60 * 1000
    fim = d.get("ts_entregue") or referencia_ms or agora_ms()
    inicio = d.get("ts_solicitado") or fim
    decorrido_ms = max(0, fim - inicio)
    excedido_ms = max(0, decorrido_ms - limite_ms)
    return {
        "limite_min": int(limite_min),
        "limite_ms": limite_ms,
        "decorrido_ms": decorrido_ms,
        "excedido_ms": excedido_ms,
        "atrasado": excedido_ms > 0,
    }


def _fmt_duracao(ms):
    total_min = int(math.ceil(max(0, ms) / 60000))
    horas, minutos = divmod(total_min, 60)
    if horas and minutos:
        return f"{horas}h {minutos}min"
    if horas:
        return f"{horas}h"
    return f"{minutos}min"


def linha_pedido(con, r):
    d = _as_dict(r)
    entregador = _nome_usuario(con, d.get("entregador_id"))
    solicitante = _nome_usuario(con, d.get("criado_por"))
    operador = _operador_do_pedido(con, d.get("operador_id"))
    return {
        "id": d["id"],
        "protocolo": d.get("protocolo") or _protocolo(d["id"]),
        "origem_id": d["origem_id"],
        "origem": _nome_unidade(con, d["origem_id"]),
        "destino_id": d["destino_id"],
        "destino": _nome_unidade(con, d["destino_id"]),
        "tipo": d["tipo"],
        "urgencia": d["urgencia"],
        "urgencia_label": URGENCIA_META.get(d["urgencia"], {}).get("label", d["urgencia"]),
        "tipo_veiculo": _normalizar_veiculo(d.get("tipo_veiculo"), "moto"),
        "sla_limite_min": d.get("sla_limite_min") or _sla_limite_min(d["urgencia"]),
        "sla": _sla_do_pedido(d),
        "status": d["status"],
        "entregador_id": d.get("entregador_id"),
        "entregador": entregador,
        "solicitante_id": d.get("criado_por"),
        "solicitante": solicitante,
        "operador_id": d.get("operador_id"),
        "operador_nome": operador["nome"] if operador else None,
        "ts": {
            "solicitado": d.get("ts_solicitado"),
            "aceito_admin": d.get("ts_aceito_admin"),
            "despachado": d.get("ts_despachado"),
            "aceito_entregador": d.get("ts_aceito_entregador"),
            "coletado": d.get("ts_coletado"),
            "entregue": d.get("ts_entregue"),
            "cancelado": d.get("ts_cancelado"),
        },
        "motivo_cancelamento": d.get("motivo_cancelamento"),
        "justificativa_atraso": d.get("justificativa_atraso"),
    }


def _entregador_ocupado(con, entregador_id):
    placeholders = _status_placeholders(STATUS_ATIVOS_ENTREGADOR)
    return (
        con.execute(
            f"SELECT 1 FROM pedidos WHERE entregador_id=? AND status IN ({placeholders}) LIMIT 1",
            (entregador_id, *STATUS_ATIVOS_ENTREGADOR),
        ).fetchone()
        is not None
    )


def _pedido_ou_404(con, pid):
    return con.execute("SELECT * FROM pedidos WHERE id=?", (pid,)).fetchone()


def _range_dia_ms(data=None):
    base = data or datetime.now(TZ).date()
    inicio = datetime.combine(base, dt_time.min, tzinfo=TZ)
    fim = datetime.combine(base, dt_time.max, tzinfo=TZ)
    return int(inicio.timestamp() * 1000), int(fim.timestamp() * 1000)


def _range_mes_ms(ano, mes):
    inicio = datetime(int(ano), int(mes), 1, tzinfo=TZ)
    ultimo = calendar.monthrange(int(ano), int(mes))[1]
    fim = datetime(int(ano), int(mes), ultimo, 23, 59, 59, 999000, tzinfo=TZ)
    return int(inicio.timestamp() * 1000), int(fim.timestamp() * 1000)


def _chat_linha(row):
    return {
        "id": row["id"],
        "solicitante_id": row["solicitante_id"],
        "unidade_id": row["unidade_id"],
        "remetente_id": row["remetente_id"],
        "remetente_nome": row["remetente_nome"],
        "remetente_papel": row["remetente_papel"],
        "unidade": row["unidade"],
        "texto": row["texto"],
        "ts": row["ts"],
    }


def _notificacao_linha(row):
    return {
        "id": row["id"],
        "papel_destino": row["papel_destino"],
        "usuario_id": row["usuario_id"],
        "unidade_id": row["unidade_id"],
        "pedido_id": row["pedido_id"],
        "protocolo": row["protocolo"],
        "tipo": row["tipo"],
        "titulo": row["titulo"],
        "mensagem": row["mensagem"],
        "lida": bool(row["lida"]),
        "criado_em": row["criado_em"],
        "lida_em": row["lida_em"],
    }


def _criar_notificacao(
    con,
    papel_destino,
    titulo,
    mensagem,
    tipo="info",
    pedido_id=None,
    usuario_id=None,
    unidade_id=None,
):
    con.execute(
        """
        INSERT INTO notificacoes(
            papel_destino, usuario_id, unidade_id, pedido_id, tipo, titulo, mensagem, lida, criado_em
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            papel_destino,
            usuario_id,
            unidade_id,
            pedido_id,
            tipo,
            titulo,
            mensagem,
            0,
            agora_ms(),
        ),
    )


def _pedido_resumo(con, pedido):
    protocolo = pedido["protocolo"] or _protocolo(pedido["id"])
    origem = _nome_unidade(con, pedido["origem_id"])
    destino = _nome_unidade(con, pedido["destino_id"])
    return protocolo, origem, destino


def _notificar_admin_novo_pedido(con, pedido):
    protocolo, origem, destino = _pedido_resumo(con, pedido)
    _criar_notificacao(
        con,
        "admin",
        "Novo pedido para retirada",
        f"{protocolo}: {origem} solicitou coleta para {destino}.",
        "pedido",
        pedido_id=pedido["id"],
    )


def _notificar_despacho(con, pedido):
    protocolo, origem, destino = _pedido_resumo(con, pedido)
    entregador = _nome_usuario(con, pedido["entregador_id"]) or "Entregador"
    _criar_notificacao(
        con,
        "entregador",
        "Novo pedido atribuído",
        f"{protocolo}: retire em {origem} e entregue em {destino}.",
        "despacho",
        pedido_id=pedido["id"],
        usuario_id=pedido["entregador_id"],
    )
    _criar_notificacao(
        con,
        "solicitante",
        "Pedido aceito pelo admin",
        f"{protocolo}: {entregador} foi acionado e irá retirar o exame.",
        "despacho",
        pedido_id=pedido["id"],
        unidade_id=pedido["origem_id"],
    )


def _notificar_entregador_a_caminho(con, pedido):
    protocolo, origem, _destino = _pedido_resumo(con, pedido)
    entregador = _nome_usuario(con, pedido["entregador_id"]) or "Entregador"
    _criar_notificacao(
        con,
        "solicitante",
        "Entregador a caminho",
        f"{protocolo}: {entregador} aceitou o pedido e está indo para {origem}.",
        "rota",
        pedido_id=pedido["id"],
        unidade_id=pedido["origem_id"],
    )


def _notificar_retirada(con, pedido):
    protocolo, origem, destino = _pedido_resumo(con, pedido)
    mensagem = f"{protocolo}: exame retirado em {origem} e em transporte para {destino}."
    _criar_notificacao(
        con,
        "admin",
        "Exame retirado",
        mensagem,
        "retirada",
        pedido_id=pedido["id"],
    )
    _criar_notificacao(
        con,
        "solicitante",
        "Exame retirado",
        mensagem,
        "retirada",
        pedido_id=pedido["id"],
        unidade_id=pedido["origem_id"],
    )


def _notificar_entrega(con, pedido):
    protocolo, _origem, destino = _pedido_resumo(con, pedido)
    mensagem = f"{protocolo}: entrega confirmada em {destino}."
    _criar_notificacao(
        con,
        "admin",
        "Entrega confirmada",
        mensagem,
        "entrega",
        pedido_id=pedido["id"],
    )
    _criar_notificacao(
        con,
        "solicitante",
        "Entrega confirmada",
        mensagem,
        "entrega",
        pedido_id=pedido["id"],
        unidade_id=pedido["origem_id"],
    )


def _resumo_mensagem_chat(texto, limite=120):
    resumo = " ".join((texto or "").split())
    if len(resumo) <= limite:
        return resumo
    return resumo[: limite - 1].rstrip() + "…"


def _notificar_chat(con, papel_remetente, unidade_id, texto):
    unidade = _nome_unidade(con, unidade_id) or "Unidade"
    resumo = _resumo_mensagem_chat(texto)
    if papel_remetente == "solicitante":
        _criar_notificacao(
            con,
            "admin",
            "Nova mensagem no chat",
            f"{unidade}: {resumo}",
            "chat",
            unidade_id=unidade_id,
        )
    elif papel_remetente == "admin":
        _criar_notificacao(
            con,
            "solicitante",
            "Mensagem do administrador",
            f"Admin para {unidade}: {resumo}",
            "chat",
            unidade_id=unidade_id,
        )


def _filtro_notificacoes_sessao(alias="n"):
    papel = session["desp_papel"]
    if papel == "admin":
        return f"{alias}.papel_destino='admin'", []
    if papel == "entregador":
        return f"{alias}.papel_destino='entregador' AND {alias}.usuario_id=?", [session["desp_uid"]]
    return f"{alias}.papel_destino='solicitante' AND {alias}.unidade_id=?", [
        session["desp_unidade_id"]
    ]


def login_required_desp(*papeis_permitidos):
    def decorator(f):
        @wraps(f)
        def wrap(*a, **k):
            if not session.get("desp_uid"):
                if request.path.startswith("/despacho/api/"):
                    return jsonify(error="não autenticado"), 401
                return redirect(url_for("despacho.desp_login"))
            if papeis_permitidos and session.get("desp_papel") not in papeis_permitidos:
                return jsonify(error="sem permissão para este papel"), 403
            return f(*a, **k)

        return wrap

    return decorator


def csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


@despacho_bp.context_processor
def contexto_csrf():
    return {"csrf_token": csrf_token}


@despacho_bp.before_request
def proteger_api_contra_csrf():
    if request.method not in METODOS_COM_MUTACAO:
        return None
    if not request.path.startswith("/despacho/api/"):
        return None
    if not session.get("desp_uid"):
        return None

    esperado = session.get(CSRF_SESSION_KEY)
    recebido = request.headers.get(CSRF_HEADER)
    if not esperado or not recebido or not hmac.compare_digest(esperado, recebido):
        return jsonify(error="token CSRF inválido"), 403
    return None


@despacho_bp.route("/login", methods=["GET", "POST"])
@limitar_falhas_login
def desp_login():
    erro = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        senha = request.form.get("senha") or ""
        con = get_db_desp()
        u = con.execute("SELECT * FROM usuarios WHERE username=? AND ativo=1", (username,)).fetchone()
        if not u or not check_password_hash(u["senha_hash"], senha):
            erro = "Usuário ou senha incorretos."
        else:
            session.clear()
            session["desp_uid"] = u["id"]
            session["desp_nome"] = u["nome"]
            session["desp_papel"] = u["papel"]
            session["desp_unidade_id"] = u["unidade_id"]
            session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
            return redirect(url_for("despacho.desp_home"))
        return render_template("despacho/login.html", erro=erro), 401
    return render_template("despacho/login.html", erro=erro)


@despacho_bp.route("/logout")
def desp_logout():
    for k in ("desp_uid", "desp_nome", "desp_papel", "desp_unidade_id", CSRF_SESSION_KEY):
        session.pop(k, None)
    return redirect(url_for("despacho.desp_login"))


@despacho_bp.route("/")
@login_required_desp()
def desp_home():
    destino = {
        "admin": "despacho.desp_admin",
        "solicitante": "despacho.desp_solicitante",
        "entregador": "despacho.desp_entregador",
    }[session["desp_papel"]]
    return redirect(url_for(destino))


@despacho_bp.route("/admin")
@login_required_desp("admin")
def desp_admin():
    return render_template("despacho/admin.html", nome=session["desp_nome"])


@despacho_bp.route("/solicitante")
@login_required_desp("solicitante")
def desp_solicitante():
    con = get_db_desp()
    unidade = con.execute("SELECT nome FROM unidades WHERE id=?", (session["desp_unidade_id"],)).fetchone()
    return render_template(
        "despacho/solicitante.html",
        nome=session["desp_nome"],
        unidade=unidade["nome"] if unidade else "?",
    )


@despacho_bp.route("/entregador")
@login_required_desp("entregador")
def desp_entregador():
    return render_template("despacho/entregador.html", nome=session["desp_nome"])


@despacho_bp.route("/api/unidades", methods=["GET", "POST"])
@login_required_desp()
def api_unidades():
    con = get_db_desp()
    if request.method == "POST":
        if session["desp_papel"] != "admin":
            return jsonify(error="apenas administrador"), 403
        nome = (request.get_json(force=True).get("nome") or "").strip()
        if not nome:
            return jsonify(error="nome obrigatório"), 400
        try:
            con.execute("INSERT INTO unidades(nome) VALUES(?)", (nome,))
            con.commit()
        except sqlite3.IntegrityError:
            return jsonify(error="unidade já cadastrada"), 400
    rows = con.execute("SELECT id, nome FROM unidades ORDER BY nome").fetchall()
    return jsonify([dict(r) for r in rows])


@despacho_bp.route("/api/usuarios", methods=["GET", "POST"])
@login_required_desp("admin")
def api_usuarios():
    con = get_db_desp()
    if request.method == "POST":
        d = request.get_json(force=True)
        nome = (d.get("nome") or "").strip()
        username = (d.get("username") or "").strip()
        senha = d.get("senha") or ""
        papel = d.get("papel")
        unidade_id = d.get("unidade_id")
        codigo_ref = (d.get("codigo_ref") or "").strip()
        tipo_veiculo = _normalizar_veiculo(d.get("tipo_veiculo"))

        if not (nome and username and senha and papel in PAPEIS):
            return jsonify(error="dados incompletos"), 400
        if papel != "admin" and not codigo_ref:
            return jsonify(error="código de referência obrigatório"), 400
        if codigo_ref:
            duplicado = con.execute(
                "SELECT 1 FROM usuarios WHERE codigo_ref=? AND papel!='admin' AND ativo=1 LIMIT 1",
                (codigo_ref,),
            ).fetchone()
            if duplicado:
                return jsonify(error="código de referência já cadastrado"), 400
        if papel == "solicitante" and not unidade_id:
            return jsonify(error="solicitante precisa de uma unidade"), 400
        if papel == "entregador" and tipo_veiculo not in TIPOS_VEICULO:
            return jsonify(error="entregador precisa de tipo de veículo"), 400
        if papel != "solicitante":
            unidade_id = None
        if papel != "entregador":
            tipo_veiculo = None

        try:
            con.execute(
                "INSERT INTO usuarios(nome,username,senha_hash,papel,unidade_id,codigo_ref,tipo_veiculo) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    nome,
                    username,
                    generate_password_hash(senha),
                    papel,
                    unidade_id,
                    codigo_ref or None,
                    tipo_veiculo,
                ),
            )
            con.commit()
        except sqlite3.IntegrityError:
            return jsonify(error="username já existe"), 400

    rows = con.execute(
        """
        SELECT u.id, u.nome, u.username, u.papel, u.unidade_id,
               u.disponivel, u.codigo_ref, u.tipo_veiculo,
               u.indisponibilidade_justificativa, u.indisponibilidade_tipo, u.indisponibilidade_ts,
               un.nome AS unidade_nome,
               CASE WHEN EXISTS(
                   SELECT 1 FROM pedidos p
                   WHERE p.entregador_id=u.id
                     AND p.status IN ('aguardando_entregador','em_rota_retirada','em_rota','despachado','coletado')
               ) THEN 1 ELSE 0 END AS ocupado
        FROM usuarios u LEFT JOIN unidades un ON un.id = u.unidade_id
        WHERE u.papel != 'admin' AND u.ativo = 1
        ORDER BY u.papel, u.nome
        """
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@despacho_bp.route("/api/usuarios/<int:uid>", methods=["PATCH", "DELETE"])
@login_required_desp("admin")
def api_usuario_admin(uid):
    con = get_db_desp()
    usuario = con.execute(
        "SELECT * FROM usuarios WHERE id=? AND papel='entregador' AND ativo=1", (uid,)
    ).fetchone()
    if not usuario:
        return jsonify(error="entregador não encontrado"), 404

    if _entregador_ocupado(con, uid):
        return jsonify(error="entregador está em atendimento"), 400

    if request.method == "DELETE":
        con.execute(
            """
            UPDATE usuarios
            SET ativo=0,
                disponivel=0,
                username=username || '__apagado_' || id,
                codigo_ref=CASE
                    WHEN codigo_ref IS NULL THEN NULL
                    ELSE codigo_ref || '__apagado_' || id
                END
            WHERE id=? AND papel='entregador'
            """,
            (uid,),
        )
        con.commit()
        return jsonify(ok=True, id=uid)

    d = request.get_json(silent=True) or {}
    tipo_veiculo = _normalizar_veiculo(d.get("tipo_veiculo"))
    if tipo_veiculo not in TIPOS_VEICULO:
        return jsonify(error="tipo de veículo inválido"), 400

    con.execute("UPDATE usuarios SET tipo_veiculo=? WHERE id=?", (tipo_veiculo, uid))
    con.commit()
    row = con.execute(
        "SELECT id, nome, username, papel, unidade_id, disponivel, codigo_ref, tipo_veiculo "
        "FROM usuarios WHERE id=?",
        (uid,),
    ).fetchone()
    return jsonify(dict(row))


@despacho_bp.route("/api/logins")
@login_required_desp("admin")
def api_logins():
    con = get_db_desp()
    rows = con.execute(
        """
        SELECT u.id, u.nome, u.username, u.papel, u.unidade_id,
               u.codigo_ref, u.tipo_veiculo, un.nome AS unidade_nome
        FROM usuarios u
        LEFT JOIN unidades un ON un.id = u.unidade_id
        WHERE u.ativo = 1
        ORDER BY CASE u.papel
            WHEN 'admin' THEN 0
            WHEN 'solicitante' THEN 1
            WHEN 'entregador' THEN 2
            ELSE 3
        END, u.nome
        """
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@despacho_bp.route("/api/usuarios/<int:uid>/senha", methods=["POST"])
@login_required_desp("admin")
def api_alterar_senha_usuario(uid):
    con = get_db_desp()
    d = request.get_json(silent=True) or {}
    senha = (d.get("senha") or d.get("nova_senha") or "").strip()
    if len(senha) < 4:
        return jsonify(error="senha deve ter pelo menos 4 caracteres"), 400

    usuario = con.execute(
        "SELECT id, username FROM usuarios WHERE id=? AND ativo=1", (uid,)
    ).fetchone()
    if not usuario:
        return jsonify(error="login não encontrado"), 404

    con.execute(
        "UPDATE usuarios SET senha_hash=? WHERE id=?",
        (generate_password_hash(senha), uid),
    )
    con.commit()
    return jsonify(ok=True, id=usuario["id"], username=usuario["username"])


@despacho_bp.route("/api/disponibilidade", methods=["GET", "POST"])
@login_required_desp("entregador")
def api_disponibilidade():
    con = get_db_desp()
    uid = session["desp_uid"]
    ocupado = _entregador_ocupado(con, uid)
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        valor = d.get("disponivel")
        if not isinstance(valor, bool):
            return jsonify(error="disponibilidade inválida"), 400
        if ocupado:
            return jsonify(error="não é possível alterar a disponibilidade durante uma entrega"), 400
        if valor is False:
            justificativa = (d.get("justificativa") or "").strip()
            tipo = (d.get("tipo") or "").strip()
            if not justificativa or tipo not in TIPOS_INDISPONIBILIDADE:
                return jsonify(error="justificativa e tipo de indisponibilidade são obrigatórios"), 400
            ts = agora_ms()
            con.execute(
                "INSERT INTO indisponibilidades_entregador(entregador_id,tipo,justificativa,ts) "
                "VALUES(?,?,?,?)",
                (uid, tipo, justificativa, ts),
            )
            con.execute(
                "UPDATE usuarios SET disponivel=0, indisponibilidade_justificativa=?, "
                "indisponibilidade_tipo=?, indisponibilidade_ts=? WHERE id=?",
                (justificativa, tipo, ts, uid),
            )
        else:
            con.execute(
                "UPDATE usuarios SET disponivel=1, indisponibilidade_justificativa=NULL, "
                "indisponibilidade_tipo=NULL, indisponibilidade_ts=NULL WHERE id=?",
                (uid,),
            )
        con.commit()
    row = con.execute(
        "SELECT disponivel, indisponibilidade_justificativa, indisponibilidade_tipo, "
        "indisponibilidade_ts FROM usuarios WHERE id=?",
        (uid,),
    ).fetchone()
    return jsonify(
        disponivel=bool(row["disponivel"]),
        ocupado=_entregador_ocupado(con, uid),
        indisponibilidade_justificativa=row["indisponibilidade_justificativa"],
        indisponibilidade_tipo=row["indisponibilidade_tipo"],
        indisponibilidade_ts=row["indisponibilidade_ts"],
    )


@despacho_bp.route("/api/operadores", methods=["GET", "POST"])
@login_required_desp("solicitante")
def api_operadores():
    con = get_db_desp()
    unidade_id = session.get("desp_unidade_id")
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        operador = _buscar_ou_criar_operador(con, unidade_id, d.get("nome"))
        if not operador:
            return jsonify(error="nome obrigatório"), 400
        con.commit()
        return jsonify(_operador_linha(operador))

    rows = con.execute(
        """
        SELECT id, unidade_id, nome, codigo, ativo, criado_em
        FROM operadores_solicitante
        WHERE unidade_id=? AND ativo=1
        ORDER BY nome
        """,
        (unidade_id,),
    ).fetchall()
    return jsonify([_operador_linha(row) for row in rows])


@despacho_bp.route("/api/tipos-exame")
@login_required_desp("solicitante")
def api_tipos_exame():
    con = get_db_desp()
    return jsonify(_tipos_exame_da_unidade(con, session["desp_unidade_id"]))


@despacho_bp.route("/api/notificacoes")
@login_required_desp("admin", "solicitante", "entregador")
def api_notificacoes():
    con = get_db_desp()
    where, params = _filtro_notificacoes_sessao("n")
    total_nao_lidas = con.execute(
        f"SELECT COUNT(*) AS n FROM notificacoes n WHERE {where} AND n.lida=0",
        params,
    ).fetchone()["n"]
    rows = con.execute(
        f"""
        SELECT n.*, p.protocolo
        FROM notificacoes n
        LEFT JOIN pedidos p ON p.id = n.pedido_id
        WHERE {where} AND n.lida=0
        ORDER BY n.criado_em DESC, n.id DESC
        LIMIT 40
        """,
        params,
    ).fetchall()
    return jsonify(
        nao_lidas=total_nao_lidas,
        notificacoes=[_notificacao_linha(row) for row in rows],
    )


@despacho_bp.route("/api/notificacoes/<int:nid>/lida", methods=["POST"])
@login_required_desp("admin", "solicitante", "entregador")
def api_notificacao_lida(nid):
    con = get_db_desp()
    where, params = _filtro_notificacoes_sessao("n")
    row = con.execute(
        f"SELECT n.id FROM notificacoes n WHERE n.id=? AND {where}",
        (nid, *params),
    ).fetchone()
    if not row:
        return jsonify(error="notificação não encontrada"), 404
    con.execute("UPDATE notificacoes SET lida=1, lida_em=? WHERE id=?", (agora_ms(), nid))
    con.commit()
    return jsonify(ok=True)


@despacho_bp.route("/api/pedidos", methods=["GET", "POST"])
@login_required_desp()
def api_pedidos():
    con = get_db_desp()
    papel = session["desp_papel"]

    if request.method == "POST":
        if papel != "solicitante":
            return jsonify(error="apenas solicitantes abrem pedidos"), 403
        d = request.get_json(force=True)
        destino_id = d.get("destino_id")
        urgencia = d.get("urgencia")
        tipo_veiculo = _normalizar_veiculo(d.get("tipo_veiculo"), "")
        origem_id = session["desp_unidade_id"]
        operador = _buscar_ou_criar_operador(con, origem_id, d.get("operador_nome"))
        if not operador:
            return jsonify(error="nome de quem solicita é obrigatório"), 400
        if urgencia not in URGENCIAS:
            return jsonify(error="urgência inválida"), 400
        if tipo_veiculo not in TIPOS_VEICULO:
            return jsonify(error="tipo de veículo obrigatório"), 400
        if not destino_id or int(destino_id) == origem_id:
            return jsonify(error="destino inválido"), 400
        tipo = _resolver_tipo_pedido(con, origem_id, d.get("tipo"), d.get("tipo_outro"))
        if not tipo:
            return jsonify(error="tipo de coleta inválido"), 400
        cur = con.execute(
            "INSERT INTO pedidos(origem_id,destino_id,tipo,urgencia,tipo_veiculo,sla_limite_min,"
            "status,criado_por,operador_id,ts_solicitado) VALUES(?,?,?,?,?,?, 'solicitado', ?, ?, ?)",
            (
                origem_id,
                destino_id,
                tipo,
                urgencia,
                tipo_veiculo,
                _sla_limite_min(urgencia),
                session["desp_uid"],
                operador["id"],
                agora_ms(),
            ),
        )
        pid = cur.lastrowid
        con.execute("UPDATE pedidos SET protocolo=? WHERE id=?", (_protocolo(pid), pid))
        pedido = _pedido_ou_404(con, pid)
        _notificar_admin_novo_pedido(con, pedido)
        con.commit()
        return jsonify(linha_pedido(con, pedido))

    if papel == "admin":
        rows = con.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
    elif papel == "solicitante":
        uid = session["desp_unidade_id"]
        rows = con.execute(
            "SELECT * FROM pedidos WHERE origem_id=? ORDER BY id DESC", (uid,)
        ).fetchall()
    else:
        placeholders = _status_placeholders(STATUS_ATIVOS_ENTREGADOR)
        rows = con.execute(
            f"SELECT * FROM pedidos WHERE entregador_id=? AND status IN ({placeholders}) ORDER BY id DESC",
            (session["desp_uid"], *STATUS_ATIVOS_ENTREGADOR),
        ).fetchall()
    return jsonify([linha_pedido(con, r) for r in rows])


@despacho_bp.route("/api/pedidos/<int:pid>/despachar", methods=["POST"])
@login_required_desp("admin")
def api_despachar(pid):
    con = get_db_desp()
    r = _pedido_ou_404(con, pid)
    if not r:
        return jsonify(error="pedido não encontrado"), 404
    if r["status"] != "solicitado":
        return jsonify(error="pedido não está mais aguardando despacho"), 400
    entregador_id = request.get_json(force=True).get("entregador_id")
    e = con.execute(
        "SELECT * FROM usuarios WHERE id=? AND papel='entregador' AND ativo=1", (entregador_id,)
    ).fetchone()
    if not e:
        return jsonify(error="entregador inválido"), 400
    if not e["disponivel"] or _entregador_ocupado(con, entregador_id):
        return jsonify(error="entregador indisponível ou em outra entrega"), 400
    if _normalizar_veiculo(e["tipo_veiculo"], "moto") != _normalizar_veiculo(
        r["tipo_veiculo"], "moto"
    ):
        return jsonify(error="entregador incompatível com o veículo solicitado"), 400
    agora = agora_ms()
    con.execute(
        "UPDATE pedidos SET status='aguardando_entregador', entregador_id=?, "
        "ts_aceito_admin=? WHERE id=?",
        (entregador_id, agora, pid),
    )
    con.execute("UPDATE usuarios SET disponivel=0 WHERE id=?", (entregador_id,))
    pedido = _pedido_ou_404(con, pid)
    _notificar_despacho(con, pedido)
    con.commit()
    return jsonify(linha_pedido(con, pedido))


@despacho_bp.route("/api/pedidos/<int:pid>/aceitar", methods=["POST"])
@login_required_desp("entregador")
def api_aceitar(pid):
    con = get_db_desp()
    r = _pedido_ou_404(con, pid)
    if not r:
        return jsonify(error="pedido não encontrado"), 404
    if r["entregador_id"] != session["desp_uid"]:
        return jsonify(error="pedido não é seu"), 403
    if r["status"] != "aguardando_entregador":
        return jsonify(error="estado inválido para aceite"), 400
    con.execute(
        "UPDATE pedidos SET status='em_rota_retirada', ts_aceito_entregador=? WHERE id=?",
        (agora_ms(), pid),
    )
    pedido = _pedido_ou_404(con, pid)
    _notificar_entregador_a_caminho(con, pedido)
    con.commit()
    return jsonify(linha_pedido(con, pedido))


@despacho_bp.route("/api/pedidos/<int:pid>/retirada", methods=["POST"])
@login_required_desp("entregador")
def api_retirada(pid):
    con = get_db_desp()
    r = _pedido_ou_404(con, pid)
    if not r:
        return jsonify(error="pedido não encontrado"), 404
    if r["entregador_id"] != session["desp_uid"]:
        return jsonify(error="pedido não é seu"), 403
    if r["status"] not in ("em_rota_retirada", "em_rota"):
        return jsonify(error="estado inválido para retirada"), 400
    agora = agora_ms()
    con.execute(
        "UPDATE pedidos SET status='despachado', ts_coletado=?, ts_despachado=? WHERE id=?",
        (agora, agora, pid),
    )
    pedido = _pedido_ou_404(con, pid)
    _notificar_retirada(con, pedido)
    con.commit()
    return jsonify(linha_pedido(con, pedido))


@despacho_bp.route("/api/pedidos/<int:pid>/entrega", methods=["POST"])
@login_required_desp("entregador")
def api_entrega(pid):
    con = get_db_desp()
    r = _pedido_ou_404(con, pid)
    if not r:
        return jsonify(error="pedido não encontrado"), 404
    if r["entregador_id"] != session["desp_uid"]:
        return jsonify(error="pedido não é seu"), 403
    if r["status"] not in ("despachado", "coletado"):
        return jsonify(error="estado inválido para entrega"), 400
    d = request.get_json(silent=True) or {}
    sla = _sla_do_pedido(r)
    justificativa = (d.get("justificativa_atraso") or "").strip()
    if sla["atrasado"] and not (justificativa or r["justificativa_atraso"]):
        return jsonify(error="justificativa de atraso obrigatória"), 400
    agora = agora_ms()
    con.execute(
        "UPDATE pedidos SET status='entregue', ts_entregue=?, justificativa_atraso=COALESCE(?, justificativa_atraso) "
        "WHERE id=?",
        (agora, justificativa or None, pid),
    )
    con.execute("UPDATE usuarios SET disponivel=1 WHERE id=?", (session["desp_uid"],))
    pedido = _pedido_ou_404(con, pid)
    _notificar_entrega(con, pedido)
    con.commit()
    return jsonify(linha_pedido(con, pedido))


@despacho_bp.route("/api/pedidos/<int:pid>/localizacoes", methods=["GET", "POST"])
@login_required_desp()
def api_localizacoes(pid):
    con = get_db_desp()
    r = _pedido_ou_404(con, pid)
    if not r:
        return jsonify(error="pedido não encontrado"), 404

    papel = session["desp_papel"]
    uid = session["desp_uid"]
    if request.method == "POST":
        if papel != "entregador" or r["entregador_id"] != uid:
            return jsonify(error="somente o entregador atribuído pode enviar localização"), 403
        if r["status"] not in STATUS_RASTREAMENTO:
            return jsonify(error="rastreamento não está ativo para este pedido"), 400
        d = request.get_json(silent=True) or {}
        try:
            latitude = float(d.get("latitude"))
            longitude = float(d.get("longitude"))
            precisao = None if d.get("precisao") is None else float(d.get("precisao"))
        except (TypeError, ValueError):
            return jsonify(error="coordenadas inválidas"), 400
        if (
            not math.isfinite(latitude)
            or not -90 <= latitude <= 90
            or not math.isfinite(longitude)
            or not -180 <= longitude <= 180
            or precisao is not None
            and (not math.isfinite(precisao) or precisao < 0)
        ):
            return jsonify(error="coordenadas inválidas"), 400
        ts = agora_ms()
        cur = con.execute(
            "INSERT INTO localizacoes_pedido(pedido_id,entregador_id,latitude,longitude,precisao,ts) "
            "VALUES(?,?,?,?,?,?)",
            (pid, uid, latitude, longitude, precisao, ts),
        )
        con.commit()
        return jsonify(id=cur.lastrowid, latitude=latitude, longitude=longitude, precisao=precisao, ts=ts)

    autorizado = papel == "admin"
    if papel == "entregador":
        autorizado = r["entregador_id"] == uid
    elif papel == "solicitante":
        unidade_id = session.get("desp_unidade_id")
        autorizado = unidade_id == r["origem_id"]
    if not autorizado:
        return jsonify(error="sem permissão para consultar esta rota"), 403
    rows = con.execute(
        "SELECT id,latitude,longitude,precisao,ts FROM localizacoes_pedido "
        "WHERE pedido_id=? ORDER BY ts,id",
        (pid,),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@despacho_bp.route("/api/pedidos/<int:pid>/cancelar", methods=["POST"])
@login_required_desp("admin", "solicitante")
def api_cancelar(pid):
    con = get_db_desp()
    r = _pedido_ou_404(con, pid)
    if not r:
        return jsonify(error="pedido não encontrado"), 404
    if session["desp_papel"] == "solicitante":
        if r["origem_id"] != session["desp_unidade_id"] or r["status"] != "solicitado":
            return jsonify(error="não é possível cancelar este pedido"), 403
    motivo = (request.get_json(silent=True) or {}).get("motivo")
    if r["status"] in ("entregue", "cancelado"):
        return jsonify(error="pedido já foi finalizado"), 400
    con.execute(
        "UPDATE pedidos SET status='cancelado', motivo_cancelamento=?, ts_cancelado=? WHERE id=?",
        (motivo, agora_ms(), pid),
    )
    if r["entregador_id"]:
        con.execute("UPDATE usuarios SET disponivel=1 WHERE id=?", (r["entregador_id"],))
    con.commit()
    return jsonify(linha_pedido(con, _pedido_ou_404(con, pid)))


@despacho_bp.route("/api/relatorios/resumo-diario")
@login_required_desp("admin")
def api_relatorio_resumo_diario():
    con = get_db_desp()
    inicio, fim = _range_dia_ms()
    rows = con.execute(
        "SELECT * FROM pedidos WHERE ts_solicitado BETWEEN ? AND ?",
        (inicio, fim),
    ).fetchall()
    total = len(rows)
    entregues = sum(1 for r in rows if r["status"] == "entregue")
    cancelados = sum(1 for r in rows if r["status"] == "cancelado")
    em_andamento = sum(1 for r in rows if r["status"] in STATUS_EM_ANDAMENTO)
    fora_sla = sum(1 for r in rows if _sla_do_pedido(r)["atrasado"])
    return jsonify(
        total=total,
        entregues=entregues,
        em_andamento=em_andamento,
        cancelados=cancelados,
        fora_sla=fora_sla,
    )


@despacho_bp.route("/api/relatorios/inconformidades")
@login_required_desp("admin")
def api_relatorio_inconformidades():
    con = get_db_desp()
    hoje = datetime.now(TZ)
    ano = int(request.args.get("ano") or hoje.year)
    mes = int(request.args.get("mes") or hoje.month)
    inicio, fim = _range_mes_ms(ano, mes)
    rows = con.execute(
        """
        SELECT p.*, ent.nome AS entregador_nome, sol.nome AS solicitante_nome
        FROM pedidos p
        LEFT JOIN usuarios ent ON ent.id = p.entregador_id
        LEFT JOIN usuarios sol ON sol.id = p.criado_por
        WHERE p.ts_solicitado BETWEEN ? AND ?
          AND p.status='entregue'
          AND p.ts_entregue IS NOT NULL
        ORDER BY p.ts_solicitado DESC
        """,
        (inicio, fim),
    ).fetchall()
    inconformidades = []
    for r in rows:
        sla = _sla_do_pedido(r)
        if not sla["atrasado"]:
            continue
        inconformidades.append(
            {
                "protocolo": r["protocolo"] or _protocolo(r["id"]),
                "entregador": r["entregador_nome"],
                "solicitante": r["solicitante_nome"],
                "tempo_excedido_ms": sla["excedido_ms"],
                "tempo_excedido": _fmt_duracao(sla["excedido_ms"]),
                "justificativa_atraso": r["justificativa_atraso"],
            }
        )
    return jsonify(inconformidades)


@despacho_bp.route("/api/chat/resumo")
@login_required_desp("admin")
def api_chat_resumo():
    con = get_db_desp()
    rows = con.execute(
        """
        SELECT c.unidade_id, un.nome AS unidade,
               COUNT(*) AS total,
               SUM(CASE WHEN c.remetente_papel != 'admin' THEN 1 ELSE 0 END) AS recebidas,
               MAX(c.ts) AS ultimo_ts
        FROM chat_mensagens c
        LEFT JOIN unidades un ON un.id = c.unidade_id
        WHERE c.unidade_id IS NOT NULL
        GROUP BY c.unidade_id, un.nome
        ORDER BY ultimo_ts DESC, unidade COLLATE NOCASE
        """
    ).fetchall()
    return jsonify(
        [
            {
                "unidade_id": row["unidade_id"],
                "unidade": row["unidade"] or "Unidade",
                "total": int(row["total"] or 0),
                "recebidas": int(row["recebidas"] or 0),
                "ultimo_ts": row["ultimo_ts"],
            }
            for row in rows
        ]
    )


@despacho_bp.route("/api/chat", methods=["GET", "POST"])
@login_required_desp("admin", "solicitante")
def api_chat():
    con = get_db_desp()
    papel = session["desp_papel"]

    if request.method == "POST":
        d = request.get_json(force=True)
        texto = (d.get("texto") or "").strip()
        if not texto:
            return jsonify(error="mensagem vazia"), 400
        if papel == "solicitante":
            solicitante_id = session["desp_uid"]
            unidade_id = session["desp_unidade_id"]
        else:
            unidade_id = d.get("unidade_id")
            solicitante_id = d.get("solicitante_id")
            if not unidade_id and solicitante_id:
                solicitante_ref = con.execute(
                    """
                    SELECT unidade_id
                    FROM usuarios
                    WHERE id=? AND papel='solicitante' AND ativo=1
                    """,
                    (solicitante_id,),
                ).fetchone()
                if solicitante_ref:
                    unidade_id = solicitante_ref["unidade_id"]

            unidade = con.execute("SELECT id FROM unidades WHERE id=?", (unidade_id,)).fetchone()
            if not unidade:
                return jsonify(error="unidade inválida"), 400

            solicitante = con.execute(
                """
                SELECT id
                FROM usuarios
                WHERE unidade_id=? AND papel='solicitante' AND ativo=1
                ORDER BY id
                LIMIT 1
                """,
                (unidade_id,),
            ).fetchone()
            if not solicitante:
                return jsonify(error="unidade sem solicitante ativo"), 400
            solicitante_id = solicitante["id"]
        cur = con.execute(
            "INSERT INTO chat_mensagens(solicitante_id,unidade_id,remetente_id,remetente_papel,texto,ts) "
            "VALUES(?,?,?,?,?,?)",
            (solicitante_id, unidade_id, session["desp_uid"], papel, texto, agora_ms()),
        )
        _notificar_chat(con, papel, unidade_id, texto)
        con.commit()
        row = con.execute(
            """
            SELECT c.*, u.nome AS remetente_nome, un.nome AS unidade
            FROM chat_mensagens c
            JOIN usuarios u ON u.id = c.remetente_id
            LEFT JOIN unidades un ON un.id = c.unidade_id
            WHERE c.id=?
            """,
            (cur.lastrowid,),
        ).fetchone()
        return jsonify(_chat_linha(row))

    params = []
    where = []
    if papel == "solicitante":
        where.append("c.unidade_id=?")
        params.append(session["desp_unidade_id"])
    elif request.args.get("unidade_id"):
        where.append("c.unidade_id=?")
        params.append(request.args.get("unidade_id"))
    elif request.args.get("solicitante_id"):
        solicitante_ref = con.execute(
            """
            SELECT unidade_id
            FROM usuarios
            WHERE id=? AND papel='solicitante' AND ativo=1
            """,
            (request.args.get("solicitante_id"),),
        ).fetchone()
        if solicitante_ref:
            where.append("c.unidade_id=?")
            params.append(solicitante_ref["unidade_id"])
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = con.execute(
        f"""
        SELECT c.*, u.nome AS remetente_nome, un.nome AS unidade
        FROM chat_mensagens c
        JOIN usuarios u ON u.id = c.remetente_id
        LEFT JOIN unidades un ON un.id = c.unidade_id
        {where_sql}
        ORDER BY c.ts, c.id
        """,
        params,
    ).fetchall()
    return jsonify([_chat_linha(row) for row in rows])
