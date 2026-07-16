import sqlite3
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


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
        self.original_admin_password = os.environ.get("DESPACHO_ADMIN_SENHA_INICIAL")
        despacho.DESP_DB_PATH = self.db_path
        os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = "senha-admin-testes-123"

    def tearDown(self):
        despacho.DESP_DB_PATH = self.original_db_path
        if self.original_admin_password is None:
            os.environ.pop("DESPACHO_ADMIN_SENHA_INICIAL", None)
        else:
            os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = self.original_admin_password
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
        chat_columns = {row[1] for row in con.execute("PRAGMA table_info(chat_mensagens)")}
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
                "urgencia_mista",
                "justificativa_atraso",
            }.issubset(order_columns)
        )
        self.assertIn("localizacoes_pedido", tables)
        self.assertIn("chat_mensagens", tables)
        self.assertIn("indisponibilidades_entregador", tables)
        self.assertIn("operadores_solicitante", tables)
        self.assertIn("tipos_coleta_unidade", tables)
        self.assertIn("notificacoes", tables)
        self.assertIn("operador_id", order_columns)
        self.assertIn("unidade_id", chat_columns)
        self.assertEqual(("aguardando_entregador", 2000, 2000), order)

    def test_initial_admin_password_must_be_configured_for_empty_database(self):
        os.environ.pop("DESPACHO_ADMIN_SENHA_INICIAL", None)
        Path(self.db_path).unlink(missing_ok=True)

        with self.assertRaisesRegex(RuntimeError, "DESPACHO_ADMIN_SENHA_INICIAL"):
            despacho.init_db_desp()

    def test_initial_admin_password_rejects_known_default(self):
        valores = ("mudar123", "defina_uma_senha_forte_para_o_admin")
        for senha in valores:
            with self.subTest(senha=senha):
                os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = senha
                Path(self.db_path).unlink(missing_ok=True)

                with self.assertRaisesRegex(RuntimeError, "DESPACHO_ADMIN_SENHA_INICIAL"):
                    despacho.init_db_desp()


class DispatchApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "despacho.db")
        self.original_db_path = despacho.DESP_DB_PATH
        self.original_admin_password = os.environ.get("DESPACHO_ADMIN_SENHA_INICIAL")
        despacho.DESP_DB_PATH = self.db_path
        os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = "senha-admin-testes-123"
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
            "INSERT INTO usuarios(nome,username,senha_hash,papel,unidade_id,codigo_ref) "
            "VALUES(?,?,?,?,?,?)",
            ("Solicitante 2", "solicitante2", "x", "solicitante", self.origem_id, "SOL-003"),
        )
        self.solicitante_mesma_unidade_id = con.execute(
            "SELECT id FROM usuarios WHERE username='solicitante2'"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO usuarios(nome,username,senha_hash,papel,unidade_id,codigo_ref) "
            "VALUES(?,?,?,?,?,?)",
            (
                "Solicitante Destino",
                "solicitante_destino",
                "x",
                "solicitante",
                self.destino_id,
                "SOL-DEST",
            ),
        )
        self.solicitante_destino_id = con.execute(
            "SELECT id FROM usuarios WHERE username='solicitante_destino'"
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
        self.raw_open = self.client.open

        def open_with_csrf(*args, **kwargs):
            method = (kwargs.get("method") or (args[1] if len(args) > 1 else "GET")).upper()
            path = args[0] if args else kwargs.get("path", "")
            if method in {"POST", "PUT", "PATCH", "DELETE"} and str(path).startswith("/despacho/api/"):
                headers = dict(kwargs.pop("headers", {}) or {})
                headers.setdefault("X-CSRF-Token", "csrf-test-token")
                kwargs["headers"] = headers
            return self.raw_open(*args, **kwargs)

        self.client.open = open_with_csrf

    def tearDown(self):
        despacho.DESP_DB_PATH = self.original_db_path
        if self.original_admin_password is None:
            os.environ.pop("DESPACHO_ADMIN_SENHA_INICIAL", None)
        else:
            os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = self.original_admin_password
        self.tempdir.cleanup()

    def login(self, papel):
        data = {
            "admin": (self.admin_id, None, "Administrador"),
            "solicitante": (self.solicitante_id, self.origem_id, "Solicitante"),
            "solicitante_mesma_unidade": (
                self.solicitante_mesma_unidade_id,
                self.origem_id,
                "Solicitante 2",
            ),
            "solicitante_destino": (
                self.solicitante_destino_id,
                self.destino_id,
                "Solicitante Destino",
            ),
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
            sess["desp_csrf_token"] = "csrf-test-token"

    def create_order(
        self,
        urgencia="rotina",
        tipo_veiculo="moto",
        operador_nome="Maria Operadora",
        urgencia_mista=False,
    ):
        self.login("solicitante")
        response = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Sangue",
                "urgencia": urgencia,
                "urgencia_mista": urgencia_mista,
                "tipo_veiculo": tipo_veiculo,
                "operador_nome": operador_nome,
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

    def concurrent_admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["desp_uid"] = self.admin_id
            sess["desp_nome"] = "Administrador"
            sess["desp_papel"] = "admin"
            sess["desp_unidade_id"] = None
            sess["desp_csrf_token"] = "csrf-test-token"
        return client

    def run_concurrent_dispatches(self, assignments):
        barrier = threading.Barrier(len(assignments))
        local_state = threading.local()
        original_now = despacho.agora_ms
        responses = []
        errors = []
        result_lock = threading.Lock()

        def synchronized_now():
            if not getattr(local_state, "dispatch_waited", False):
                local_state.dispatch_waited = True
                barrier.wait(timeout=10)
            return original_now()

        def dispatch(client, pedido_id, entregador_id):
            try:
                response = client.post(
                    f"/despacho/api/pedidos/{pedido_id}/despachar",
                    json={"entregador_id": entregador_id},
                    headers={"X-CSRF-Token": "csrf-test-token"},
                )
                with result_lock:
                    responses.append(response)
            except Exception as exc:
                with result_lock:
                    errors.append(exc)

        with patch.object(despacho, "agora_ms", side_effect=synchronized_now):
            threads = [
                threading.Thread(
                    target=dispatch,
                    args=(self.concurrent_admin_client(), pedido_id, entregador_id),
                )
                for pedido_id, entregador_id in assignments
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual([], errors)
        return responses

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
        short_password = self.client.post(
            "/despacho/api/usuarios",
            json={
                "nome": "Senha Curta",
                "username": "senha_curta",
                "senha": "1234567",
                "papel": "solicitante",
                "unidade_id": self.origem_id,
                "codigo_ref": "SOL-CURTA",
            },
        )
        self.assertEqual(400, short_password.status_code)
        self.assertIn("8 caracteres", short_password.get_json()["error"])

        missing_ref = self.client.post(
            "/despacho/api/usuarios",
            json={
                "nome": "Novo Solicitante",
                "username": "novo_sol",
                "senha": "senha123",
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
                "senha": "senha123",
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
                "senha": "senha123",
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
                "senha": "senha123",
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
                "senha": "senha123",
                "papel": "solicitante",
                "unidade_id": self.origem_id,
                "codigo_ref": "ENT-999",
            },
        )
        self.assertEqual(400, duplicate_ref.status_code)

    def test_mutating_api_requires_csrf_token(self):
        self.login("admin")

        blocked = self.raw_open(
            "/despacho/api/unidades",
            method="POST",
            json={"nome": "Unidade sem token"},
        )
        self.assertEqual(403, blocked.status_code)
        self.assertIn("CSRF", blocked.get_json()["error"])

        allowed = self.client.post("/despacho/api/unidades", json={"nome": "Unidade com token"})
        self.assertEqual(200, allowed.status_code, allowed.get_json())

    def test_order_requires_vehicle_and_uses_collection_serial(self):
        self.login("solicitante")
        missing_vehicle = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Sangue",
                "urgencia": "rotina",
                "operador_nome": "Operador Veículo",
            },
        )
        self.assertEqual(400, missing_vehicle.status_code)

        pedido = self.create_order(tipo_veiculo="moto")
        self.assertRegex(pedido["protocolo"], r"^COL-\d{5}$")
        self.assertEqual("moto", pedido["tipo_veiculo"])
        self.assertEqual(720, pedido["sla_limite_min"])
        self.assertFalse(pedido["urgencia_mista"])

    def test_mixed_urgencies_use_one_order_with_the_highest_selected_sla(self):
        pedido = self.create_order(urgencia="urgente", urgencia_mista=True)

        self.assertTrue(pedido["urgencia_mista"])
        self.assertEqual("urgente", pedido["urgencia"])
        self.assertEqual(40, pedido["sla_limite_min"])

        self.login("solicitante")
        invalid = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Sangue",
                "urgencia": "rotina",
                "urgencia_mista": "sim",
                "tipo_veiculo": "moto",
                "operador_nome": "Operador Inválido",
            },
        )
        self.assertEqual(400, invalid.status_code)

    def test_operator_name_is_required_and_internal_code_stays_hidden(self):
        self.login("solicitante")
        sem_operador = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Sangue",
                "urgencia": "rotina",
                "tipo_veiculo": "moto",
            },
        )
        self.assertEqual(400, sem_operador.status_code)

        pedido = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Sangue",
                "urgencia": "rotina",
                "tipo_veiculo": "moto",
                "operador_nome": "João da Recepção",
            },
        )
        self.assertEqual(200, pedido.status_code, pedido.get_json())
        self.assertEqual("João da Recepção", pedido.get_json()["operador_nome"])
        self.assertNotIn("operador_codigo", pedido.get_json())

        con = sqlite3.connect(self.db_path)
        row = con.execute(
            "SELECT nome, codigo FROM operadores_solicitante WHERE unidade_id=?",
            (self.origem_id,),
        ).fetchone()
        con.close()
        self.assertEqual("João da Recepção", row[0])
        self.assertRegex(row[1], r"^[a-f0-9]{64}$")

        self.login("outro_solicitante")
        outra_unidade = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Sangue",
                "urgencia": "rotina",
                "tipo_veiculo": "moto",
                "operador_nome": "João da Recepção",
            },
        )
        self.assertEqual(200, outra_unidade.status_code, outra_unidade.get_json())

    def test_custom_collection_type_is_saved_only_for_requester_unit(self):
        self.login("solicitante")
        tipos_iniciais = self.client.get("/despacho/api/tipos-exame")
        self.assertEqual(200, tipos_iniciais.status_code, tipos_iniciais.get_json())
        self.assertIn("Outro", tipos_iniciais.get_json())
        self.assertNotIn("Swab nasal", tipos_iniciais.get_json())

        pedido = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Outro",
                "tipo_outro": "Swab nasal",
                "urgencia": "rotina",
                "tipo_veiculo": "moto",
                "operador_nome": "Ana Técnica",
            },
        )
        self.assertEqual(200, pedido.status_code, pedido.get_json())
        self.assertEqual("Swab nasal", pedido.get_json()["tipo"])

        tipos_origem = self.client.get("/despacho/api/tipos-exame")
        self.assertIn("Swab nasal", tipos_origem.get_json())

        pedido_reutilizando_tipo = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Swab nasal",
                "urgencia": "rotina",
                "tipo_veiculo": "moto",
                "operador_nome": "Ana Técnica",
            },
        )
        self.assertEqual(200, pedido_reutilizando_tipo.status_code, pedido_reutilizando_tipo.get_json())

        self.login("outro_solicitante")
        tipos_outra_unidade = self.client.get("/despacho/api/tipos-exame")
        self.assertEqual(200, tipos_outra_unidade.status_code, tipos_outra_unidade.get_json())
        self.assertNotIn("Swab nasal", tipos_outra_unidade.get_json())

        bloqueado = self.client.post(
            "/despacho/api/pedidos",
            json={
                "destino_id": self.destino_id,
                "tipo": "Swab nasal",
                "urgencia": "rotina",
                "tipo_veiculo": "moto",
                "operador_nome": "Bruno Técnico",
            },
        )
        self.assertEqual(400, bloqueado.status_code)

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

    def test_concurrent_dispatch_of_same_order_has_only_one_winner(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "UPDATE usuarios SET tipo_veiculo='moto', disponivel=1 WHERE id=?",
            (self.outro_entregador_id,),
        )
        con.commit()
        con.close()
        pedido = self.create_order()

        responses = self.run_concurrent_dispatches(
            [
                (pedido["id"], self.entregador_id),
                (pedido["id"], self.outro_entregador_id),
            ]
        )

        self.assertEqual([200, 409], sorted(response.status_code for response in responses))
        con = sqlite3.connect(self.db_path)
        assigned_driver = con.execute(
            "SELECT entregador_id FROM pedidos WHERE id=?", (pedido["id"],)
        ).fetchone()[0]
        availability = dict(
            con.execute(
                "SELECT id, disponivel FROM usuarios WHERE id IN (?,?)",
                (self.entregador_id, self.outro_entregador_id),
            ).fetchall()
        )
        notification_count = con.execute(
            "SELECT COUNT(*) FROM notificacoes WHERE pedido_id=? AND tipo='despacho'",
            (pedido["id"],),
        ).fetchone()[0]
        con.close()
        self.assertEqual(0, availability[assigned_driver])
        unassigned_driver = (
            self.outro_entregador_id
            if assigned_driver == self.entregador_id
            else self.entregador_id
        )
        self.assertEqual(1, availability[unassigned_driver])
        self.assertEqual(2, notification_count)

    def test_concurrent_orders_cannot_reserve_same_driver(self):
        first = self.create_order()
        second = self.create_order(operador_nome="Outro operador")

        responses = self.run_concurrent_dispatches(
            [
                (first["id"], self.entregador_id),
                (second["id"], self.entregador_id),
            ]
        )

        self.assertEqual([200, 409], sorted(response.status_code for response in responses))
        con = sqlite3.connect(self.db_path)
        active_count = con.execute(
            "SELECT COUNT(*) FROM pedidos WHERE entregador_id=? "
            "AND status='aguardando_entregador'",
            (self.entregador_id,),
        ).fetchone()[0]
        waiting_count = con.execute(
            "SELECT COUNT(*) FROM pedidos WHERE id IN (?,?) AND status='solicitado'",
            (first["id"], second["id"]),
        ).fetchone()[0]
        con.close()
        self.assertEqual(1, active_count)
        self.assertEqual(1, waiting_count)

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

    def test_dispatch_accepts_legacy_vehicle_values_with_spaces_or_case(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "UPDATE usuarios SET tipo_veiculo=? WHERE id=?",
            (" Moto ", self.entregador_id),
        )
        con.commit()
        con.close()

        pedido = self.create_order(tipo_veiculo="moto")
        response = self.dispatch_order(pedido["id"])
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual("Entregador", response.get_json()["entregador"])

    def test_dispatch_defaults_legacy_driver_without_vehicle_to_motorcycle(self):
        con = sqlite3.connect(self.db_path)
        con.execute("UPDATE usuarios SET tipo_veiculo=NULL WHERE id=?", (self.entregador_id,))
        con.commit()
        con.close()

        pedido = self.create_order(tipo_veiculo="moto")
        response = self.dispatch_order(pedido["id"])
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual("Entregador", response.get_json()["entregador"])

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
        self.login("solicitante_destino")
        self.assertEqual([], self.client.get("/despacho/api/pedidos").get_json())
        self.assertEqual(
            403, self.client.get(f"/despacho/api/pedidos/{pedido['id']}/localizacoes").status_code
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

        self.login("admin")
        relatorio_em_aberto = self.client.get("/despacho/api/relatorios/inconformidades").get_json()
        self.assertEqual([], relatorio_em_aberto)

        self.login("entregador")
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

    def test_notifications_follow_dispatch_flow_by_role(self):
        pedido = self.create_order()

        self.login("admin")
        admin_feed = self.client.get("/despacho/api/notificacoes")
        self.assertEqual(200, admin_feed.status_code, admin_feed.get_json())
        self.assertEqual(1, admin_feed.get_json()["nao_lidas"])
        self.assertIn(
            "Novo pedido para retirada",
            [n["titulo"] for n in admin_feed.get_json()["notificacoes"]],
        )

        response = self.dispatch_order(pedido["id"])
        self.assertEqual(200, response.status_code, response.get_json())

        self.login("entregador")
        driver_feed = self.client.get("/despacho/api/notificacoes").get_json()
        self.assertEqual(1, driver_feed["nao_lidas"])
        self.assertIn("Novo pedido atribuído", [n["titulo"] for n in driver_feed["notificacoes"]])

        marked = self.client.post(
            f"/despacho/api/notificacoes/{driver_feed['notificacoes'][0]['id']}/lida"
        )
        self.assertEqual(200, marked.status_code, marked.get_json())
        self.assertEqual(0, self.client.get("/despacho/api/notificacoes").get_json()["nao_lidas"])

        accepted = self.client.post(f"/despacho/api/pedidos/{pedido['id']}/aceitar")
        self.assertEqual(200, accepted.status_code, accepted.get_json())

        self.login("solicitante")
        requester_feed = self.client.get("/despacho/api/notificacoes").get_json()
        titulos_solicitante = [n["titulo"] for n in requester_feed["notificacoes"]]
        self.assertIn("Pedido aceito pelo admin", titulos_solicitante)
        self.assertIn("Entregador a caminho", titulos_solicitante)

        self.login("outro_solicitante")
        self.assertEqual(0, self.client.get("/despacho/api/notificacoes").get_json()["nao_lidas"])

        self.login("entregador")
        retirada = self.client.post(f"/despacho/api/pedidos/{pedido['id']}/retirada")
        self.assertEqual(200, retirada.status_code, retirada.get_json())

        self.login("admin")
        admin_titles = [n["titulo"] for n in self.client.get("/despacho/api/notificacoes").get_json()["notificacoes"]]
        self.assertIn("Exame retirado", admin_titles)

        self.login("solicitante")
        requester_titles = [n["titulo"] for n in self.client.get("/despacho/api/notificacoes").get_json()["notificacoes"]]
        self.assertIn("Exame retirado", requester_titles)

    def test_read_notifications_are_removed_from_feed(self):
        self.create_order()

        self.login("admin")
        feed = self.client.get("/despacho/api/notificacoes").get_json()
        self.assertEqual(1, feed["nao_lidas"])
        self.assertEqual(1, len(feed["notificacoes"]))

        marked = self.client.post(f"/despacho/api/notificacoes/{feed['notificacoes'][0]['id']}/lida")
        self.assertEqual(200, marked.status_code, marked.get_json())

        after = self.client.get("/despacho/api/notificacoes").get_json()
        self.assertEqual(0, after["nao_lidas"])
        self.assertEqual([], after["notificacoes"])

    def test_internal_chat_is_isolated_by_unit_and_shared_by_same_unit_logins(self):
        self.login("solicitante")
        sent = self.client.post("/despacho/api/chat", json={"texto": "Preciso de apoio na coleta"})
        self.assertEqual(200, sent.status_code, sent.get_json())
        self.assertEqual(self.origem_id, sent.get_json()["unidade_id"])
        requester_thread = self.client.get("/despacho/api/chat").get_json()
        self.assertEqual(["Preciso de apoio na coleta"], [msg["texto"] for msg in requester_thread])

        self.login("solicitante_mesma_unidade")
        same_unit_thread = self.client.get("/despacho/api/chat").get_json()
        self.assertEqual(["Preciso de apoio na coleta"], [msg["texto"] for msg in same_unit_thread])

        self.login("outro_solicitante")
        other_unit_thread = self.client.get("/despacho/api/chat").get_json()
        self.assertEqual([], other_unit_thread)

        self.login("admin")
        admin_messages = self.client.get(f"/despacho/api/chat?unidade_id={self.origem_id}").get_json()
        self.assertEqual("Solicitante", admin_messages[0]["remetente_nome"])
        self.assertEqual("Origem", admin_messages[0]["unidade"])

        all_admin_messages = self.client.get("/despacho/api/chat").get_json()
        self.assertEqual(["Preciso de apoio na coleta"], [msg["texto"] for msg in all_admin_messages])

        reply = self.client.post(
            "/despacho/api/chat",
            json={"unidade_id": self.origem_id, "texto": "Admin acompanhando."},
        )
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(self.origem_id, reply.get_json()["unidade_id"])

        self.login("solicitante")
        requester_thread = self.client.get("/despacho/api/chat").get_json()
        self.assertEqual(
            ["Preciso de apoio na coleta", "Admin acompanhando."],
            [msg["texto"] for msg in requester_thread],
        )

        self.login("outro_solicitante")
        other_unit_thread = self.client.get("/despacho/api/chat").get_json()
        self.assertEqual([], other_unit_thread)

    def test_admin_chat_summary_groups_received_messages_by_unit(self):
        self.login("solicitante")
        first = self.client.post("/despacho/api/chat", json={"texto": "Primeira mensagem"})
        self.assertEqual(200, first.status_code, first.get_json())
        second = self.client.post("/despacho/api/chat", json={"texto": "Segunda mensagem"})
        self.assertEqual(200, second.status_code, second.get_json())

        self.login("admin")
        reply = self.client.post(
            "/despacho/api/chat",
            json={"unidade_id": self.origem_id, "texto": "Resposta do admin"},
        )
        self.assertEqual(200, reply.status_code, reply.get_json())

        self.login("outro_solicitante")
        other = self.client.post("/despacho/api/chat", json={"texto": "Mensagem de outra unidade"})
        self.assertEqual(200, other.status_code, other.get_json())

        self.login("admin")
        resumo = self.client.get("/despacho/api/chat/resumo")
        self.assertEqual(200, resumo.status_code, resumo.get_json())
        por_unidade = {row["unidade_id"]: row for row in resumo.get_json()}
        self.assertEqual(2, por_unidade[self.origem_id]["recebidas"])
        self.assertEqual(3, por_unidade[self.origem_id]["total"])
        self.assertEqual(1, por_unidade[self.outra_unidade_id]["recebidas"])
        self.assertEqual(1, por_unidade[self.outra_unidade_id]["total"])

    def test_chat_messages_create_notifications_for_the_other_side(self):
        self.login("solicitante")
        sent = self.client.post("/despacho/api/chat", json={"texto": "Pode verificar essa coleta?"})
        self.assertEqual(200, sent.status_code, sent.get_json())

        self.login("admin")
        admin_feed = self.client.get("/despacho/api/notificacoes")
        self.assertEqual(200, admin_feed.status_code, admin_feed.get_json())
        self.assertEqual(1, admin_feed.get_json()["nao_lidas"])
        self.assertEqual("Nova mensagem no chat", admin_feed.get_json()["notificacoes"][0]["titulo"])
        self.assertIn("Origem", admin_feed.get_json()["notificacoes"][0]["mensagem"])

        reply = self.client.post(
            "/despacho/api/chat",
            json={"unidade_id": self.origem_id, "texto": "Vou acompanhar por aqui."},
        )
        self.assertEqual(200, reply.status_code, reply.get_json())

        self.login("solicitante")
        requester_feed = self.client.get("/despacho/api/notificacoes").get_json()
        self.assertEqual(1, requester_feed["nao_lidas"])
        self.assertEqual("Mensagem do administrador", requester_feed["notificacoes"][0]["titulo"])

        self.login("outro_solicitante")
        self.assertEqual(0, self.client.get("/despacho/api/notificacoes").get_json()["nao_lidas"])

    def test_admin_lists_logins_and_resets_any_password(self):
        self.login("admin")
        logins = self.client.get("/despacho/api/logins")
        self.assertEqual(200, logins.status_code, logins.get_json())
        usernames = {row["username"] for row in logins.get_json()}
        self.assertTrue({"admin", "solicitante", "entregador"}.issubset(usernames))

        empty = self.client.post(
            f"/despacho/api/usuarios/{self.solicitante_id}/senha",
            json={"senha": ""},
        )
        self.assertEqual(400, empty.status_code)

        changed = self.client.post(
            f"/despacho/api/usuarios/{self.solicitante_id}/senha",
            json={"senha": "nova1234"},
        )
        self.assertEqual(200, changed.status_code, changed.get_json())

        with self.client.session_transaction() as sess:
            sess.clear()
        logged = self.client.post(
            "/despacho/login",
            data={"username": "solicitante", "senha": "nova1234"},
        )
        self.assertEqual(302, logged.status_code)
        with self.client.session_transaction() as sess:
            self.assertEqual(self.solicitante_id, sess["desp_uid"])

        self.login("admin")
        changed_admin = self.client.post(
            f"/despacho/api/usuarios/{self.admin_id}/senha",
            json={"senha": "adminnova"},
        )
        self.assertEqual(200, changed_admin.status_code, changed_admin.get_json())

    def test_admin_updates_and_removes_driver(self):
        self.login("admin")
        changed = self.client.patch(
            f"/despacho/api/usuarios/{self.entregador_id}",
            json={"tipo_veiculo": "carro"},
        )
        self.assertEqual(200, changed.status_code, changed.get_json())
        self.assertEqual("carro", changed.get_json()["tipo_veiculo"])

        users = self.client.get("/despacho/api/usuarios").get_json()
        driver = next(row for row in users if row["id"] == self.entregador_id)
        self.assertEqual("carro", driver["tipo_veiculo"])

        pedido = self.create_order(tipo_veiculo="carro")
        response = self.dispatch_order(pedido["id"])
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertEqual("Entregador", response.get_json()["entregador"])

        self.login("admin")
        busy_delete = self.client.delete(f"/despacho/api/usuarios/{self.entregador_id}")
        self.assertEqual(400, busy_delete.status_code)

        removed = self.client.delete(f"/despacho/api/usuarios/{self.outro_entregador_id}")
        self.assertEqual(200, removed.status_code, removed.get_json())
        users = self.client.get("/despacho/api/usuarios").get_json()
        self.assertNotIn(self.outro_entregador_id, [row["id"] for row in users])

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
            "chatUnidade",
            "Barra de mensagens",
            "unidade_id",
            "?unidade_id=",
            "notifButton",
            "notifBadge",
            "notifPanel",
            "/api/notificacoes",
            "Notification",
            "DESPACHO_CSRF_TOKEN",
            "despachoFetchOptions",
            "X-CSRF-Token",
            "normalizarVeiculo",
            "/api/chat/resumo",
            "mensagens recebidas",
            "Alterar senhas",
            "btnResetSenha",
            "/api/logins",
            "Solicitado por",
            "driverVehicle-",
            "data-driver-save",
            "data-driver-delete",
            "/api/usuarios/",
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
            "brand-logo",
            "/static/img/unimed.png",
            "--primary:#009962",
            "Nome de quem está solicitando",
            "operadorNome",
            "operador_nome",
            "tipoOutroBox",
            "tipoOutro",
            "tipo_outro",
            "urgenciaMista",
            "Esta coleta reúne exames com urgências diferentes",
            "Maior nível de urgência presente",
            "PRIORIDADES MISTAS",
            "/api/tipos-exame",
            "notifButton",
            "notifBadge",
            "notifPanel",
            "/api/notificacoes",
            "Notification",
            "DESPACHO_CSRF_TOKEN",
            "despachoFetchOptions",
            "X-CSRF-Token",
            "localizacoes",
            "leaflet",
            "15000",
            "pedidos-scroll",
            "max-height:620px",
            "overflow-y:auto",
            "module-tabs",
            "data-module=\"solicitante\"",
            "Cadastro de Pacientes",
            "Sistema de Estoque",
            "Fale com o administrador",
            "floatingChatPanel",
            "pacienteNome",
            "CPF/cartão",
            "pacientesMvpLista",
            "estoqueResumo",
            "estoqueTabela",
            "Itens críticos",
            "MVP visual",
        ):
            self.assertIn(expected, html)
        self.assertNotIn('id="selOperador"', html)
        self.assertNotIn("operatorGate", html)
        self.assertNotIn("btnLoginOperador", html)
        self.assertNotIn("Código gerado", html)
        self.assertNotIn("/api/operadores/login", html)
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
            "Justificativa de atraso",
            "rascunhosJustificativa",
            "salvarRascunhosJustificativa",
            "data-justificativa-atraso",
            "notifButton",
            "notifBadge",
            "notifPanel",
            "/api/notificacoes",
            "Notification",
            "DESPACHO_CSRF_TOKEN",
            "despachoFetchOptions",
            "X-CSRF-Token",
        ):
            self.assertIn(expected, html)
        self.assertNotIn("<label>justificativa_atraso</label>", html)


if __name__ == "__main__":
    unittest.main()
