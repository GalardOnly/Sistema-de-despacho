"""Rotas de relatórios operacionais e inconformidades."""

from datetime import datetime

from flask import jsonify, request

from ..auth.services import login_required_desp
from ..config import STATUS_EM_ANDAMENTO
from ..database import get_db_desp
from ..extensions import despacho_bp
from ..pedidos.services import TZ, _fmt_duracao, _protocolo, _range_dia_ms, _range_mes_ms, _sla_do_pedido


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
