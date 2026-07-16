"""Rotas de relatórios operacionais e inconformidades."""

from datetime import datetime

from flask import jsonify, request

from ..auth.services import login_required_desp
from ..config import LIMITE_RELATORIO_RETORNO
from ..database import get_db_desp
from ..extensions import despacho_bp
from ..pedidos.services import (
    TZ,
    _fmt_duracao,
    _inteiro_positivo,
    _limite_consulta,
    _parametro_consulta_positivo,
    _protocolo,
    _range_dia_ms,
    _range_mes_ms,
    _resposta_paginada,
    _sla_do_pedido,
    agora_ms,
)


@despacho_bp.route("/api/relatorios/resumo-diario")
@login_required_desp("admin")
def api_relatorio_resumo_diario():
    con = get_db_desp()
    inicio, fim = _range_dia_ms()
    resumo = con.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='entregue' THEN 1 ELSE 0 END) AS entregues, "
        "SUM(CASE WHEN status='cancelado' THEN 1 ELSE 0 END) AS cancelados, "
        "SUM(CASE WHEN status IN ("
        "'aguardando_entregador','em_rota_retirada','em_rota','despachado','coletado'"
        ") THEN 1 ELSE 0 END) AS em_andamento, "
        "SUM(CASE WHEN (COALESCE(ts_entregue, ?) - ts_solicitado) "
        "> (COALESCE(sla_limite_min, 720) * 60000) THEN 1 ELSE 0 END) AS fora_sla "
        "FROM pedidos WHERE ts_solicitado BETWEEN ? AND ?",
        (agora_ms(), inicio, fim),
    ).fetchone()
    return jsonify(
        total=int(resumo["total"] or 0),
        entregues=int(resumo["entregues"] or 0),
        em_andamento=int(resumo["em_andamento"] or 0),
        cancelados=int(resumo["cancelados"] or 0),
        fora_sla=int(resumo["fora_sla"] or 0),
    )


@despacho_bp.route("/api/relatorios/inconformidades")
@login_required_desp("admin")
def api_relatorio_inconformidades():
    con = get_db_desp()
    hoje = datetime.now(TZ)
    ano = _inteiro_positivo(request.args.get("ano") or hoje.year)
    mes = _inteiro_positivo(request.args.get("mes") or hoje.month)
    if not ano or not 2000 <= ano <= 2100 or not mes or not 1 <= mes <= 12:
        return jsonify(error="mês ou ano inválido"), 400
    inicio, fim = _range_mes_ms(ano, mes)
    limite = _limite_consulta(LIMITE_RELATORIO_RETORNO, LIMITE_RELATORIO_RETORNO)
    antes_id = _parametro_consulta_positivo("antes_id")
    cursor = antes_id or 0
    rows = con.execute(
        """
        SELECT p.*, ent.nome AS entregador_nome, sol.nome AS solicitante_nome
        FROM pedidos p
        LEFT JOIN usuarios ent ON ent.id = p.entregador_id
        LEFT JOIN usuarios sol ON sol.id = p.criado_por
        WHERE p.ts_solicitado BETWEEN ? AND ?
          AND p.status='entregue'
          AND p.ts_entregue IS NOT NULL
          AND (p.ts_entregue - p.ts_solicitado) > (COALESCE(p.sla_limite_min, 720) * 60000)
          AND (?=0 OR p.id<?)
        ORDER BY p.id DESC LIMIT ?
        """,
        (inicio, fim, cursor, cursor, limite + 1),
    ).fetchall()

    def serializar(r):
        sla = _sla_do_pedido(r)
        return {
            "protocolo": r["protocolo"] or _protocolo(r["id"]),
            "entregador": r["entregador_nome"],
            "solicitante": r["solicitante_nome"],
            "tempo_excedido_ms": sla["excedido_ms"],
            "tempo_excedido": _fmt_duracao(sla["excedido_ms"]),
            "justificativa_atraso": r["justificativa_atraso"],
        }

    return _resposta_paginada(rows, limite, serializar)
