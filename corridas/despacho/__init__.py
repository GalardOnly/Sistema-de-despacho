"""Rotas do sistema de despacho de coletas."""

import calendar
import math
import os
import sqlite3
import time
from datetime import datetime, time as dt_time
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


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

ADMIN_SENHA_INICIAL = os.environ.get("DESPACHO_ADMIN_SENHA_INICIAL", "mudar123")

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


@despacho_bp.teardown_app_request
def close_db_desp(exc):
    d = g.pop("db_desp", None)
    if d is not None:
        d.close()


def _ensure_column(con, table, column, definition):
    columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
            remetente_id   INTEGER NOT NULL REFERENCES usuarios(id),
            remetente_papel TEXT NOT NULL,
            texto          TEXT NOT NULL,
            ts             INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_localizacoes_pedido_ts
            ON localizacoes_pedido(pedido_id, ts);
        CREATE INDEX IF NOT EXISTS idx_chat_solicitante_ts
            ON chat_mensagens(solicitante_id, ts);
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
    ):
        _ensure_column(con, "pedidos", column, definition)

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
    con.execute("UPDATE pedidos SET tipo_veiculo='moto' WHERE tipo_veiculo IS NULL OR tipo_veiculo=''")
    for urgencia, meta in URGENCIA_META.items():
        con.execute(
            "UPDATE pedidos SET sla_limite_min=? WHERE urgencia=? AND sla_limite_min IS NULL",
            (meta["sla_min"], urgencia),
        )
    for row in con.execute("SELECT id FROM pedidos WHERE protocolo IS NULL OR protocolo=''").fetchall():
        con.execute("UPDATE pedidos SET protocolo=? WHERE id=?", (_protocolo(row[0]), row[0]))

    existe = con.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()[0]
    if existe == 0:
        con.execute(
            "INSERT INTO usuarios(nome,username,senha_hash,papel,unidade_id) VALUES(?,?,?,?,?)",
            ("Administrador", "admin", generate_password_hash(ADMIN_SENHA_INICIAL), "admin", None),
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
        "tipo_veiculo": d.get("tipo_veiculo") or "moto",
        "sla_limite_min": d.get("sla_limite_min") or _sla_limite_min(d["urgencia"]),
        "sla": _sla_do_pedido(d),
        "status": d["status"],
        "entregador_id": d.get("entregador_id"),
        "entregador": entregador,
        "solicitante_id": d.get("criado_por"),
        "solicitante": solicitante,
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
        "remetente_id": row["remetente_id"],
        "remetente_nome": row["remetente_nome"],
        "remetente_papel": row["remetente_papel"],
        "unidade": row["unidade"],
        "texto": row["texto"],
        "ts": row["ts"],
    }


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


@despacho_bp.route("/login", methods=["GET", "POST"])
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
            session["desp_uid"] = u["id"]
            session["desp_nome"] = u["nome"]
            session["desp_papel"] = u["papel"]
            session["desp_unidade_id"] = u["unidade_id"]
            return redirect(url_for("despacho.desp_home"))
    return render_template("despacho/login.html", erro=erro)


@despacho_bp.route("/logout")
def desp_logout():
    for k in ("desp_uid", "desp_nome", "desp_papel", "desp_unidade_id"):
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
        tipo_veiculo = (d.get("tipo_veiculo") or "").strip().lower() or None

        if not (nome and username and senha and papel in PAPEIS):
            return jsonify(error="dados incompletos"), 400
        if papel != "admin" and not codigo_ref:
            return jsonify(error="código de referência obrigatório"), 400
        if codigo_ref:
            duplicado = con.execute(
                "SELECT 1 FROM usuarios WHERE codigo_ref=? AND papel!='admin' LIMIT 1",
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
        WHERE u.papel != 'admin'
        ORDER BY u.papel, u.nome
        """
    ).fetchall()
    return jsonify([dict(r) for r in rows])


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
        tipo = d.get("tipo")
        urgencia = d.get("urgencia")
        tipo_veiculo = (d.get("tipo_veiculo") or "").strip().lower()
        origem_id = session["desp_unidade_id"]
        if tipo not in TIPOS_EXAME or urgencia not in URGENCIAS:
            return jsonify(error="tipo ou urgência inválidos"), 400
        if tipo_veiculo not in TIPOS_VEICULO:
            return jsonify(error="tipo de veículo obrigatório"), 400
        if not destino_id or int(destino_id) == origem_id:
            return jsonify(error="destino inválido"), 400
        cur = con.execute(
            "INSERT INTO pedidos(origem_id,destino_id,tipo,urgencia,tipo_veiculo,sla_limite_min,"
            "status,criado_por,ts_solicitado) VALUES(?,?,?,?,?,?, 'solicitado', ?, ?)",
            (
                origem_id,
                destino_id,
                tipo,
                urgencia,
                tipo_veiculo,
                _sla_limite_min(urgencia),
                session["desp_uid"],
                agora_ms(),
            ),
        )
        pid = cur.lastrowid
        con.execute("UPDATE pedidos SET protocolo=? WHERE id=?", (_protocolo(pid), pid))
        con.commit()
        return jsonify(linha_pedido(con, _pedido_ou_404(con, pid)))

    if papel == "admin":
        rows = con.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
    elif papel == "solicitante":
        uid = session["desp_unidade_id"]
        rows = con.execute(
            "SELECT * FROM pedidos WHERE origem_id=? OR destino_id=? ORDER BY id DESC", (uid, uid)
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
    if (e["tipo_veiculo"] or "").lower() != (r["tipo_veiculo"] or "moto").lower():
        return jsonify(error="entregador incompatível com o veículo solicitado"), 400
    agora = agora_ms()
    con.execute(
        "UPDATE pedidos SET status='aguardando_entregador', entregador_id=?, "
        "ts_aceito_admin=? WHERE id=?",
        (entregador_id, agora, pid),
    )
    con.execute("UPDATE usuarios SET disponivel=0 WHERE id=?", (entregador_id,))
    con.commit()
    return jsonify(linha_pedido(con, _pedido_ou_404(con, pid)))


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
    con.commit()
    return jsonify(linha_pedido(con, _pedido_ou_404(con, pid)))


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
    con.commit()
    return jsonify(linha_pedido(con, _pedido_ou_404(con, pid)))


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
    con.commit()
    return jsonify(linha_pedido(con, _pedido_ou_404(con, pid)))


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
        autorizado = unidade_id in (r["origem_id"], r["destino_id"])
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
        else:
            solicitante_id = d.get("solicitante_id")
            solicitante = con.execute(
                "SELECT 1 FROM usuarios WHERE id=? AND papel='solicitante' AND ativo=1",
                (solicitante_id,),
            ).fetchone()
            if not solicitante:
                return jsonify(error="solicitante inválido"), 400
        cur = con.execute(
            "INSERT INTO chat_mensagens(solicitante_id,remetente_id,remetente_papel,texto,ts) "
            "VALUES(?,?,?,?,?)",
            (solicitante_id, session["desp_uid"], papel, texto, agora_ms()),
        )
        con.commit()
        row = con.execute(
            """
            SELECT c.*, u.nome AS remetente_nome, un.nome AS unidade
            FROM chat_mensagens c
            JOIN usuarios u ON u.id = c.remetente_id
            LEFT JOIN usuarios sol ON sol.id = c.solicitante_id
            LEFT JOIN unidades un ON un.id = sol.unidade_id
            WHERE c.id=?
            """,
            (cur.lastrowid,),
        ).fetchone()
        return jsonify(_chat_linha(row))

    params = []
    where = []
    if papel == "solicitante":
        where.append("c.solicitante_id=?")
        params.append(session["desp_uid"])
    elif request.args.get("solicitante_id"):
        where.append("c.solicitante_id=?")
        params.append(request.args.get("solicitante_id"))
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = con.execute(
        f"""
        SELECT c.*, u.nome AS remetente_nome, un.nome AS unidade
        FROM chat_mensagens c
        JOIN usuarios u ON u.id = c.remetente_id
        LEFT JOIN usuarios sol ON sol.id = c.solicitante_id
        LEFT JOIN unidades un ON un.id = sol.unidade_id
        {where_sql}
        ORDER BY c.ts, c.id
        """,
        params,
    ).fetchall()
    return jsonify([_chat_linha(row) for row in rows])
