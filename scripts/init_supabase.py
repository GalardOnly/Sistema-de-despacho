"""Aplica as migrations PostgreSQL no projeto Supabase."""

import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg import sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "database" / "migrations" / "postgresql"


def _migration_url():
    valor = (
        os.environ.get("SUPABASE_MIGRATION_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    ).strip()
    if not valor:
        raise RuntimeError(
            "Defina SUPABASE_MIGRATION_URL com a conexão direta ou Session pooler do Supabase."
        )

    partes = urlsplit(valor)
    if partes.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("SUPABASE_MIGRATION_URL precisa apontar para PostgreSQL.")
    if partes.port == 6543:
        raise RuntimeError(
            "Use a conexão direta ou o Session pooler na porta 5432 para aplicar migrations."
        )

    query = dict(parse_qsl(partes.query, keep_blank_values=True))
    query.setdefault("sslmode", os.environ.get("DATABASE_SSLMODE", "require"))
    certificado = (os.environ.get("DATABASE_SSLROOTCERT") or "").strip()
    if certificado:
        query.setdefault("sslrootcert", certificado)
    return urlunsplit((partes.scheme, partes.netloc, partes.path, urlencode(query), partes.fragment))


def _runtime_password():
    senha = (os.environ.get("SUPABASE_RUNTIME_PASSWORD") or "").strip()
    if len(senha) < 24:
        raise RuntimeError("SUPABASE_RUNTIME_PASSWORD precisa ter pelo menos 24 caracteres.")
    return senha


def _configurar_role_runtime(conexao):
    role = "despacho_app"
    senha = _runtime_password()
    with conexao.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        if cursor.fetchone():
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(role), sql.Literal(senha))
            )
        else:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} WITH LOGIN PASSWORD {} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(role), sql.Literal(senha))
            )

        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(conexao.info.dbname),
                sql.Identifier(role),
            )
        )
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA despacho TO {}").format(sql.Identifier(role)))
        cursor.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA despacho TO {}").format(
                sql.Identifier(role)
            )
        )
        cursor.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA despacho TO {}").format(
                sql.Identifier(role)
            )
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA despacho "
                "GRANT SELECT, INSERT, UPDATE ON TABLES TO {}"
            ).format(sql.Identifier(role))
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA despacho "
                "GRANT USAGE, SELECT ON SEQUENCES TO {}"
            ).format(sql.Identifier(role))
        )
        cursor.execute(
            sql.SQL("REVOKE INSERT, UPDATE, DELETE ON despacho.schema_migrations FROM {}").format(
                sql.Identifier(role)
            )
        )
        cursor.execute(
            sql.SQL("ALTER ROLE {} SET search_path TO despacho, public").format(
                sql.Identifier(role)
            )
        )


def aplicar_migrations():
    arquivos = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not arquivos:
        raise RuntimeError("Nenhuma migration PostgreSQL foi encontrada.")

    with psycopg.connect(_migration_url(), connect_timeout=10) as conexao:
        for arquivo in arquivos:
            sql = arquivo.read_text(encoding="utf-8")
            with conexao.cursor() as cursor:
                cursor.execute(sql, prepare=False)
            conexao.commit()
            print(f"Migration aplicada: {arquivo.name}")

        _configurar_role_runtime(conexao)
        conexao.commit()

        with conexao.cursor() as cursor:
            cursor.execute(
                "SELECT version FROM despacho.schema_migrations ORDER BY aplicado_em, version"
            )
            versoes = [row[0] for row in cursor.fetchall()]
    print("Schema Supabase pronto: " + ", ".join(versoes))
    print("Role limitada criada: despacho_app")


if __name__ == "__main__":
    try:
        aplicar_migrations()
    except Exception as exc:
        print(f"Falha ao preparar o Supabase: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
