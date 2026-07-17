"""Camada de persistência do sistema de despacho."""

from .dispatch import close_db_desp, get_db_desp, init_db_desp, verificar_db_desp
from .runtime import (
    ERROS_INTEGRIDADE,
    ConexaoPostgres,
    ResultadoPostgres,
    _url_postgres,
    adaptar_sql_postgres,
    abrir_conexao,
    banco_postgres_configurado,
    resetar_engine_postgres,
    usuario_postgres_runtime,
)

__all__ = [
    "ERROS_INTEGRIDADE",
    "ConexaoPostgres",
    "ResultadoPostgres",
    "_url_postgres",
    "adaptar_sql_postgres",
    "abrir_conexao",
    "banco_postgres_configurado",
    "close_db_desp",
    "get_db_desp",
    "init_db_desp",
    "resetar_engine_postgres",
    "usuario_postgres_runtime",
    "verificar_db_desp",
]
