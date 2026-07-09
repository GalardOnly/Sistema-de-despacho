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
