import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CORRIDAS_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CORRIDAS_DIR.parent
if str(CORRIDAS_DIR) not in sys.path:
    sys.path.insert(0, str(CORRIDAS_DIR))

import despacho
import security


class AppEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = despacho.DESP_DB_PATH
        self.original_env_db_path = os.environ.get("DB_PATH")
        self.original_app_secret = os.environ.get("APP_SECRET")
        self.original_admin_password = os.environ.get("DESPACHO_ADMIN_SENHA_INICIAL")
        self.original_app_module = sys.modules.pop("app", None)

        despacho.DESP_DB_PATH = str(Path(self.tempdir.name) / "despacho.db")
        os.environ["DB_PATH"] = str(Path(self.tempdir.name) / "corridas.db")
        os.environ["APP_SECRET"] = "segredo-testes-entrypoint-1234567890"
        os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = "senha-admin-testes"

        self.app_module = importlib.import_module("app")
        security.login_limiter.reset()
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        sys.modules.pop("app", None)
        if self.original_app_module is not None:
            sys.modules["app"] = self.original_app_module
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

    def test_legacy_login_code_no_longer_unlocks_old_api(self):
        response = self.client.post("/login", data={"nome": "Teste", "codigo": "1234"})
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/despacho/login"))
        self.assertEqual(404, self.client.get("/api/config").status_code)

        with self.client.session_transaction() as sess:
            self.assertNotIn("nome", sess)

    def test_app_secret_must_be_configured(self):
        sys.modules.pop("app", None)
        os.environ.pop("APP_SECRET", None)

        with self.assertRaisesRegex(RuntimeError, "APP_SECRET"):
            importlib.import_module("app")

    def test_app_secret_rejects_placeholder_value(self):
        sys.modules.pop("app", None)
        for value in (
            "troque-este-segredo-em-producao",
            "cole_aqui_a_chave_gerada_com_secrets",
        ):
            with self.subTest(value=value):
                sys.modules.pop("app", None)
                os.environ["APP_SECRET"] = value

                with self.assertRaisesRegex(RuntimeError, "APP_SECRET"):
                    importlib.import_module("app")

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
        self.assertIn("Produção bloqueada", proc.stderr + proc.stdout)

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
        self.assertIn("APP_ENV=homologation", cloudbuild)
        self.assertIn("ALLOW_EPHEMERAL_SQLITE=1", cloudbuild)
        self.assertIn("--max=1", cloudbuild)

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
