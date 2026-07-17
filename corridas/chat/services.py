"""Serialização e tratamento de mensagens do chat."""

def _chat_linha(row):
    return {
        "id": row["id"],
        "solicitante_id": row["solicitante_id"],
        "unidade_id": row["unidade_id"],
        "remetente_id": row["remetente_id"],
        "remetente_nome": row["remetente_nome"],
        "remetente_papel": row["remetente_papel"],
        "unidade": row["unidade"],
        "texto": row["texto"],
        "ts": row["ts"],
    }

def _resumo_mensagem_chat(texto, limite=120):
    resumo = " ".join((texto or "").split())
    if len(resumo) <= limite:
        return resumo
    return resumo[: limite - 1].rstrip() + "…"
