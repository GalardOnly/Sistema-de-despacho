import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from corridas import database


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
        despacho_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (PROJECT_ROOT / "corridas").rglob("*.py")
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

    def test_alembic_chain_keeps_schema_private_and_reaches_expected_head(self):
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        scripts = ScriptDirectory.from_config(config)
        revisions = [revision.revision for revision in scripts.walk_revisions()]

        self.assertEqual(["004_revogacao_sessao"], scripts.get_heads())
        self.assertEqual(
            [
                "004_revogacao_sessao",
                "003_despacho_atomico",
                "002_urgencia_mista",
                "001_initial",
            ],
            revisions,
        )

        migration = (
            PROJECT_ROOT / "migrations" / "versions" / "001_initial.py"
        ).read_text(encoding="utf-8")

        for trecho in (
            "CREATE SCHEMA IF NOT EXISTS despacho",
            "REVOKE ALL ON SCHEMA despacho FROM PUBLIC",
            "REVOKE ALL ON ALL TABLES IN SCHEMA despacho FROM anon",
            "REVOKE ALL ON ALL TABLES IN SCHEMA despacho FROM authenticated",
            "CREATE TABLE IF NOT EXISTS despacho.pedidos",
            "CREATE TABLE IF NOT EXISTS despacho.chat_mensagens",
        ):
            self.assertIn(trecho, migration)

        mixed_priority_migration = (
            PROJECT_ROOT
            / "migrations"
            / "versions"
            / "002_urgencia_mista.py"
        ).read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS urgencia_mista", mixed_priority_migration)
        self.assertIn("002_urgencia_mista", mixed_priority_migration)

        atomic_dispatch_migration = (
            PROJECT_ROOT
            / "migrations"
            / "versions"
            / "003_despacho_atomico.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_pedidos_entregador_ativo",
            atomic_dispatch_migration,
        )
        self.assertIn("HAVING COUNT(*) > 1", atomic_dispatch_migration)
        self.assertIn("003_despacho_atomico", atomic_dispatch_migration)

        session_revocation_migration = (
            PROJECT_ROOT
            / "migrations"
            / "versions"
            / "004_revogacao_sessao.py"
        ).read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS sessao_versao", session_revocation_migration)
        self.assertIn("004_revogacao_sessao", session_revocation_migration)

        role_script = (PROJECT_ROOT / "scripts" / "init_supabase.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("GRANT SELECT, INSERT, UPDATE", role_script)
        self.assertNotIn("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES", role_script)
        self.assertIn('command.upgrade(_alembic_config(url), "head")', role_script)
        self.assertNotIn('glob("*.sql")', role_script)


if __name__ == "__main__":
    unittest.main()
