"""Rotas administrativas de unidades, usuários e senhas."""

from flask import jsonify, request, session
from werkzeug.security import generate_password_hash

from ..auth.services import login_required_desp
from ..config import (
    LIMITE_CODIGO_REFERENCIA,
    LIMITE_NOME,
    LIMITE_SENHA,
    LIMITE_USERNAME,
    PAPEIS,
    TIPOS_VEICULO,
)
from ..database import ERROS_INTEGRIDADE, get_db_desp
from ..database.dispatch import _normalizar_veiculo
from ..extensions import despacho_bp
from ..pedidos.services import _entregador_ocupado


@despacho_bp.route("/api/unidades", methods=["GET", "POST"])
@login_required_desp()
def api_unidades():
    con = get_db_desp()
    if request.method == "POST":
        if session["desp_papel"] != "admin":
            return jsonify(error="apenas administrador"), 403
        nome = (request.get_json(force=True).get("nome") or "").strip()
        if not nome:
            return jsonify(error="nome obrigatório"), 400
        if len(nome) > LIMITE_NOME:
            return jsonify(error=f"nome deve ter no máximo {LIMITE_NOME} caracteres"), 400
        try:
            con.execute("INSERT INTO unidades(nome) VALUES(?)", (nome,))
            con.commit()
        except ERROS_INTEGRIDADE:
            return jsonify(error="unidade já cadastrada"), 400
    rows = con.execute("SELECT id, nome FROM unidades ORDER BY nome").fetchall()
    return jsonify([dict(r) for r in rows])


@despacho_bp.route("/api/usuarios", methods=["GET", "POST"])
@login_required_desp("admin")
def api_usuarios():
    con = get_db_desp()
    if request.method == "POST":
        d = request.get_json(force=True)
        nome = (d.get("nome") or "").strip()
        username = (d.get("username") or "").strip()
        senha = d.get("senha") or ""
        papel = d.get("papel")
        unidade_id = d.get("unidade_id")
        codigo_ref = (d.get("codigo_ref") or "").strip()
        tipo_veiculo = _normalizar_veiculo(d.get("tipo_veiculo"))

        if not (nome and username and senha and papel in PAPEIS):
            return jsonify(error="dados incompletos"), 400
        if len(nome) > LIMITE_NOME or len(username) > LIMITE_USERNAME:
            return jsonify(error="nome ou login excede o limite permitido"), 400
        if not 8 <= len(senha) <= LIMITE_SENHA:
            return jsonify(error=f"senha deve ter entre 8 e {LIMITE_SENHA} caracteres"), 400
        if len(codigo_ref) > LIMITE_CODIGO_REFERENCIA:
            return jsonify(error="código de referência excede o limite permitido"), 400
        if papel != "admin" and not codigo_ref:
            return jsonify(error="código de referência obrigatório"), 400
        if codigo_ref:
            duplicado = con.execute(
                "SELECT 1 FROM usuarios WHERE codigo_ref=? AND papel!='admin' AND ativo=1 LIMIT 1",
                (codigo_ref,),
            ).fetchone()
            if duplicado:
                return jsonify(error="código de referência já cadastrado"), 400
        if papel == "solicitante" and not unidade_id:
            return jsonify(error="solicitante precisa de uma unidade"), 400
        if papel == "entregador" and tipo_veiculo not in TIPOS_VEICULO:
            return jsonify(error="entregador precisa de tipo de veículo"), 400
        if papel != "solicitante":
            unidade_id = None
        if papel != "entregador":
            tipo_veiculo = None

        try:
            con.execute(
                "INSERT INTO usuarios(nome,username,senha_hash,papel,unidade_id,codigo_ref,tipo_veiculo) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    nome,
                    username,
                    generate_password_hash(senha),
                    papel,
                    unidade_id,
                    codigo_ref or None,
                    tipo_veiculo,
                ),
            )
            con.commit()
        except ERROS_INTEGRIDADE:
            return jsonify(error="username já existe"), 400

    rows = con.execute(
        """
        SELECT u.id, u.nome, u.username, u.papel, u.unidade_id,
               u.disponivel, u.codigo_ref, u.tipo_veiculo,
               u.indisponibilidade_justificativa, u.indisponibilidade_tipo, u.indisponibilidade_ts,
               un.nome AS unidade_nome,
               CASE WHEN EXISTS(
                   SELECT 1 FROM pedidos p
                   WHERE p.entregador_id=u.id
                     AND p.status IN ('aguardando_entregador','em_rota_retirada','em_rota','despachado','coletado')
               ) THEN 1 ELSE 0 END AS ocupado
        FROM usuarios u LEFT JOIN unidades un ON un.id = u.unidade_id
        WHERE u.papel != 'admin' AND u.ativo = 1
        ORDER BY u.papel, u.nome
        """
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@despacho_bp.route("/api/usuarios/<int:uid>", methods=["PATCH", "DELETE"])
@login_required_desp("admin")
def api_usuario_admin(uid):
    con = get_db_desp()
    usuario = con.execute(
        "SELECT * FROM usuarios WHERE id=? AND papel='entregador' AND ativo=1", (uid,)
    ).fetchone()
    if not usuario:
        return jsonify(error="entregador não encontrado"), 404

    if _entregador_ocupado(con, uid):
        return jsonify(error="entregador está em atendimento"), 400

    if request.method == "DELETE":
        con.execute(
            """
            UPDATE usuarios
            SET ativo=0,
                disponivel=0,
                sessao_versao=sessao_versao+1,
                username=username || '__apagado_' || id,
                codigo_ref=CASE
                    WHEN codigo_ref IS NULL THEN NULL
                    ELSE codigo_ref || '__apagado_' || id
                END
            WHERE id=? AND papel='entregador'
            """,
            (uid,),
        )
        con.commit()
        return jsonify(ok=True, id=uid)

    d = request.get_json(silent=True) or {}
    tipo_veiculo = _normalizar_veiculo(d.get("tipo_veiculo"))
    if tipo_veiculo not in TIPOS_VEICULO:
        return jsonify(error="tipo de veículo inválido"), 400

    con.execute("UPDATE usuarios SET tipo_veiculo=? WHERE id=?", (tipo_veiculo, uid))
    con.commit()
    row = con.execute(
        "SELECT id, nome, username, papel, unidade_id, disponivel, codigo_ref, tipo_veiculo "
        "FROM usuarios WHERE id=?",
        (uid,),
    ).fetchone()
    return jsonify(dict(row))


@despacho_bp.route("/api/logins")
@login_required_desp("admin")
def api_logins():
    con = get_db_desp()
    rows = con.execute(
        """
        SELECT u.id, u.nome, u.username, u.papel, u.unidade_id,
               u.codigo_ref, u.tipo_veiculo, un.nome AS unidade_nome
        FROM usuarios u
        LEFT JOIN unidades un ON un.id = u.unidade_id
        WHERE u.ativo = 1
        ORDER BY CASE u.papel
            WHEN 'admin' THEN 0
            WHEN 'solicitante' THEN 1
            WHEN 'entregador' THEN 2
            ELSE 3
        END, u.nome
        """
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@despacho_bp.route("/api/usuarios/<int:uid>/senha", methods=["POST"])
@login_required_desp("admin")
def api_alterar_senha_usuario(uid):
    con = get_db_desp()
    d = request.get_json(silent=True) or {}
    senha = (d.get("senha") or d.get("nova_senha") or "").strip()
    if not 8 <= len(senha) <= LIMITE_SENHA:
        return jsonify(error=f"senha deve ter entre 8 e {LIMITE_SENHA} caracteres"), 400

    usuario = con.execute(
        "SELECT id, username FROM usuarios WHERE id=? AND ativo=1", (uid,)
    ).fetchone()
    if not usuario:
        return jsonify(error="login não encontrado"), 404

    con.execute(
        "UPDATE usuarios SET senha_hash=?, sessao_versao=sessao_versao+1 WHERE id=?",
        (generate_password_hash(senha), uid),
    )
    con.commit()
    return jsonify(ok=True, id=usuario["id"], username=usuario["username"])
