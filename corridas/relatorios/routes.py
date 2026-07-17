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


def _periodo_mensal_requisitado():
    hoje = datetime.now(TZ)
    ano = _inteiro_positivo(request.args.get("ano") or hoje.year)
    mes = _inteiro_positivo(request.args.get("mes") or hoje.month)
    if not ano or not 2000 <= ano <= 2100 or not mes or not 1 <= mes <= 12:
        return None
    inicio, fim = _range_mes_ms(ano, mes)
    return ano, mes, inicio, fim


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
    periodo = _periodo_mensal_requisitado()
    if not periodo:
        return jsonify(error="mês ou ano inválido"), 400
    _, _, inicio, fim = periodo
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


@despacho_bp.route("/api/relatorios/entregadores")
@login_required_desp("admin")
def api_relatorio_entregadores():
    tipo_periodo = (request.args.get("periodo") or "mes").strip().casefold()
    if tipo_periodo == "dia":
        hoje = datetime.now(TZ)
        inicio, fim = _range_dia_ms()
        periodo_resposta = {
            "tipo": "dia",
            "data": hoje.date().isoformat(),
        }
    elif tipo_periodo == "mes":
        periodo = _periodo_mensal_requisitado()
        if not periodo:
            return jsonify(error="mês ou ano inválido"), 400
        ano, mes, inicio, fim = periodo
        periodo_resposta = {"tipo": "mes", "ano": ano, "mes": mes}
    else:
        return jsonify(error="período inválido"), 400
    con = get_db_desp()
    rows = con.execute(
        """
        SELECT
            u.id,
            u.nome,
            u.codigo_ref,
            u.tipo_veiculo,
            COUNT(p.id) AS total,
            SUM(CASE WHEN p.status='entregue' THEN 1 ELSE 0 END) AS entregues,
            SUM(CASE WHEN p.status IN (
                'aguardando_entregador','em_rota_retirada','em_rota','despachado','coletado'
            ) THEN 1 ELSE 0 END) AS em_andamento,
            SUM(CASE WHEN p.status='cancelado' THEN 1 ELSE 0 END) AS canceladas,
            SUM(CASE WHEN p.status='entregue'
                AND p.ts_entregue IS NOT NULL
                AND (p.ts_entregue - p.ts_solicitado)
                    > (COALESCE(p.sla_limite_min, 720) * 60000)
                THEN 1 ELSE 0 END) AS fora_sla
        FROM usuarios u
        LEFT JOIN pedidos p
            ON p.entregador_id=u.id
            AND p.ts_solicitado BETWEEN ? AND ?
        WHERE u.papel='entregador' AND u.ativo=1
        GROUP BY u.id, u.nome, u.codigo_ref, u.tipo_veiculo
        ORDER BY LOWER(u.nome), u.id
        """,
        (inicio, fim),
    ).fetchall()
    return jsonify(
        periodo=periodo_resposta,
        entregadores=[
            {
                "id": row["id"],
                "nome": row["nome"],
                "codigo_ref": row["codigo_ref"],
                "tipo_veiculo": row["tipo_veiculo"],
                "total": int(row["total"] or 0),
                "entregues": int(row["entregues"] or 0),
                "em_andamento": int(row["em_andamento"] or 0),
                "canceladas": int(row["canceladas"] or 0),
                "fora_sla": int(row["fora_sla"] or 0),
            }
            for row in rows
        ],
    )
