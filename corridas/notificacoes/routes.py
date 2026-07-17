"""Rotas da central de notificações."""

from flask import jsonify, session

from ..auth.services import login_required_desp
from ..database import get_db_desp
from ..extensions import despacho_bp
from ..pedidos.services import agora_ms
from .services import _notificacao_linha


def _parametros_destinatario():
    return (
        session["desp_papel"],
        session.get("desp_uid"),
        session.get("desp_unidade_id"),
    )


@despacho_bp.route("/api/notificacoes")
@login_required_desp("admin", "solicitante", "entregador")
def api_notificacoes():
    con = get_db_desp()
    papel, usuario_id, unidade_id = _parametros_destinatario()
    params = (papel, papel, usuario_id, papel, unidade_id)
    total_nao_lidas = con.execute(
        """
        SELECT COUNT(*) AS n FROM notificacoes n
        WHERE (
            (?='admin' AND n.papel_destino='admin')
            OR (?='entregador' AND n.papel_destino='entregador' AND n.usuario_id=?)
            OR (?='solicitante' AND n.papel_destino='solicitante' AND n.unidade_id=?)
        ) AND n.lida=0
        """,
        params,
    ).fetchone()["n"]
    rows = con.execute(
        """
        SELECT n.*, p.protocolo FROM notificacoes n
        LEFT JOIN pedidos p ON p.id = n.pedido_id
        WHERE (
            (?='admin' AND n.papel_destino='admin')
            OR (?='entregador' AND n.papel_destino='entregador' AND n.usuario_id=?)
            OR (?='solicitante' AND n.papel_destino='solicitante' AND n.unidade_id=?)
        ) AND n.lida=0
        ORDER BY n.criado_em DESC, n.id DESC LIMIT 40
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
    papel, usuario_id, unidade_id = _parametros_destinatario()
    params = (papel, papel, usuario_id, papel, unidade_id)
    row = con.execute(
        """
        SELECT n.id FROM notificacoes n
        WHERE n.id=? AND (
            (?='admin' AND n.papel_destino='admin')
            OR (?='entregador' AND n.papel_destino='entregador' AND n.usuario_id=?)
            OR (?='solicitante' AND n.papel_destino='solicitante' AND n.unidade_id=?)
        )
        """,
        (nid, *params),
    ).fetchone()
    if not row:
        return jsonify(error="notificação não encontrada"), 404
    con.execute("UPDATE notificacoes SET lida=1, lida_em=? WHERE id=?", (agora_ms(), nid))
    con.commit()
    return jsonify(ok=True)
