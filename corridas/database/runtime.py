"""Conexões SQLite e PostgreSQL usadas pelo sistema de despacho."""

import os
import re
import sqlite3
import threading

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError as SqlAlchemyIntegrityError


SCHEMA_POSTGRES = "despacho"
ERROS_INTEGRIDADE = (sqlite3.IntegrityError, SqlAlchemyIntegrityError)

_engine = None
_engine_key = None
_engine_lock = threading.Lock()


def banco_postgres_configurado():
    return bool((os.environ.get("DATABASE_URL") or "").strip())


def usuario_postgres_runtime():
    if not banco_postgres_configurado():
        return None
    try:
        username = make_url((os.environ.get("DATABASE_URL") or "").strip()).username or ""
    except Exception:
        return None
    return username.split(".", 1)[0]


def _inteiro_ambiente(nome, padrao, minimo, maximo):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    try:
        numero = int(valor)
    except ValueError as exc:
        raise RuntimeError(f"{nome} precisa ser um número inteiro.") from exc
    if not minimo <= numero <= maximo:
        raise RuntimeError(f"{nome} precisa estar entre {minimo} e {maximo}.")
    return numero


def _url_postgres():
    valor = (os.environ.get("DATABASE_URL") or "").strip()
    if not valor:
        raise RuntimeError("DATABASE_URL não foi configurada.")

    try:
        url = make_url(valor)
    except Exception as exc:
        raise RuntimeError("DATABASE_URL possui formato inválido.") from exc

    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise RuntimeError("DATABASE_URL precisa apontar para PostgreSQL.")
    if url.port == 6543:
        raise RuntimeError(
            "Use o Session pooler do Supabase na porta 5432. "
            "A porta 6543 não preserva o schema privado entre transações."
        )

    query = dict(url.query)
    query.setdefault("sslmode", (os.environ.get("DATABASE_SSLMODE") or "require").strip())
    certificado = (os.environ.get("DATABASE_SSLROOTCERT") or "").strip()
    if certificado:
        query.setdefault("sslrootcert", certificado)
    query.setdefault("application_name", "sistema-despacho")
    return url.set(drivername="postgresql+psycopg", query=query)


def _chave_engine(url):
    return (
        url.render_as_string(hide_password=False),
        _inteiro_ambiente("DATABASE_POOL_SIZE", 5, 1, 20),
        _inteiro_ambiente("DATABASE_POOL_OVERFLOW", 2, 0, 20),
        _inteiro_ambiente("DATABASE_POOL_TIMEOUT", 10, 1, 60),
    )


def _configurar_sessao(dbapi_connection, _connection_record):
    with dbapi_connection.cursor() as cursor:
        cursor.execute(f'SET search_path TO "{SCHEMA_POSTGRES}", public')
    dbapi_connection.commit()


def _criar_engine(url, chave):
    _, pool_size, max_overflow, pool_timeout = chave
    connect_args = {"connect_timeout": 5}
    opcoes = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "connect_args": connect_args,
    }

    opcoes.update(
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
    )

    engine = create_engine(url, **opcoes)
    event.listen(engine, "connect", _configurar_sessao)
    return engine


def _obter_engine():
    global _engine, _engine_key

    url = _url_postgres()
    chave = _chave_engine(url)
    with _engine_lock:
        if _engine is None or chave != _engine_key:
            if _engine is not None:
                _engine.dispose()
            _engine = _criar_engine(url, chave)
            _engine_key = chave
    return _engine


def resetar_engine_postgres():
    global _engine, _engine_key

    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _engine_key = None


def _trocar_placeholders(sql):
    resultado = []
    aspas_simples = False
    aspas_duplas = False
    indice = 0

    while indice < len(sql):
        caractere = sql[indice]
        seguinte = sql[indice + 1] if indice + 1 < len(sql) else ""

        if caractere == "'" and not aspas_duplas:
            resultado.append(caractere)
            if aspas_simples and seguinte == "'":
                resultado.append(seguinte)
                indice += 2
                continue
            aspas_simples = not aspas_simples
        elif caractere == '"' and not aspas_simples:
            resultado.append(caractere)
            if aspas_duplas and seguinte == '"':
                resultado.append(seguinte)
                indice += 2
                continue
            aspas_duplas = not aspas_duplas
        elif caractere == "?" and not aspas_simples and not aspas_duplas:
            resultado.append("%s")
        else:
            resultado.append(caractere)
        indice += 1

    return "".join(resultado)


def adaptar_sql_postgres(sql):
    comando = sql.strip().rstrip(";")
    ignorar_conflito = bool(
        re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO\b", comando, flags=re.IGNORECASE)
    )
    if ignorar_conflito:
        comando = re.sub(
            r"^INSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            comando,
            count=1,
            flags=re.IGNORECASE,
        )
        comando += " ON CONFLICT DO NOTHING"

    inserir = bool(re.match(r"^INSERT\s+INTO\b", comando, flags=re.IGNORECASE))
    retornar_id = inserir and not re.search(r"\bRETURNING\b", comando, flags=re.IGNORECASE)
    if retornar_id:
        comando += " RETURNING id"

    return _trocar_placeholders(comando), retornar_id


class ResultadoPostgres:
    def __init__(self, resultado=None, lastrowid=None):
        self._resultado = resultado
        self.lastrowid = lastrowid
        self.rowcount = resultado.rowcount if resultado is not None else 1 if lastrowid else 0

    def fetchone(self):
        if self._resultado is None or not self._resultado.returns_rows:
            return None
        return self._resultado.mappings().fetchone()

    def fetchall(self):
        if self._resultado is None or not self._resultado.returns_rows:
            return []
        return self._resultado.mappings().fetchall()


class ConexaoPostgres:
    def __init__(self, conexao):
        self._conexao = conexao

    def execute(self, sql, parametros=()):
        comando, retornar_id = adaptar_sql_postgres(sql)
        resultado = self._conexao.exec_driver_sql(comando, tuple(parametros or ()))
        if retornar_id:
            row = resultado.fetchone()
            return ResultadoPostgres(lastrowid=row[0] if row else None)
        return ResultadoPostgres(resultado=resultado)

    def commit(self):
        self._conexao.commit()

    def rollback(self):
        self._conexao.rollback()

    def close(self):
        self._conexao.close()


def abrir_conexao(caminho_sqlite):
    if banco_postgres_configurado():
        return ConexaoPostgres(_obter_engine().connect())

    conexao = sqlite3.connect(caminho_sqlite)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 5000")
    return conexao
