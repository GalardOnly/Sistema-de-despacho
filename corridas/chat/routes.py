"""Rotas do chat interno entre unidades e administração."""

from flask import jsonify, request, session

from ..auth.services import login_required_desp
from ..config import LIMITE_CHAT_RETORNO, LIMITE_RESUMO_CHAT_RETORNO, LIMITE_TEXTO_CHAT
from ..database import get_db_desp
from ..extensions import despacho_bp
from ..notificacoes.services import _notificar_chat
from ..pedidos.services import (
    _inteiro_positivo,
    _limite_consulta,
    _parametro_consulta_positivo,
    _resposta_paginada,
    agora_ms,
)
from .services import _chat_linha
from ..validation import dados_json, texto as texto_recebido


@despacho_bp.route("/api/chat/resumo")
@login_required_desp("admin")
def api_chat_resumo():
    con = get_db_desp()
    limite = _limite_consulta(
        LIMITE_RESUMO_CHAT_RETORNO, LIMITE_RESUMO_CHAT_RETORNO
    )
    antes_id = _parametro_consulta_positivo("antes_id")
    cursor = antes_id or 0
    rows = con.execute(
        """
        SELECT c.unidade_id, un.nome AS unidade,
               COUNT(*) AS total,
               SUM(CASE WHEN c.remetente_papel != 'admin' THEN 1 ELSE 0 END) AS recebidas,
               MAX(c.ts) AS ultimo_ts
        FROM chat_mensagens c
        LEFT JOIN unidades un ON un.id = c.unidade_id
        WHERE c.unidade_id IS NOT NULL
          AND (?=0 OR c.unidade_id<?)
        GROUP BY c.unidade_id, un.nome
        ORDER BY c.unidade_id DESC
        LIMIT ?
        """,
        (cursor, cursor, limite + 1),
    ).fetchall()
    return _resposta_paginada(
        rows,
        limite,
        lambda row: {
            "unidade_id": row["unidade_id"],
            "unidade": row["unidade"] or "Unidade",
            "total": int(row["total"] or 0),
            "recebidas": int(row["recebidas"] or 0),
            "ultimo_ts": row["ultimo_ts"],
        },
        campo_cursor="unidade_id",
    )


@despacho_bp.route("/api/chat", methods=["GET", "POST"])
@login_required_desp("admin", "solicitante")
def api_chat():
    con = get_db_desp()
    papel = session["desp_papel"]

    if request.method == "POST":
        d = dados_json()
        mensagem = texto_recebido(d.get("texto")).strip()
        if not mensagem:
            return jsonify(error="mensagem vazia"), 400
        if len(mensagem) > LIMITE_TEXTO_CHAT:
            return jsonify(error="mensagem excede o limite permitido"), 400
        if papel == "solicitante":
            solicitante_id = session["desp_uid"]
            unidade_id = session["desp_unidade_id"]
        else:
            unidade_id = _inteiro_positivo(d.get("unidade_id"))
            solicitante_id = _inteiro_positivo(d.get("solicitante_id"))
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
            (solicitante_id, unidade_id, session["desp_uid"], papel, mensagem, agora_ms()),
        )
        _notificar_chat(con, papel, unidade_id, mensagem)
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

    unidade_filtro = None
    if papel == "solicitante":
        unidade_filtro = session["desp_unidade_id"]
    elif request.args.get("unidade_id"):
        unidade_id = _inteiro_positivo(request.args.get("unidade_id"))
        if not unidade_id:
            return jsonify(error="unidade inválida"), 400
        unidade_filtro = unidade_id
    elif request.args.get("solicitante_id"):
        solicitante_id = _inteiro_positivo(request.args.get("solicitante_id"))
        if not solicitante_id:
            return jsonify(error="solicitante inválido"), 400
        solicitante_ref = con.execute(
            """
            SELECT unidade_id
            FROM usuarios
            WHERE id=? AND papel='solicitante' AND ativo=1
            """,
            (solicitante_id,),
        ).fetchone()
        if not solicitante_ref:
            return jsonify(error="solicitante inválido"), 400
        unidade_filtro = solicitante_ref["unidade_id"]
    antes_id = _parametro_consulta_positivo("antes_id")
    unidade_filtro = unidade_filtro or 0
    cursor = antes_id or 0
    limite = _limite_consulta(LIMITE_CHAT_RETORNO, LIMITE_CHAT_RETORNO)
    rows = con.execute(
        """
        SELECT c.*, u.nome AS remetente_nome, un.nome AS unidade
        FROM chat_mensagens c
        JOIN usuarios u ON u.id = c.remetente_id
        LEFT JOIN unidades un ON un.id = c.unidade_id
        WHERE (?=0 OR c.unidade_id=?)
          AND (?=0 OR c.id<?)
        ORDER BY c.id DESC LIMIT ?
        """,
        (
            unidade_filtro,
            unidade_filtro,
            cursor,
            cursor,
            limite + 1,
        ),
    ).fetchall()
    return _resposta_paginada(rows, limite, _chat_linha, inverter=True)
