"""Rotas da central de notificações."""

from flask import jsonify

from ..auth.services import login_required_desp
from ..database import get_db_desp
from ..extensions import despacho_bp
from ..pedidos.services import agora_ms
from .services import _filtro_notificacoes_sessao, _notificacao_linha


@despacho_bp.route("/api/notificacoes")
@login_required_desp("admin", "solicitante", "entregador")
def api_notificacoes():
    con = get_db_desp()
    where, params = _filtro_notificacoes_sessao("n")
    total_nao_lidas = con.execute(
        f"SELECT COUNT(*) AS n FROM notificacoes n WHERE {where} AND n.lida=0",  # nosec B608
        params,
    ).fetchone()["n"]
    rows = con.execute(
        f"SELECT n.*, p.protocolo FROM notificacoes n "  # nosec B608
        "LEFT JOIN pedidos p ON p.id = n.pedido_id "
        f"WHERE {where} AND n.lida=0 "
        "ORDER BY n.criado_em DESC, n.id DESC LIMIT 40",
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
        f"SELECT n.id FROM notificacoes n WHERE n.id=? AND {where}",  # nosec B608
        (nid, *params),
    ).fetchone()
    if not row:
        return jsonify(error="notificação não encontrada"), 404
    con.execute("UPDATE notificacoes SET lida=1, lida_em=? WHERE id=?", (agora_ms(), nid))
    con.commit()
    return jsonify(ok=True)
