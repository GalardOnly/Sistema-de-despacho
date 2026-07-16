import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORRIDAS_DIR = PROJECT_ROOT / "corridas"
if str(CORRIDAS_DIR) not in sys.path:
    sys.path.insert(0, str(CORRIDAS_DIR))

import database


class DatabaseCompatibilityTests(unittest.TestCase):
    def tearDown(self):
        database.resetar_engine_postgres()

    def test_postgres_adapter_keeps_parameters_out_of_sql(self):
        sql, retorna_id = database.adaptar_sql_postgres(
            "INSERT INTO usuarios(nome,username) VALUES(?,?)"
        )

        self.assertEqual(
            "INSERT INTO usuarios(nome,username) VALUES(%s,%s) RETURNING id",
            sql,
        )
        self.assertTrue(retorna_id)

    def test_insert_or_ignore_uses_postgres_conflict_handling(self):
        sql, retorna_id = database.adaptar_sql_postgres(
            "INSERT OR IGNORE INTO unidades(nome) VALUES(?)"
        )

        self.assertEqual(
            "INSERT INTO unidades(nome) VALUES(%s) ON CONFLICT DO NOTHING RETURNING id",
            sql,
        )
        self.assertTrue(retorna_id)

    def test_question_mark_inside_literal_is_not_changed(self):
        sql, retorna_id = database.adaptar_sql_postgres(
            "SELECT '?' AS literal FROM usuarios WHERE id=?"
        )

        self.assertEqual("SELECT '?' AS literal FROM usuarios WHERE id=%s", sql)
        self.assertFalse(retorna_id)

    def test_dispatch_queries_do_not_use_sqlite_only_nocase_collation(self):
        despacho_source = (PROJECT_ROOT / "corridas" / "despacho" / "__init__.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("COLLATE NOCASE", despacho_source.upper())
        self.assertIn("ORDER BY ultimo_ts DESC, LOWER(unidade)", despacho_source)

    def test_database_url_requires_postgres_and_enables_ssl(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://usuario:senha@localhost:5432/postgres"},
            clear=False,
        ):
            url = database._url_postgres()

        self.assertEqual("postgresql+psycopg", url.drivername)
        self.assertEqual("require", url.query["sslmode"])
        self.assertEqual("sistema-despacho", url.query["application_name"])

    def test_runtime_database_role_is_detected_without_exposing_password(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": (
                    "postgresql://despacho_app.projeto:segredo@localhost:5432/postgres"
                )
            },
            clear=False,
        ):
            self.assertEqual("despacho_app", database.usuario_postgres_runtime())

    def test_transaction_pooler_is_rejected_for_private_schema(self):
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://usuario:senha@localhost:6543/postgres"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "Session pooler"):
                database._url_postgres()

    def test_initial_schema_is_private_and_blocks_data_api_roles(self):
        migration = (
            PROJECT_ROOT / "database" / "migrations" / "postgresql" / "001_initial.sql"
        ).read_text(encoding="utf-8")

        for trecho in (
            "CREATE SCHEMA IF NOT EXISTS despacho",
            "REVOKE ALL ON SCHEMA despacho FROM PUBLIC",
            "REVOKE ALL ON ALL TABLES IN SCHEMA despacho FROM anon",
            "REVOKE ALL ON ALL TABLES IN SCHEMA despacho FROM authenticated",
            "CREATE TABLE IF NOT EXISTS pedidos",
            "CREATE TABLE IF NOT EXISTS chat_mensagens",
            "001_initial",
        ):
            self.assertIn(trecho, migration)

        mixed_priority_migration = (
            PROJECT_ROOT
            / "database"
            / "migrations"
            / "postgresql"
            / "002_urgencia_mista.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS urgencia_mista", mixed_priority_migration)
        self.assertIn("002_urgencia_mista", mixed_priority_migration)

        atomic_dispatch_migration = (
            PROJECT_ROOT
            / "database"
            / "migrations"
            / "postgresql"
            / "003_despacho_atomico.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_pedidos_entregador_ativo",
            atomic_dispatch_migration,
        )
        self.assertIn("HAVING COUNT(*) > 1", atomic_dispatch_migration)
        self.assertIn("003_despacho_atomico", atomic_dispatch_migration)

        role_script = (PROJECT_ROOT / "scripts" / "init_supabase.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("GRANT SELECT, INSERT, UPDATE", role_script)
        self.assertNotIn("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES", role_script)


if __name__ == "__main__":
    unittest.main()
