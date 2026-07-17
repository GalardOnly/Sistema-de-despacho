"""Criação e filtragem de notificações por papel e unidade."""

from ..chat.services import _resumo_mensagem_chat
from ..pedidos.services import _nome_unidade, _nome_usuario, _protocolo, agora_ms


def _notificacao_linha(row):
    return {
        "id": row["id"],
        "papel_destino": row["papel_destino"],
        "usuario_id": row["usuario_id"],
        "unidade_id": row["unidade_id"],
        "pedido_id": row["pedido_id"],
        "protocolo": row["protocolo"],
        "tipo": row["tipo"],
        "titulo": row["titulo"],
        "mensagem": row["mensagem"],
        "lida": bool(row["lida"]),
        "criado_em": row["criado_em"],
        "lida_em": row["lida_em"],
    }


def _criar_notificacao(
    con,
    papel_destino,
    titulo,
    mensagem,
    tipo="info",
    pedido_id=None,
    usuario_id=None,
    unidade_id=None,
):
    con.execute(
        """
        INSERT INTO notificacoes(
            papel_destino, usuario_id, unidade_id, pedido_id, tipo, titulo, mensagem, lida, criado_em
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            papel_destino,
            usuario_id,
            unidade_id,
            pedido_id,
            tipo,
            titulo,
            mensagem,
            0,
            agora_ms(),
        ),
    )


def _pedido_resumo(con, pedido):
    protocolo = pedido["protocolo"] or _protocolo(pedido["id"])
    origem = _nome_unidade(con, pedido["origem_id"])
    destino = _nome_unidade(con, pedido["destino_id"])
    return protocolo, origem, destino


def _notificar_admin_novo_pedido(con, pedido):
    protocolo, origem, destino = _pedido_resumo(con, pedido)
    _criar_notificacao(
        con,
        "admin",
        "Novo pedido para retirada",
        f"{protocolo}: {origem} solicitou coleta para {destino}.",
        "pedido",
        pedido_id=pedido["id"],
    )


def _notificar_despacho(con, pedido):
    protocolo, origem, destino = _pedido_resumo(con, pedido)
    entregador = _nome_usuario(con, pedido["entregador_id"]) or "Entregador"
    _criar_notificacao(
        con,
        "entregador",
        "Novo pedido atribuído",
        f"{protocolo}: retire em {origem} e entregue em {destino}.",
        "despacho",
        pedido_id=pedido["id"],
        usuario_id=pedido["entregador_id"],
    )
    _criar_notificacao(
        con,
        "solicitante",
        "Pedido aceito pelo admin",
        f"{protocolo}: {entregador} foi acionado e irá retirar o exame.",
        "despacho",
        pedido_id=pedido["id"],
        unidade_id=pedido["origem_id"],
    )


def _notificar_entregador_a_caminho(con, pedido):
    protocolo, origem, _destino = _pedido_resumo(con, pedido)
    entregador = _nome_usuario(con, pedido["entregador_id"]) or "Entregador"
    _criar_notificacao(
        con,
        "solicitante",
        "Entregador a caminho",
        f"{protocolo}: {entregador} aceitou o pedido e está indo para {origem}.",
        "rota",
        pedido_id=pedido["id"],
        unidade_id=pedido["origem_id"],
    )


def _notificar_retirada(con, pedido):
    protocolo, origem, destino = _pedido_resumo(con, pedido)
    mensagem = f"{protocolo}: exame retirado em {origem} e em transporte para {destino}."
    _criar_notificacao(
        con,
        "admin",
        "Exame retirado",
        mensagem,
        "retirada",
        pedido_id=pedido["id"],
    )
    _criar_notificacao(
        con,
        "solicitante",
        "Exame retirado",
        mensagem,
        "retirada",
        pedido_id=pedido["id"],
        unidade_id=pedido["origem_id"],
    )


def _notificar_entrega(con, pedido):
    protocolo, _origem, destino = _pedido_resumo(con, pedido)
    mensagem = f"{protocolo}: entrega confirmada em {destino}."
    _criar_notificacao(
        con,
        "admin",
        "Entrega confirmada",
        mensagem,
        "entrega",
        pedido_id=pedido["id"],
    )
    _criar_notificacao(
        con,
        "solicitante",
        "Entrega confirmada",
        mensagem,
        "entrega",
        pedido_id=pedido["id"],
        unidade_id=pedido["origem_id"],
    )

def _notificar_chat(con, papel_remetente, unidade_id, texto):
    unidade = _nome_unidade(con, unidade_id) or "Unidade"
    resumo = _resumo_mensagem_chat(texto)
    if papel_remetente == "solicitante":
        _criar_notificacao(
            con,
            "admin",
            "Nova mensagem no chat",
            f"{unidade}: {resumo}",
            "chat",
            unidade_id=unidade_id,
        )
    elif papel_remetente == "admin":
        _criar_notificacao(
            con,
            "solicitante",
            "Mensagem do administrador",
            f"Admin para {unidade}: {resumo}",
            "chat",
            unidade_id=unidade_id,
        )
