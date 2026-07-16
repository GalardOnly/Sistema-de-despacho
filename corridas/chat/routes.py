"""Rotas do chat interno entre unidades e administração."""

from flask import jsonify, request, session

from ..auth.services import login_required_desp
from ..config import LIMITE_CHAT_RETORNO, LIMITE_TEXTO_CHAT
from ..database import get_db_desp
from ..extensions import despacho_bp
from ..notificacoes.services import _notificar_chat
from ..pedidos.services import _limite_consulta, _parametro_consulta_positivo, agora_ms
from .services import _chat_linha


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
        ORDER BY ultimo_ts DESC, LOWER(unidade)
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
        if len(texto) > LIMITE_TEXTO_CHAT:
            return jsonify(error="mensagem excede o limite permitido"), 400
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
    antes_id = _parametro_consulta_positivo("antes_id")
    if antes_id:
        where.append("c.id<?")
        params.append(antes_id)
    limite = _limite_consulta(LIMITE_CHAT_RETORNO, LIMITE_CHAT_RETORNO)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = con.execute(
        f"SELECT c.*, u.nome AS remetente_nome, un.nome AS unidade "  # nosec B608
        "FROM chat_mensagens c JOIN usuarios u ON u.id = c.remetente_id "
        "LEFT JOIN unidades un ON un.id = c.unidade_id "
        f"{where_sql} ORDER BY c.id DESC LIMIT ?",
        (*params, limite),
    ).fetchall()
    rows = list(reversed(rows))
    return jsonify([_chat_linha(row) for row in rows])
