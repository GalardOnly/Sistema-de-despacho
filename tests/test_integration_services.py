import os
import unittest
from pathlib import Path


POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
REDIS_URL = os.environ.get("TEST_REDIS_URL")
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(POSTGRES_URL, "PostgreSQL de integração não configurado")
class PostgreSQLIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from alembic import command
        from alembic.config import Config
        from werkzeug.security import generate_password_hash

        cls.psycopg = psycopg
        os.environ["DATABASE_URL"] = POSTGRES_URL
        os.environ["DATABASE_SSLMODE"] = "disable"

        with psycopg.connect(POSTGRES_URL, autocommit=True) as con:
            con.execute("DROP SCHEMA IF EXISTS despacho CASCADE")

        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection_url"] = POSTGRES_URL
        command.upgrade(config, "head")

        with psycopg.connect(POSTGRES_URL) as con:
            con.execute("SET search_path TO despacho, public")
            cls.origem_id = con.execute(
                "INSERT INTO unidades(nome) VALUES(%s) RETURNING id", ("Unidade Origem",)
            ).fetchone()[0]
            cls.destino_id = con.execute(
                "INSERT INTO unidades(nome) VALUES(%s) RETURNING id", ("Unidade Destino",)
            ).fetchone()[0]
            cls.admin_id = cls._inserir_usuario(
                con, generate_password_hash, "Admin", "admin-pg", "admin", None, None, None
            )
            cls.solicitante_id = cls._inserir_usuario(
                con,
                generate_password_hash,
                "Solicitante",
                "solicitante-pg",
                "solicitante",
                cls.origem_id,
                "SOL-PG",
                None,
            )
            cls.entregador_id = cls._inserir_usuario(
                con,
                generate_password_hash,
                "Entregador",
                "entregador-pg",
                "entregador",
                None,
                "ENT-PG",
                "moto",
            )
            con.commit()

        from corridas.database import resetar_engine_postgres

        resetar_engine_postgres()
        from corridas.app import criar_app

        cls.app = criar_app()
        cls.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)

    @classmethod
    def tearDownClass(cls):
        from corridas.database import resetar_engine_postgres

        resetar_engine_postgres()
        with cls.psycopg.connect(POSTGRES_URL, autocommit=True) as con:
            con.execute("DROP SCHEMA IF EXISTS despacho CASCADE")
        os.environ.pop("DATABASE_URL", None)

    @staticmethod
    def _inserir_usuario(
        con, gerar_hash, nome, username, papel, unidade_id, codigo_ref, tipo_veiculo
    ):
        return con.execute(
            """
            INSERT INTO usuarios(
                nome, username, senha_hash, papel, unidade_id, codigo_ref, tipo_veiculo
            ) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """,
            (
                nome,
                username,
                gerar_hash("Senha-forte-938!"),
                papel,
                unidade_id,
                codigo_ref,
                tipo_veiculo,
            ),
        ).fetchone()[0]

    def _cliente_autenticado(self, username):
        cliente = self.app.test_client()
        resposta = cliente.post(
            "/despacho/login",
            data={"username": username, "senha": "Senha-forte-938!"},
        )
        self.assertEqual(302, resposta.status_code)
        return cliente

    @staticmethod
    def _csrf(cliente):
        with cliente.session_transaction() as sessao:
            return sessao["desp_csrf_token"]

    def _post(self, cliente, url, dados=None):
        return cliente.post(
            url,
            json=dados or {},
            headers={"X-CSRF-Token": self._csrf(cliente)},
        )

    def test_complete_dispatch_flow_uses_postgresql(self):
        solicitante = self._cliente_autenticado("solicitante-pg")
        criado = self._post(
            solicitante,
            "/despacho/api/pedidos",
            {
                "destino_id": self.destino_id,
                "tipo": "Sangue",
                "urgencia": "rotina",
                "urgencia_mista": False,
                "tipo_veiculo": "moto",
                "operador_nome": "Operador PostgreSQL",
            },
        )
        self.assertEqual(200, criado.status_code, criado.get_data(as_text=True))
        pedido_id = criado.get_json()["id"]

        admin = self._cliente_autenticado("admin-pg")
        despacho = self._post(
            admin,
            f"/despacho/api/pedidos/{pedido_id}/despachar",
            {"entregador_id": self.entregador_id},
        )
        self.assertEqual(200, despacho.status_code, despacho.get_data(as_text=True))

        entregador = self._cliente_autenticado("entregador-pg")
        self.assertEqual(
            200,
            self._post(entregador, f"/despacho/api/pedidos/{pedido_id}/aceitar").status_code,
        )
        localizacao = self._post(
            entregador,
            f"/despacho/api/pedidos/{pedido_id}/localizacoes",
            {"latitude": -22.97, "longitude": -49.87, "precisao": 8.5},
        )
        self.assertEqual(200, localizacao.status_code, localizacao.get_data(as_text=True))
        self.assertEqual(
            200,
            self._post(entregador, f"/despacho/api/pedidos/{pedido_id}/retirada").status_code,
        )
        entregue = self._post(
            entregador, f"/despacho/api/pedidos/{pedido_id}/entrega"
        )
        self.assertEqual(200, entregue.status_code, entregue.get_data(as_text=True))
        self.assertEqual("entregue", entregue.get_json()["status"])

        mensagem = self._post(
            solicitante,
            "/despacho/api/chat",
            {"texto": "Mensagem persistida no PostgreSQL"},
        )
        self.assertEqual(200, mensagem.status_code, mensagem.get_data(as_text=True))
        conversa = admin.get(f"/despacho/api/chat?unidade_id={self.origem_id}")
        self.assertEqual(
            "Mensagem persistida no PostgreSQL", conversa.get_json()[-1]["texto"]
        )

        with self.psycopg.connect(POSTGRES_URL) as con:
            con.execute("SET search_path TO despacho, public")
            estado = con.execute(
                "SELECT status FROM pedidos WHERE id=%s", (pedido_id,)
            ).fetchone()[0]
            notificacoes = con.execute(
                "SELECT COUNT(*) FROM notificacoes WHERE pedido_id=%s", (pedido_id,)
            ).fetchone()[0]
        self.assertEqual("entregue", estado)
        self.assertGreaterEqual(notificacoes, 4)

    def test_database_prevents_two_active_orders_for_same_driver(self):
        import time

        agora = int(time.time() * 1000)
        with self.psycopg.connect(POSTGRES_URL) as con:
            con.execute("SET search_path TO despacho, public")
            con.execute(
                """
                INSERT INTO pedidos(
                    origem_id,destino_id,tipo,urgencia,tipo_veiculo,sla_limite_min,
                    status,entregador_id,criado_por,ts_solicitado
                ) VALUES(%s,%s,'Sangue','rotina','moto',720,'despachado',%s,%s,%s)
                """,
                (
                    self.origem_id,
                    self.destino_id,
                    self.entregador_id,
                    self.solicitante_id,
                    agora,
                ),
            )
            with self.assertRaises(self.psycopg.errors.UniqueViolation):
                con.execute(
                    """
                    INSERT INTO pedidos(
                        origem_id,destino_id,tipo,urgencia,tipo_veiculo,sla_limite_min,
                        status,entregador_id,criado_por,ts_solicitado
                    ) VALUES(%s,%s,'Urina','rotina','moto',720,'despachado',%s,%s,%s)
                    """,
                    (
                        self.origem_id,
                        self.destino_id,
                        self.entregador_id,
                        self.solicitante_id,
                        agora + 1,
                    ),
                )
            con.rollback()

    def test_operator_identity_is_unique_after_normalization(self):
        solicitante = self._cliente_autenticado("solicitante-pg")
        primeiro = self._post(
            solicitante,
            "/despacho/api/operadores",
            {"nome": "  Maria   da Recepção  "},
        )
        segundo = self._post(
            solicitante,
            "/despacho/api/operadores",
            {"nome": "MARIA DA RECEPÇÃO"},
        )
        self.assertEqual(200, primeiro.status_code, primeiro.get_data(as_text=True))
        self.assertEqual(200, segundo.status_code, segundo.get_data(as_text=True))
        self.assertEqual(primeiro.get_json()["id"], segundo.get_json()["id"])

        with self.psycopg.connect(POSTGRES_URL) as con:
            con.execute("SET search_path TO despacho, public")
            total = con.execute(
                """
                SELECT COUNT(*)
                FROM operadores_solicitante
                WHERE unidade_id=%s AND nome_normalizado=%s AND ativo=1
                """,
                (self.origem_id, "maria da recepção"),
            ).fetchone()[0]
        self.assertEqual(1, total)

    def test_paginated_queries_are_postgresql_compatible(self):
        admin = self._cliente_autenticado("admin-pg")
        solicitante = self._cliente_autenticado("solicitante-pg")
        entregador = self._cliente_autenticado("entregador-pg")

        consultas = (
            (admin, "/despacho/api/unidades?limit=1"),
            (admin, "/despacho/api/usuarios?limit=1"),
            (admin, "/despacho/api/logins?limit=1"),
            (admin, "/despacho/api/pedidos?limit=1"),
            (admin, "/despacho/api/chat/resumo?limit=1"),
            (admin, "/despacho/api/relatorios/inconformidades?limit=1"),
            (solicitante, "/despacho/api/operadores?limit=1"),
            (solicitante, "/despacho/api/pedidos?limit=1"),
            (entregador, "/despacho/api/pedidos?limit=1"),
        )
        for cliente, url in consultas:
            with self.subTest(url=url):
                resposta = cliente.get(url)
                self.assertEqual(200, resposta.status_code, resposta.get_data(as_text=True))
                self.assertIsInstance(resposta.get_json(), list)


@unittest.skipUnless(REDIS_URL, "Redis de integração não configurado")
class RedisIntegrationTests(unittest.TestCase):
    def test_rate_limit_is_shared_between_process_instances(self):
        import redis

        from corridas.security import LoginRateLimiter

        cliente = redis.Redis.from_url(REDIS_URL)
        cliente.flushdb()
        os.environ["REDIS_URL"] = REDIS_URL
        primeiro = LoginRateLimiter()
        segundo = LoginRateLimiter()
        primeiro.configurar("production")
        segundo.configurar("production")

        for _ in range(primeiro.max_usuario):
            token = primeiro.iniciar("192.0.2.25", "usuario-compartilhado")
            self.assertIsNotNone(token)
            primeiro.concluir(
                "192.0.2.25", "usuario-compartilhado", token, falhou=True
            )

        self.assertIsNone(segundo.iniciar("192.0.2.25", "usuario-compartilhado"))
        cliente.flushdb()


if __name__ == "__main__":
    unittest.main()
