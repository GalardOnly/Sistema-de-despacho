"""Regras de negócio e serialização de pedidos."""

import calendar
import hashlib
import math
import secrets
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from flask import request

from ..config import (
    LIMITE_NOME,
    STATUS_ATIVOS_ENTREGADOR,
    TIPOS_EXAME,
    TZ_NOME,
    URGENCIA_META,
)
from ..database.dispatch import _normalizar_veiculo


TZ = ZoneInfo(TZ_NOME)


def agora_ms():
    return int(time.time() * 1000)


def _parametro_consulta_positivo(nome):
    valor = request.args.get(nome)
    if valor is None:
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def _limite_consulta(padrao, maximo):
    solicitado = _parametro_consulta_positivo("limit")
    return min(solicitado or padrao, maximo)


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
    if len(nome_limpo) < 2 or len(nome_limpo) > LIMITE_NOME or normalizado == "outro":
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
    if len(nome_limpo) < 2 or len(nome_limpo) > LIMITE_NOME:
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
        "urgencia_mista": bool(d.get("urgencia_mista")),
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
            f"SELECT 1 FROM pedidos WHERE entregador_id=? AND status IN ({placeholders}) LIMIT 1",  # nosec B608
            (entregador_id, *STATUS_ATIVOS_ENTREGADOR),
        ).fetchone()
        is not None
    )


def _reservar_entregador(con, entregador_id):
    placeholders = _status_placeholders(STATUS_ATIVOS_ENTREGADOR)
    resultado = con.execute(
        "UPDATE usuarios SET disponivel=0 "  # nosec B608
        "WHERE id=? AND papel='entregador' AND ativo=1 AND disponivel=1 "
        "AND NOT EXISTS ("
        f"SELECT 1 FROM pedidos WHERE entregador_id=? AND status IN ({placeholders})"
        ")",
        (entregador_id, entregador_id, *STATUS_ATIVOS_ENTREGADOR),
    )
    return resultado.rowcount == 1


def _liberar_entregador_se_sem_pedido_ativo(con, entregador_id):
    placeholders = _status_placeholders(STATUS_ATIVOS_ENTREGADOR)
    con.execute(
        "UPDATE usuarios SET disponivel=1 "  # nosec B608
        "WHERE id=? AND NOT EXISTS ("
        f"SELECT 1 FROM pedidos WHERE entregador_id=? AND status IN ({placeholders})"
        ")",
        (entregador_id, entregador_id, *STATUS_ATIVOS_ENTREGADOR),
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
