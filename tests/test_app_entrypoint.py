import importlib
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORRIDAS_DIR = PROJECT_ROOT / "corridas"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from corridas import despacho, security


class AppEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = despacho.DESP_DB_PATH
        self.original_env_db_path = os.environ.get("DB_PATH")
        self.original_app_secret = os.environ.get("APP_SECRET")
        self.original_admin_password = os.environ.get("DESPACHO_ADMIN_SENHA_INICIAL")
        self.original_app_module = sys.modules.pop("corridas.app", None)

        despacho.DESP_DB_PATH = str(Path(self.tempdir.name) / "despacho.db")
        os.environ["DB_PATH"] = str(Path(self.tempdir.name) / "corridas.db")
        os.environ["APP_SECRET"] = "segredo-testes-entrypoint-1234567890"
        os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = "senha-admin-testes"

        despacho.init_db_desp()
        self.app_module = importlib.import_module("corridas.app")
        security.login_limiter.reset()
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        sys.modules.pop("corridas.app", None)
        if self.original_app_module is not None:
            sys.modules["corridas.app"] = self.original_app_module
        despacho.DESP_DB_PATH = self.original_db_path
        if self.original_env_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = self.original_env_db_path
        if self.original_app_secret is None:
            os.environ.pop("APP_SECRET", None)
        else:
            os.environ["APP_SECRET"] = self.original_app_secret
        if self.original_admin_password is None:
            os.environ.pop("DESPACHO_ADMIN_SENHA_INICIAL", None)
        else:
            os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = self.original_admin_password
        self.tempdir.cleanup()

    def test_entrypoint_exposes_only_dispatch_system(self):
        root = self.client.get("/")
        self.assertEqual(302, root.status_code)
        self.assertTrue(root.headers["Location"].endswith("/despacho/"))

        legacy_login = self.client.get("/login")
        self.assertEqual(302, legacy_login.status_code)
        self.assertTrue(legacy_login.headers["Location"].endswith("/despacho/login"))

        self.assertEqual(404, self.client.get("/api/config").status_code)
        self.assertEqual(404, self.client.get("/api/corridas").status_code)

    def test_health_and_readiness_endpoints(self):
        health = self.client.get("/healthz")
        ready = self.client.get("/readyz")

        self.assertEqual(200, health.status_code)
        self.assertEqual("ok", health.get_json()["status"])
        self.assertEqual(200, ready.status_code)
        self.assertEqual("pronto", ready.get_json()["status"])

    def test_request_body_limit_is_enabled(self):
        self.assertEqual(65536, self.app_module.app.config["MAX_CONTENT_LENGTH"])
        logged = self.client.post(
            "/despacho/login",
            data={"username": "admin", "senha": "senha-admin-testes"},
        )
        self.assertEqual(302, logged.status_code)
        with self.client.session_transaction() as sess:
            csrf = sess["desp_csrf_token"]
        response = self.client.post(
            "/despacho/api/chat",
            data="x" * 70000,
            content_type="application/json",
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(413, response.status_code, response.get_json())
        self.assertIn("limite", response.get_json()["error"])

    def test_legacy_login_code_no_longer_unlocks_old_api(self):
        response = self.client.post("/login", data={"nome": "Teste", "codigo": "1234"})
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/despacho/login"))
        self.assertEqual(404, self.client.get("/api/config").status_code)

        with self.client.session_transaction() as sess:
            self.assertNotIn("nome", sess)

    def test_app_secret_must_be_configured(self):
        sys.modules.pop("corridas.app", None)
        os.environ.pop("APP_SECRET", None)

        with self.assertRaisesRegex(RuntimeError, "APP_SECRET"):
            importlib.import_module("corridas.app")

    def test_app_secret_rejects_placeholder_value(self):
        sys.modules.pop("corridas.app", None)
        for value in (
            "troque-este-segredo-em-producao",
            "cole_aqui_a_chave_gerada_com_secrets",
        ):
            with self.subTest(value=value):
                sys.modules.pop("corridas.app", None)
                os.environ["APP_SECRET"] = value

                with self.assertRaisesRegex(RuntimeError, "APP_SECRET"):
                    importlib.import_module("corridas.app")

    def test_wsgi_import_works_from_project_root(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["APP_SECRET"] = "segredo-testes-wsgi-12345678901234567890"
        env["DESPACHO_ADMIN_SENHA_INICIAL"] = "senha-admin-wsgi"
        env["DESPACHO_DB_PATH"] = str(Path(self.tempdir.name) / "wsgi-despacho.db")

        script = (
            "import sys; "
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
            "from corridas.app import app; "
            "print(app.name)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.tempdir.name,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)

    def test_wsgi_import_does_not_create_or_migrate_database(self):
        database_path = Path(self.tempdir.name) / "startup-must-not-create.db"
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["APP_SECRET"] = "segredo-testes-sem-migration-123456789012345"
        env["DESPACHO_DB_PATH"] = str(database_path)

        script = (
            "import sys; "
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
            "from corridas.app import app; "
            f"print({str(database_path)!r})"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.tempdir.name,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertFalse(database_path.exists())

    def test_readiness_rejects_uninitialized_sqlite(self):
        original = despacho.DESP_DB_PATH
        despacho.DESP_DB_PATH = str(Path(self.tempdir.name) / "not-initialized.db")
        try:
            response = self.client.get("/readyz")
        finally:
            despacho.DESP_DB_PATH = original

        self.assertEqual(503, response.status_code)
        self.assertEqual("indisponivel", response.get_json()["status"])

    def test_readiness_rejects_outdated_sqlite_schema(self):
        database_path = Path(self.tempdir.name) / "outdated.db"
        con = sqlite3.connect(database_path)
        try:
            con.execute("CREATE TABLE usuarios(id INTEGER PRIMARY KEY, papel TEXT, ativo INTEGER)")
            con.commit()
        finally:
            con.close()

        original = despacho.DESP_DB_PATH
        despacho.DESP_DB_PATH = str(database_path)
        try:
            response = self.client.get("/readyz")
        finally:
            despacho.DESP_DB_PATH = original

        self.assertEqual(503, response.status_code)
        self.assertEqual("indisponivel", response.get_json()["status"])

    def test_sqlite_initializer_is_explicit_and_idempotent(self):
        database_path = Path(self.tempdir.name) / "explicit-init.db"
        env = os.environ.copy()
        env.pop("DATABASE_URL", None)
        env["DESPACHO_DB_PATH"] = str(database_path)
        env["DESPACHO_ADMIN_SENHA_INICIAL"] = "senha-admin-init-testes"
        command = [sys.executable, str(PROJECT_ROOT / "scripts" / "init_sqlite.py")]

        first = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
        second = subprocess.run(command, env=env, capture_output=True, text=True, check=False)

        self.assertEqual(0, first.returncode, first.stderr + first.stdout)
        self.assertEqual(0, second.returncode, second.stderr + second.stdout)
        con = sqlite3.connect(database_path)
        try:
            admin_count = con.execute(
                "SELECT COUNT(*) FROM usuarios WHERE username='admin'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(1, admin_count)

    def test_cloud_run_rejects_sqlite_marked_as_production(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["APP_SECRET"] = "segredo-testes-producao-123456789012345"
        env["DESPACHO_ADMIN_SENHA_INICIAL"] = "senha-admin-producao"
        env["DESPACHO_DB_PATH"] = str(Path(self.tempdir.name) / "producao.db")
        env["APP_ENV"] = "production"
        env["K_SERVICE"] = "sistema-despacho"
        env["ALLOW_EPHEMERAL_SQLITE"] = "1"

        script = (
            "import sys; "
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
            "from corridas.app import app"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.tempdir.name,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(0, proc.returncode)
        self.assertIn("Produção exige DATABASE_URL", proc.stderr + proc.stdout)

    def test_production_requires_limited_postgres_role(self):
        base_env = {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql://postgres.projeto:senha@localhost:5432/postgres",
        }
        with patch.dict(os.environ, base_env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "role limitada despacho_app"):
                self.app_module.validar_persistencia_cloud_run()

        base_env["DATABASE_URL"] = (
            "postgresql://despacho_app.projeto:senha@localhost:5432/postgres"
        )
        with patch.dict(os.environ, base_env, clear=False):
            self.app_module.validar_persistencia_cloud_run()

    def test_cloud_run_homologation_requires_explicit_ephemeral_sqlite(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("ALLOW_EPHEMERAL_SQLITE", None)
        env["APP_SECRET"] = "segredo-testes-hml-12345678901234567890"
        env["DESPACHO_ADMIN_SENHA_INICIAL"] = "senha-admin-hml"
        env["DESPACHO_DB_PATH"] = str(Path(self.tempdir.name) / "hml.db")
        env["APP_ENV"] = "homologation"
        env["K_SERVICE"] = "sistema-despacho-hml"

        script = (
            "import sys; "
            f"sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
            "from corridas.app import app"
        )
        blocked = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.tempdir.name,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, blocked.returncode)
        self.assertIn("ALLOW_EPHEMERAL_SQLITE=1", blocked.stderr + blocked.stdout)

        env["ALLOW_EPHEMERAL_SQLITE"] = "1"
        allowed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.tempdir.name,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, allowed.returncode, allowed.stderr + allowed.stdout)

    def test_google_cloud_deployment_files_keep_homologation_safe(self):
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        cloudbuild = (PROJECT_ROOT / "cloudbuild.yaml").read_text(encoding="utf-8")
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn("corridas.app:app", dockerfile)
        self.assertIn("gunicorn", requirements)
        self.assertIn("Alembic", requirements)
        self.assertIn("COPY migrations ./migrations", dockerfile)
        self.assertIn("APP_ENV=homologation", cloudbuild)
        self.assertIn("DATABASE_URL=despacho-database-url:latest", cloudbuild)
        self.assertNotIn("ALLOW_EPHEMERAL_SQLITE=1", cloudbuild)
        self.assertIn("--max=1", cloudbuild)

    def test_direct_execution_does_not_expose_flask_debugger_by_default(self):
        source = (CORRIDAS_DIR / "app.py").read_text(encoding="utf-8")

        self.assertNotIn("app.run(debug=True", source)
        self.assertNotIn('host="0.0.0.0"', source)
        self.assertIn('FLASK_DEBUG", False', source)

    def test_security_headers_and_session_cookie_are_enabled(self):
        response = self.client.get("/despacho/login", base_url="https://localhost")

        self.assertEqual("DENY", response.headers["X-Frame-Options"])
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        self.assertEqual(
            "strict-origin-when-cross-origin",
            response.headers["Referrer-Policy"],
        )
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual("no-store, private", response.headers["Cache-Control"])
        self.assertEqual("max-age=31536000", response.headers["Strict-Transport-Security"])
        self.assertEqual(24, len(response.headers["X-Request-ID"]))

        logged = self.client.post(
            "/despacho/login",
            data={"username": "admin", "senha": "senha-admin-testes"},
            base_url="https://localhost",
        )
        cookie = logged.headers["Set-Cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

        admin = self.client.get("/despacho/admin", base_url="https://localhost")
        html = admin.get_data(as_text=True)
        nonce = re.search(r'<style nonce="([^"]+)">', html).group(1)
        csp = admin.headers["Content-Security-Policy"]
        self.assertIn(f"script-src 'self' 'nonce-{nonce}'", csp)
        self.assertIn(f"style-src 'self' 'nonce-{nonce}'", csp)
        self.assertIn("style-src-attr 'none'", csp)
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertNotIn("style-src 'self' 'unsafe-inline'", csp)
        self.assertIn("script-src-attr 'none'", csp)
        self.assertIn(
            'integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="',
            html,
        )

    def test_all_inline_script_and_style_blocks_have_nonce(self):
        templates = CORRIDAS_DIR / "templates" / "despacho"
        for path in templates.glob("*.html"):
            source = path.read_text(encoding="utf-8")
            for tag in re.findall(r"<(?:script|style)\b[^>]*>", source):
                with self.subTest(template=path.name, tag=tag):
                    self.assertIn('nonce="{{ g.csp_nonce }}"', tag)

    def test_templates_do_not_use_inline_style_attributes(self):
        templates = PROJECT_ROOT / "corridas" / "templates"
        for caminho in templates.rglob("*.html"):
            conteudo = caminho.read_text(encoding="utf-8")
            with self.subTest(template=str(caminho.relative_to(PROJECT_ROOT))):
                self.assertNotIn("style=", conteudo)
                self.assertNotIn(".style.", conteudo)

    def test_login_error_is_generic_for_known_and_unknown_users(self):
        known = self.client.post(
            "/despacho/login",
            data={"username": "admin", "senha": "incorreta"},
        )
        unknown = self.client.post(
            "/despacho/login",
            data={"username": "usuario-inexistente", "senha": "incorreta"},
        )

        self.assertEqual(401, known.status_code)
        self.assertEqual(401, unknown.status_code)
        for response in (known, unknown):
            body = response.get_data(as_text=True)
            self.assertIn("Usuário ou senha incorretos.", body)
            self.assertNotIn("usuario-inexistente", body)

    def test_login_blocks_repeated_failures(self):
        for _ in range(5):
            response = self.client.post(
                "/despacho/login",
                data={"username": "admin", "senha": "incorreta"},
                environ_base={"REMOTE_ADDR": "192.0.2.50"},
            )
            self.assertEqual(401, response.status_code)

        blocked = self.client.post(
            "/despacho/login",
            data={"username": "admin", "senha": "incorreta"},
            environ_base={"REMOTE_ADDR": "192.0.2.50"},
        )
        self.assertEqual(429, blocked.status_code)
        self.assertIn("Muitas tentativas", blocked.get_data(as_text=True))

    def test_internal_error_returns_reference_without_exception_details(self):
        @self.app_module.app.get("/despacho/api/teste-falha")
        def teste_falha():
            raise RuntimeError("detalhe interno que não pode vazar")

        response = self.client.get("/despacho/api/teste-falha")

        self.assertEqual(500, response.status_code)
        data = response.get_json()
        self.assertEqual("erro interno", data["error"])
        self.assertEqual(response.headers["X-Request-ID"], data["request_id"])
        self.assertNotIn("detalhe interno", response.get_data(as_text=True))
