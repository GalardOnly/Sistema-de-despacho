"""Rotas do fluxo operacional de pedidos e rastreamento."""

import math

from flask import jsonify, request, session

from ..auth.services import login_required_desp
from ..config import (
    LIMITE_JUSTIFICATIVA,
    LIMITE_LOCALIZACOES_RETORNO,
    LIMITE_PEDIDOS_RETORNO,
    STATUS_ATIVOS_ENTREGADOR,
    STATUS_RASTREAMENTO,
    TIPOS_VEICULO,
    URGENCIAS,
)
from ..database import ERROS_INTEGRIDADE, get_db_desp
from ..database.dispatch import _normalizar_veiculo
from ..extensions import despacho_bp
from ..notificacoes.services import (
    _notificar_admin_novo_pedido,
    _notificar_despacho,
    _notificar_entrega,
    _notificar_entregador_a_caminho,
    _notificar_retirada,
)
from .services import (
    _buscar_ou_criar_operador,
    _entregador_ocupado,
    _liberar_entregador_se_sem_pedido_ativo,
    _limite_consulta,
    _operador_linha,
    _parametro_consulta_positivo,
    _pedido_ou_404,
    _protocolo,
    _reservar_entregador,
    _resolver_tipo_pedido,
    _sla_do_pedido,
    _sla_limite_min,
    _status_placeholders,
    _tipos_exame_da_unidade,
    agora_ms,
    linha_pedido,
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
        urgencia_mista = d.get("urgencia_mista", False)
        tipo_veiculo = _normalizar_veiculo(d.get("tipo_veiculo"), "")
        origem_id = session["desp_unidade_id"]
        operador = _buscar_ou_criar_operador(con, origem_id, d.get("operador_nome"))
        if not operador:
            return jsonify(error="nome de quem solicita é obrigatório"), 400
        if urgencia not in URGENCIAS:
            return jsonify(error="urgência inválida"), 400
        if not isinstance(urgencia_mista, bool):
            return jsonify(error="informação de urgências diferentes inválida"), 400
        if tipo_veiculo not in TIPOS_VEICULO:
            return jsonify(error="tipo de veículo obrigatório"), 400
        if not destino_id or int(destino_id) == origem_id:
            return jsonify(error="destino inválido"), 400
        tipo = _resolver_tipo_pedido(con, origem_id, d.get("tipo"), d.get("tipo_outro"))
        if not tipo:
            return jsonify(error="tipo de coleta inválido"), 400
        cur = con.execute(
            "INSERT INTO pedidos(origem_id,destino_id,tipo,urgencia,urgencia_mista,tipo_veiculo,"
            "sla_limite_min,status,criado_por,operador_id,ts_solicitado) "
            "VALUES(?,?,?,?,?,?,?, 'solicitado', ?, ?, ?)",
            (
                origem_id,
                destino_id,
                tipo,
                urgencia,
                int(urgencia_mista),
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

    limite = _limite_consulta(LIMITE_PEDIDOS_RETORNO, LIMITE_PEDIDOS_RETORNO)
    antes_id = _parametro_consulta_positivo("antes_id")
    filtros = []
    params = []
    if papel == "solicitante":
        filtros.append("origem_id=?")
        params.append(session["desp_unidade_id"])
    elif papel == "entregador":
        placeholders = _status_placeholders(STATUS_ATIVOS_ENTREGADOR)
        filtros.extend(
            ["entregador_id=?", f"status IN ({placeholders})"]
        )
        params.extend((session["desp_uid"], *STATUS_ATIVOS_ENTREGADOR))
    if antes_id:
        filtros.append("id<?")
        params.append(antes_id)
    where_sql = "WHERE " + " AND ".join(filtros) if filtros else ""
    rows = con.execute(
        f"SELECT * FROM pedidos {where_sql} ORDER BY id DESC LIMIT ?",  # nosec B608
        (*params, limite),
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
    if not _reservar_entregador(con, entregador_id):
        con.rollback()
        return jsonify(error="entregador indisponível ou em outra entrega"), 409
    try:
        atualizado = con.execute(
            "UPDATE pedidos SET status='aguardando_entregador', entregador_id=?, "
            "ts_aceito_admin=? WHERE id=? AND status='solicitado'",
            (entregador_id, agora, pid),
        )
    except ERROS_INTEGRIDADE:
        con.rollback()
        return jsonify(error="entregador indisponível ou pedido já despachado"), 409
    if atualizado.rowcount != 1:
        con.rollback()
        return jsonify(error="pedido não está mais aguardando despacho"), 409
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
    atualizado = con.execute(
        "UPDATE pedidos SET status='em_rota_retirada', ts_aceito_entregador=? "
        "WHERE id=? AND entregador_id=? AND status='aguardando_entregador'",
        (agora_ms(), pid, session["desp_uid"]),
    )
    if atualizado.rowcount != 1:
        con.rollback()
        return jsonify(error="pedido foi atualizado por outra operação"), 409
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
    atualizado = con.execute(
        "UPDATE pedidos SET status='despachado', ts_coletado=?, ts_despachado=? "
        "WHERE id=? AND entregador_id=? AND status IN ('em_rota_retirada','em_rota')",
        (agora, agora, pid, session["desp_uid"]),
    )
    if atualizado.rowcount != 1:
        con.rollback()
        return jsonify(error="pedido foi atualizado por outra operação"), 409
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
    if len(justificativa) > LIMITE_JUSTIFICATIVA:
        return jsonify(error="justificativa excede o limite permitido"), 400
    if sla["atrasado"] and not (justificativa or r["justificativa_atraso"]):
        return jsonify(error="justificativa de atraso obrigatória"), 400
    agora = agora_ms()
    atualizado = con.execute(
        "UPDATE pedidos SET status='entregue', ts_entregue=?, "
        "justificativa_atraso=COALESCE(?, justificativa_atraso) "
        "WHERE id=? AND entregador_id=? AND status IN ('despachado','coletado')",
        (agora, justificativa or None, pid, session["desp_uid"]),
    )
    if atualizado.rowcount != 1:
        con.rollback()
        return jsonify(error="pedido foi atualizado por outra operação"), 409
    _liberar_entregador_se_sem_pedido_ativo(con, session["desp_uid"])
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
    limite = _limite_consulta(
        LIMITE_LOCALIZACOES_RETORNO, LIMITE_LOCALIZACOES_RETORNO
    )
    antes_id = _parametro_consulta_positivo("antes_id")
    filtro_cursor = " AND id<?" if antes_id else ""
    params = (pid, antes_id, limite) if antes_id else (pid, limite)
    rows = con.execute(
        "SELECT id,latitude,longitude,precisao,ts FROM localizacoes_pedido "  # nosec B608
        f"WHERE pedido_id=?{filtro_cursor} ORDER BY id DESC LIMIT ?",
        params,
    ).fetchall()
    rows = list(reversed(rows))
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
    motivo = ((request.get_json(silent=True) or {}).get("motivo") or "").strip()
    if len(motivo) > LIMITE_JUSTIFICATIVA:
        return jsonify(error="motivo excede o limite permitido"), 400
    if r["status"] in ("entregue", "cancelado"):
        return jsonify(error="pedido já foi finalizado"), 400
    if session["desp_papel"] == "solicitante":
        atualizado = con.execute(
            "UPDATE pedidos SET status='cancelado', motivo_cancelamento=?, ts_cancelado=? "
            "WHERE id=? AND origem_id=? AND status='solicitado'",
            (motivo, agora_ms(), pid, session["desp_unidade_id"]),
        )
    else:
        atualizado = con.execute(
            "UPDATE pedidos SET status='cancelado', motivo_cancelamento=?, ts_cancelado=? "
            "WHERE id=? AND status NOT IN ('entregue','cancelado')",
            (motivo, agora_ms(), pid),
        )
    if atualizado.rowcount != 1:
        con.rollback()
        return jsonify(error="pedido foi atualizado por outra operação"), 409
    pedido = _pedido_ou_404(con, pid)
    if pedido["entregador_id"]:
        _liberar_entregador_se_sem_pedido_ativo(con, pedido["entregador_id"])
    con.commit()
    return jsonify(linha_pedido(con, pedido))
