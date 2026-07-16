import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORRIDAS = ROOT / "corridas"


class ProjectStructureTests(unittest.TestCase):
    def test_domain_packages_are_present(self):
        for nome in (
            "auth",
            "admin",
            "pedidos",
            "entregadores",
            "chat",
            "notificacoes",
            "relatorios",
            "database",
        ):
            with self.subTest(pacote=nome):
                self.assertTrue((CORRIDAS / nome / "__init__.py").is_file())

    def test_templates_tests_and_migrations_use_the_modular_layout(self):
        self.assertTrue((CORRIDAS / "templates" / "despacho" / "login.html").is_file())
        self.assertTrue((ROOT / "tests" / "test_despacho.py").is_file())
        self.assertTrue((ROOT / "migrations" / "env.py").is_file())
        self.assertFalse((CORRIDAS / "tests" / "test_despacho.py").exists())
        self.assertFalse((ROOT / "database" / "migrations" / "env.py").exists())

    def test_application_modules_do_not_become_monoliths(self):
        maiores = {}
        for caminho in CORRIDAS.rglob("*.py"):
            if "__pycache__" in caminho.parts:
                continue
            linhas = len(caminho.read_text(encoding="utf-8").splitlines())
            if linhas > 600:
                maiores[str(caminho.relative_to(ROOT))] = linhas
        self.assertEqual({}, maiores)
        self.assertLessEqual(
            len((CORRIDAS / "despacho" / "__init__.py").read_text(encoding="utf-8").splitlines()),
            100,
        )


if __name__ == "__main__":
    unittest.main()
