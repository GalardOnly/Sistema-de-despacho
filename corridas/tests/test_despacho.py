import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path


CORRIDAS_DIR = Path(__file__).resolve().parents[1]
if str(CORRIDAS_DIR) not in sys.path:
    sys.path.insert(0, str(CORRIDAS_DIR))

import despacho
from flask import Flask


LEGACY_SCHEMA = """
CREATE TABLE unidades(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT UNIQUE NOT NULL
);
CREATE TABLE usuarios(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    username TEXT UNIQUE NOT NULL,
    senha_hash TEXT NOT NULL,
    papel TEXT NOT NULL,
    unidade_id INTEGER,
    ativo INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE pedidos(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocolo TEXT UNIQUE,
    origem_id INTEGER NOT NULL,
    destino_id INTEGER NOT NULL,
    tipo TEXT NOT NULL,
    urgencia TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'solicitado',
    entregador_id INTEGER,
    criado_por INTEGER,
    ts_solicitado INTEGER NOT NULL,
    ts_despachado INTEGER,
    ts_coletado INTEGER,
    ts_entregue INTEGER,
    motivo_cancelamento TEXT
);
"""


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "despacho.db")
        self.original_db_path = despacho.DESP_DB_PATH
        despacho.DESP_DB_PATH = self.db_path

    def tearDown(self):
        despacho.DESP_DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_migrates_legacy_database_without_losing_assigned_order(self):
        con = sqlite3.connect(self.db_path)
        con.executescript(LEGACY_SCHEMA)
        con.execute("INSERT INTO unidades(id,nome) VALUES(1,'Origem'),(2,'Destino')")
        con.execute(
            "INSERT INTO pedidos(id,protocolo,origem_id,destino_id,tipo,urgencia,status,"
            "ts_solicitado,ts_despachado) VALUES(1,'P-0001',1,2,'Sangue','rotina',"
            "'despachado',1000,2000)"
        )
        con.commit()
        con.close()

        despacho.init_db_desp()
        despacho.init_db_desp()

        con = sqlite3.connect(self.db_path)
        user_columns = {row[1] for row in con.execute("PRAGMA table_info(usuarios)")}
        order_columns = {row[1] for row in con.execute("PRAGMA table_info(pedidos)")}
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        order = None
        if "ts_aceito_admin" in order_columns:
            order = con.execute(
                "SELECT status, ts_aceito_admin, ts_despachado FROM pedidos WHERE id=1"
            ).fetchone()
        con.close()

        self.assertTrue(
            {
                "disponivel",
                "codigo_ref",
                "tipo_veiculo",
                "indisponibilidade_justificativa",
                "indisponibilidade_tipo",
            }.issubset(user_columns)
        )
        self.assertTrue(
            {
                "ts_aceito_admin",
                "ts_aceito_entregador",
                "ts_cancelado",
                "tipo_veiculo",
                "sla_limite_min",
                "justificativa_atraso",
            }.issubset(order_columns)
        )
        self.assertIn("localizacoes_pedido", tables)
        self.assertIn("chat_mensagens", tables)
        self.assertIn("indisponibilidades_entregador", tables)
        self.assertEqual(("aguardando_entregador", 2000, 2000), order)


class DispatchApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "despacho.db")
        self.original_db_path = despacho.DESP_DB_PATH
        despacho.DESP_DB_PATH = self.db_path
        despacho.init_db_desp()

        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO unidades(nome) VALUES('Origem')")
        self.origem_id = con.execute("SELECT id FROM unidades WHERE nome='Origem'").fetchone()[0]
        self.destino_id = con.execute("SELECT id FROM unidades WHERE nome='Santa Casa'").fetchone()[0]
        con.execute("INSERT INTO unidades(nome) VALUES('Outra unidade')")
        self.outra_unidade_id = con.execute(
            "SELECT id FROM unidades WHERE nome='Outra unidade'"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO usuarios(nome,username,senha_hash,papel,unidade_id,codigo_ref) "
            "VALUES(?,?,?,?,?,?)",
            ("Solicitante", "solicitante", "x", "solicitante", self.origem_id, "SOL-001"),
        )
        self.solicitante_id = con.execute(
            "SELECT id FROM usuarios WHERE username='solicitante'"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO usuarios(nome,username,senha_hash,papel,codigo_ref,tipo_veiculo) "
            "VALUES(?,?,?,?,?,?)",
            ("Entregador", "entregador", "x", "entregador", "ENT-001", "moto"),
        )
        self.entregador_id = con.execute(
            "SELECT id FROM usuarios WHERE username='entregador'"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO usuarios(nome,username,senha_hash,papel,codigo_ref,tipo_veiculo) "
            "VALUES(?,?,?,?,?,?)",
            ("Outro Entregador", "outro_entregador", "x", "entregador", "ENT-002", "carro"),
        )
        self.outro_entregador_id = con.execute(
            "SELECT id FROM usuarios WHERE username='outro_entregador'"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO usuarios(nome,username,senha_hash,papel,unidade_id,codigo_ref) "
            "VALUES(?,?,?,?,?,?)",
            (
                "Outro Solicitante",
                "outro_solicitante",
                "x",
                "solicitante",
                self.outra_unidade_id,
                "SOL-002",
            ),
        )
        self.outro_solicitante_id = con.execute(
            "SELECT id FROM usuarios WHERE username='outro_solicitante'"
        ).fetchone()[0]
        self.admin_id = con.execute("SELECT id FROM usuarios WHERE papel='admin'").fetchone()[0]
        con.commit()
        con.close()

        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.app.register_blueprint(despacho.despacho_bp)
        self.client = self.app.test_client()

    def tearDown(self):
        despacho.DESP_DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def login(self, papel):
        data = {
            "admin": (self.admin_id, None, "Administrador"),
            "solicitante": (self.solicitante_id, self.origem_id, "Solicitante"),
            "entregador": (self.entregador_id, None, "Entregador"),
            "outro_entregador": (self.outro_entregador_id, None, "Outro Entregador"),
            "outro_solicitante": (
                self.outro_solicitante_id,
                self.outra_unidade_id,
                "Outro Solicitante",
            ),
        }
        uid, unidade_id, nome = data[papel]
        with self.client.session_transaction() as sess:
            sess.clear()
            sess["desp_uid"] = uid
            sess["desp_nome"] = nome
            sess["desp_papel"] = "entregador" if "entregador" in papel else (
                "solicitante" if "solicitante" in papel else papel
            )
            sess["desp_unidade_id"] = unidade_id

    def create_order(self, urgencia="rotina", tipo_veiculo="moto"):
        self.login("solicitante")
        response = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Sangue",
                "urgencia": urgencia,
                "tipo_veiculo": tipo_veiculo,
            },
        )
        self.assertEqual(200, response.status_code, response.get_json())
        return response.get_json()

    def dispatch_order(self, pedido_id):
        self.login("admin")
        return self.client.post(
            f"/despacho/api/pedidos/{pedido_id}/despachar",
            json={"entregador_id": self.entregador_id},
        )

    def test_default_units_are_created_for_mvp(self):
        self.login("admin")
        response = self.client.get("/despacho/api/unidades")
        self.assertEqual(200, response.status_code)
        nomes = {row["nome"] for row in response.get_json()}
        self.assertTrue(
            {"Unimed-Lar", "Unimed-Camu 1", "Unimed-Camu 2", "Unimed Farmais"}.issubset(nomes)
        )

    def test_user_registration_requires_reference_code_and_driver_vehicle(self):
        self.login("admin")
        missing_ref = self.client.post(
            "/despacho/api/usuarios",
            json={
                "nome": "Novo Solicitante",
                "username": "novo_sol",
                "senha": "123",
                "papel": "solicitante",
                "unidade_id": self.origem_id,
            },
        )
        self.assertEqual(400, missing_ref.status_code)

        created_requester = self.client.post(
            "/despacho/api/usuarios",
            json={
                "nome": "Novo Solicitante",
                "username": "novo_sol",
                "senha": "123",
                "papel": "solicitante",
                "unidade_id": self.origem_id,
                "codigo_ref": "SOL-999",
            },
        )
        self.assertEqual(200, created_requester.status_code, created_requester.get_json())

        missing_vehicle = self.client.post(
            "/despacho/api/usuarios",
            json={
                "nome": "Novo Entregador",
                "username": "novo_ent",
                "senha": "123",
                "papel": "entregador",
                "codigo_ref": "ENT-999",
            },
        )
        self.assertEqual(400, missing_vehicle.status_code)

        created_driver = self.client.post(
            "/despacho/api/usuarios",
            json={
                "nome": "Novo Entregador",
                "username": "novo_ent",
                "senha": "123",
                "papel": "entregador",
                "codigo_ref": "ENT-999",
                "tipo_veiculo": "carro",
            },
        )
        self.assertEqual(200, created_driver.status_code, created_driver.get_json())

        duplicate_ref = self.client.post(
            "/despacho/api/usuarios",
            json={
                "nome": "Duplicado",
                "username": "duplicado",
                "senha": "123",
                "papel": "solicitante",
                "unidade_id": self.origem_id,
                "codigo_ref": "ENT-999",
            },
        )
        self.assertEqual(400, duplicate_ref.status_code)

    def test_order_requires_vehicle_and_uses_collection_serial(self):
        self.login("solicitante")
        missing_vehicle = self.client.post(
            "/despacho/api/pedidos",
            json={"destino_id": self.destino_id, "tipo": "Sangue", "urgencia": "rotina"},
        )
        self.assertEqual(400, missing_vehicle.status_code)

        pedido = self.create_order(tipo_veiculo="moto")
        self.assertRegex(pedido["protocolo"], r"^COL-\d{5}$")
        self.assertEqual("moto", pedido["tipo_veiculo"])
        self.assertEqual(720, pedido["sla_limite_min"])

    def test_records_complete_flow_and_restores_driver_availability(self):
        pedido = self.create_order()

        response = self.dispatch_order(pedido["id"])
        self.assertEqual(200, response.status_code, response.get_json())
        atribuido = response.get_json()
        self.assertEqual("aguardando_entregador", atribuido["status"])
        self.assertIsNotNone(atribuido["ts"]["aceito_admin"])
        self.assertIsNone(atribuido["ts"]["despachado"])

        self.login("entregador")
        response = self.client.post(f"/despacho/api/pedidos/{pedido['id']}/aceitar")
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual("em_rota_retirada", response.get_json()["status"])
        self.assertIsNotNone(response.get_json()["ts"]["aceito_entregador"])

        response = self.client.post(f"/despacho/api/pedidos/{pedido['id']}/retirada")
        self.assertEqual("despachado", response.get_json()["status"])
        self.assertIsNotNone(response.get_json()["ts"]["coletado"])
        self.assertIsNotNone(response.get_json()["ts"]["despachado"])

        response = self.client.post(f"/despacho/api/pedidos/{pedido['id']}/entrega")
        self.assertEqual("entregue", response.get_json()["status"])
        self.assertIsNotNone(response.get_json()["ts"]["entregue"])

        disponibilidade = self.client.get("/despacho/api/disponibilidade").get_json()
        self.assertTrue(disponibilidade["disponivel"])
        self.assertFalse(disponibilidade["ocupado"])

    def test_blocks_unavailable_or_busy_driver_and_availability_change_while_active(self):
        primeiro = self.create_order()
        self.login("entregador")
        response = self.client.post("/despacho/api/disponibilidade", json={"disponivel": False})
        self.assertEqual(400, response.status_code, response.get_json())

        response = self.client.post(
            "/despacho/api/disponibilidade",
            json={
                "disponivel": False,
                "justificativa": "Consulta médica",
                "tipo": "clt_desconto",
            },
        )
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual("clt_desconto", response.get_json()["indisponibilidade_tipo"])

        response = self.dispatch_order(primeiro["id"])
        self.assertEqual(400, response.status_code)

        self.login("entregador")
        self.client.post("/despacho/api/disponibilidade", json={"disponivel": True})
        response = self.dispatch_order(primeiro["id"])
        self.assertEqual(200, response.status_code, response.get_json())

        segundo = self.create_order()
        response = self.dispatch_order(segundo["id"])
        self.assertEqual(400, response.status_code)

        self.login("entregador")
        response = self.client.post("/despacho/api/disponibilidade", json={"disponivel": True})
        self.assertEqual(400, response.status_code)

    def test_dispatch_requires_vehicle_compatible_driver(self):
        pedido = self.create_order(tipo_veiculo="carro")
        response = self.dispatch_order(pedido["id"])
        self.assertEqual(400, response.status_code)

        self.login("admin")
        response = self.client.post(
            f"/despacho/api/pedidos/{pedido['id']}/despachar",
            json={"entregador_id": self.outro_entregador_id},
        )
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual("Outro Entregador", response.get_json()["entregador"])

    def test_cancel_records_timestamp(self):
        pedido = self.create_order()
        response = self.client.post(
            f"/despacho/api/pedidos/{pedido['id']}/cancelar", json={"motivo": "Exame suspenso"}
        )
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual("cancelado", response.get_json()["status"])
        self.assertIsNotNone(response.get_json()["ts"]["cancelado"])

    def test_stores_ordered_route_and_enforces_location_permissions(self):
        pedido = self.create_order()
        self.assertEqual(200, self.dispatch_order(pedido["id"]).status_code)

        self.login("entregador")
        before_accept = self.client.post(
            f"/despacho/api/pedidos/{pedido['id']}/localizacoes",
            json={"latitude": -22.2, "longitude": -49.9, "precisao": 10},
        )
        self.assertEqual(400, before_accept.status_code)
        self.assertEqual(200, self.client.post(f"/despacho/api/pedidos/{pedido['id']}/aceitar").status_code)

        for latitude, longitude in ((-22.20, -49.90), (-22.21, -49.91)):
            response = self.client.post(
                f"/despacho/api/pedidos/{pedido['id']}/localizacoes",
                json={"latitude": latitude, "longitude": longitude, "precisao": 8.5},
            )
            self.assertEqual(200, response.status_code, response.get_json())

        route = self.client.get(
            f"/despacho/api/pedidos/{pedido['id']}/localizacoes"
        ).get_json()
        self.assertEqual([-22.20, -22.21], [point["latitude"] for point in route])

        self.login("admin")
        self.assertEqual(
            200, self.client.get(f"/despacho/api/pedidos/{pedido['id']}/localizacoes").status_code
        )
        self.login("solicitante")
        self.assertEqual(
            200, self.client.get(f"/despacho/api/pedidos/{pedido['id']}/localizacoes").status_code
        )
        self.login("outro_solicitante")
        self.assertEqual(
            403, self.client.get(f"/despacho/api/pedidos/{pedido['id']}/localizacoes").status_code
        )
        self.login("outro_entregador")
        self.assertEqual(
            403,
            self.client.post(
                f"/despacho/api/pedidos/{pedido['id']}/localizacoes",
                json={"latitude": -22.3, "longitude": -49.8},
            ).status_code,
        )

    def test_rejects_invalid_coordinates_and_locations_after_delivery(self):
        pedido = self.create_order()
        self.dispatch_order(pedido["id"])
        self.login("entregador")
        self.client.post(f"/despacho/api/pedidos/{pedido['id']}/aceitar")
        invalid = self.client.post(
            f"/despacho/api/pedidos/{pedido['id']}/localizacoes",
            json={"latitude": 100, "longitude": -49.9, "precisao": 5},
        )
        self.assertEqual(400, invalid.status_code)
        self.client.post(f"/despacho/api/pedidos/{pedido['id']}/retirada")
        self.client.post(f"/despacho/api/pedidos/{pedido['id']}/entrega")
        finished = self.client.post(
            f"/despacho/api/pedidos/{pedido['id']}/localizacoes",
            json={"latitude": -22.2, "longitude": -49.9, "precisao": 5},
        )
        self.assertEqual(400, finished.status_code)

    def test_late_delivery_requires_justification_and_reports_inconformity(self):
        pedido = self.create_order(urgencia="emergencia")
        self.dispatch_order(pedido["id"])
        self.login("entregador")
        self.client.post(f"/despacho/api/pedidos/{pedido['id']}/aceitar")
        self.client.post(f"/despacho/api/pedidos/{pedido['id']}/retirada")

        old_ts = int((time.time() - 3600) * 1000)
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE pedidos SET ts_solicitado=? WHERE id=?", (old_ts, pedido["id"]))
        con.commit()
        con.close()

        without_reason = self.client.post(f"/despacho/api/pedidos/{pedido['id']}/entrega")
        self.assertEqual(400, without_reason.status_code)

        delivered = self.client.post(
            f"/despacho/api/pedidos/{pedido['id']}/entrega",
            json={"justificativa_atraso": "Trânsito intenso na região central"},
        )
        self.assertEqual(200, delivered.status_code, delivered.get_json())
        self.assertEqual(
            "Trânsito intenso na região central", delivered.get_json()["justificativa_atraso"]
        )
        self.assertGreater(delivered.get_json()["sla"]["excedido_ms"], 0)

        self.login("admin")
        relatorio = self.client.get("/despacho/api/relatorios/inconformidades").get_json()
        self.assertEqual([delivered.get_json()["protocolo"]], [row["protocolo"] for row in relatorio])
        self.assertEqual("Entregador", relatorio[0]["entregador"])
        self.assertEqual("Solicitante", relatorio[0]["solicitante"])
        self.assertIn("Trânsito intenso", relatorio[0]["justificativa_atraso"])

    def test_daily_report_summarizes_orders(self):
        pedido = self.create_order()
        self.dispatch_order(pedido["id"])

        self.login("admin")
        resumo = self.client.get("/despacho/api/relatorios/resumo-diario").get_json()
        self.assertEqual(1, resumo["total"])
        self.assertEqual(1, resumo["em_andamento"])
        self.assertEqual(0, resumo["fora_sla"])

    def test_internal_chat_between_requester_and_admin(self):
        self.login("solicitante")
        sent = self.client.post("/despacho/api/chat", json={"texto": "Preciso de apoio na coleta"})
        self.assertEqual(200, sent.status_code, sent.get_json())
        requester_thread = self.client.get("/despacho/api/chat").get_json()
        self.assertEqual(["Preciso de apoio na coleta"], [msg["texto"] for msg in requester_thread])

        self.login("admin")
        admin_messages = self.client.get(
            f"/despacho/api/chat?solicitante_id={self.solicitante_id}"
        ).get_json()
        self.assertEqual("Solicitante", admin_messages[0]["remetente_nome"])

        reply = self.client.post(
            "/despacho/api/chat",
            json={"solicitante_id": self.solicitante_id, "texto": "Admin acompanhando."},
        )
        self.assertEqual(200, reply.status_code, reply.get_json())

        self.login("solicitante")
        requester_thread = self.client.get("/despacho/api/chat").get_json()
        self.assertEqual(
            ["Preciso de apoio na coleta", "Admin acompanhando."],
            [msg["texto"] for msg in requester_thread],
        )

    def test_admin_page_contains_complete_history_and_live_route(self):
        self.login("admin")
        html = self.client.get("/despacho/admin").get_data(as_text=True)
        for expected in (
            "listHistorico",
            "aceito_admin",
            "aceito_entregador",
            "localizacoes",
            "Cadastro de Unidades",
            "Cadastro de Usuários",
            "Cadastro de Entregadores",
            "Relatórios",
            "Chat interno",
            "Disponíveis agora",
            "leaflet",
            "15000",
        ):
            self.assertIn(expected, html)

    def test_requester_page_contains_only_public_times_and_live_route(self):
        self.login("solicitante")
        html = self.client.get("/despacho/solicitante").get_data(as_text=True)
        for expected in (
            "Solicitado às",
            "Entregue às",
            "Moto",
            "Carro",
            "ROTINA (12 horas)",
            "Chat interno",
            "localizacoes",
            "leaflet",
            "15000",
        ):
            self.assertIn(expected, html)
        self.assertNotIn("Aceito pelo admin", html)

    def test_driver_page_contains_availability_acceptance_and_geolocation(self):
        self.login("entregador")
        html = self.client.get("/despacho/entregador").get_data(as_text=True)
        for expected in (
            "disponibilidade",
            "data-aceitar",
            "geolocation",
            "15000",
            "Justificativa",
            "clt_desconto",
            "justificativa_atraso",
        ):
            self.assertIn(expected, html)


if __name__ == "__main__":
    unittest.main()
