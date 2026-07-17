"""Configuração central da aplicação e regras de ambiente."""

import os
from pathlib import Path


TZ_NOME = "America/Sao_Paulo"
DESP_DB_PATH = os.environ.get(
    "DESPACHO_DB_PATH",
    str(Path(__file__).resolve().parent / "despacho.db"),
)

TIPOS_EXAME = [
    "Sangue",
    "Urina",
    "Gasometria",
    "Microbiologia/Cultura",
    "Anatomia patológica",
    "Outro",
]
URGENCIAS = ["rotina", "urgente", "emergencia"]
URGENCIA_META = {
    "rotina": {"prioridade": 2, "sla_min": 720, "label": "ROTINA (12 horas)"},
    "urgente": {"prioridade": 1, "sla_min": 40, "label": "URGENTE (40 min)"},
    "emergencia": {"prioridade": 0, "sla_min": 15, "label": "EMERGÊNCIA (15 min)"},
}
PRIORIDADE = {chave: valor["prioridade"] for chave, valor in URGENCIA_META.items()}
PAPEIS = ["admin", "solicitante", "entregador"]
TIPOS_VEICULO = ["moto", "carro"]
TIPOS_INDISPONIBILIDADE = ["clt_desconto", "recusa_padrao"]

STATUS_ATIVOS_ENTREGADOR = (
    "aguardando_entregador",
    "em_rota_retirada",
    "em_rota",
    "despachado",
    "coletado",
)
STATUS_RASTREAMENTO = ("em_rota_retirada", "em_rota", "despachado", "coletado")
STATUS_EM_ANDAMENTO = STATUS_ATIVOS_ENTREGADOR
POSTGRES_SCHEMA_REVISION = "005_operadores_normalizados"
SQLITE_SCHEMA_OBRIGATORIO = {
    "unidades": {"id", "nome"},
    "usuarios": {
        "id",
        "papel",
        "ativo",
        "disponivel",
        "codigo_ref",
        "tipo_veiculo",
        "sessao_versao",
    },
    "pedidos": {
        "id",
        "status",
        "urgencia_mista",
        "ts_aceito_admin",
        "ts_aceito_entregador",
        "ts_cancelado",
    },
    "operadores_solicitante": {
        "id",
        "unidade_id",
        "codigo",
        "nome_normalizado",
    },
    "tipos_coleta_unidade": {"id", "unidade_id", "nome_normalizado"},
    "localizacoes_pedido": {"id", "pedido_id", "latitude", "longitude"},
    "indisponibilidades_entregador": {"id", "entregador_id", "tipo"},
    "chat_mensagens": {"id", "unidade_id", "texto"},
    "notificacoes": {"id", "papel_destino", "lida"},
}

CSRF_SESSION_KEY = "desp_csrf_token"
CSRF_HEADER = "X-CSRF-Token"
SESSION_VERSION_KEY = "desp_sessao_versao"
METODOS_COM_MUTACAO = {"POST", "PUT", "PATCH", "DELETE"}
LIMITE_NOME = 120
LIMITE_USERNAME = 64
LIMITE_CODIGO_REFERENCIA = 64
LIMITE_SENHA = 128
LIMITE_TEXTO_CHAT = 2000
LIMITE_JUSTIFICATIVA = 1000
LIMITE_PEDIDOS_RETORNO = 500
LIMITE_CHAT_RETORNO = 200
LIMITE_LOCALIZACOES_RETORNO = 1000
LIMITE_CADASTROS_RETORNO = 200
LIMITE_RELATORIO_RETORNO = 200
LIMITE_RESUMO_CHAT_RETORNO = 200
LIMITE_TIPOS_COLETA_RETORNO = 200

SENHAS_ADMIN_INSEGURAS = {
    "mudar123",
    "admin",
    "admin123",
    "senha",
    "password",
    "troque-a-senha-inicial",
}
SEGREDOS_INSEGUROS = {
    "troque-este-segredo-em-producao",
    "change-me",
    "changeme",
    "secret",
    "dev",
    "1234",
}


def parece_senha_admin_insegura(valor):
    texto = valor.casefold()
    return texto in SENHAS_ADMIN_INSEGURAS or any(
        termo in texto
        for termo in ("troque", "change", "cole_aqui", "defina_", "placeholder")
    )


def senha_admin_inicial_configurada():
    senha = (os.environ.get("DESPACHO_ADMIN_SENHA_INICIAL") or "").strip()
    if not senha:
        raise RuntimeError(
            "Defina DESPACHO_ADMIN_SENHA_INICIAL antes de criar o primeiro administrador."
        )
    if len(senha) < 8 or parece_senha_admin_insegura(senha):
        raise RuntimeError(
            "DESPACHO_ADMIN_SENHA_INICIAL precisa ter pelo menos 8 caracteres e não pode ser padrão."
        )
    return senha


def parece_placeholder(valor):
    texto = valor.casefold()
    return texto in SEGREDOS_INSEGUROS or any(
        termo in texto for termo in ("troque", "change", "cole_aqui", "placeholder")
    )


def carregar_app_secret():
    secret = (os.environ.get("APP_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("Defina APP_SECRET com uma chave segura antes de iniciar a aplicação.")
    if len(secret) < 32 or parece_placeholder(secret):
        raise RuntimeError(
            "APP_SECRET precisa ter pelo menos 32 caracteres e não pode ser um valor padrão."
        )
    return secret


def env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().casefold() not in {"0", "false", "nao", "não", "off"}


def env_int(nome, padrao, minimo, maximo):
    try:
        valor = int(os.environ.get(nome, padrao))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{nome} precisa ser um número inteiro.") from exc
    if not minimo <= valor <= maximo:
        raise RuntimeError(f"{nome} precisa estar entre {minimo} e {maximo}.")
    return valor


def ambiente():
    return (os.environ.get("APP_ENV") or "development").strip().casefold()
