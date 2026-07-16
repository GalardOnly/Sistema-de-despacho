"""Rotas de autenticação e páginas principais."""

import hmac
import secrets

from flask import jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from ..config import (
    CSRF_HEADER,
    CSRF_SESSION_KEY,
    LIMITE_SENHA,
    LIMITE_USERNAME,
    METODOS_COM_MUTACAO,
    SESSION_VERSION_KEY,
)
from ..database import get_db_desp
from ..extensions import despacho_bp
from ..security import limitar_falhas_login
from .services import _limpar_sessao_desp, csrf_token, login_required_desp


@despacho_bp.context_processor
def contexto_csrf():
    return {"csrf_token": csrf_token}


@despacho_bp.before_request
def proteger_api_contra_csrf():
    if request.method not in METODOS_COM_MUTACAO:
        return None
    if not request.path.startswith("/despacho/api/"):
        return None
    if not session.get("desp_uid"):
        return None

    esperado = session.get(CSRF_SESSION_KEY)
    recebido = request.headers.get(CSRF_HEADER)
    if not esperado or not recebido or not hmac.compare_digest(esperado, recebido):
        return jsonify(error="token CSRF inválido"), 403
    return None


@despacho_bp.route("/login", methods=["GET", "POST"])
@limitar_falhas_login
def desp_login():
    erro = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        senha = request.form.get("senha") or ""
        con = get_db_desp()
        u = None
        if len(username) <= LIMITE_USERNAME and len(senha) <= LIMITE_SENHA:
            u = con.execute(
                "SELECT * FROM usuarios WHERE username=? AND ativo=1", (username,)
            ).fetchone()
        if not u or not check_password_hash(u["senha_hash"], senha):
            erro = "Usuário ou senha incorretos."
        else:
            session.clear()
            session["desp_uid"] = u["id"]
            session["desp_nome"] = u["nome"]
            session["desp_papel"] = u["papel"]
            session["desp_unidade_id"] = u["unidade_id"]
            session[SESSION_VERSION_KEY] = u["sessao_versao"]
            session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
            return redirect(url_for("despacho.desp_home"))
        return render_template("despacho/login.html", erro=erro), 401
    return render_template("despacho/login.html", erro=erro)


@despacho_bp.route("/logout")
def desp_logout():
    _limpar_sessao_desp()
    return redirect(url_for("despacho.desp_login"))


@despacho_bp.route("/")
@login_required_desp()
def desp_home():
    destino = {
        "admin": "despacho.desp_admin",
        "solicitante": "despacho.desp_solicitante",
        "entregador": "despacho.desp_entregador",
    }[session["desp_papel"]]
    return redirect(url_for(destino))


@despacho_bp.route("/admin")
@login_required_desp("admin")
def desp_admin():
    return render_template("despacho/admin.html", nome=session["desp_nome"])


@despacho_bp.route("/solicitante")
@login_required_desp("solicitante")
def desp_solicitante():
    con = get_db_desp()
    unidade = con.execute("SELECT nome FROM unidades WHERE id=?", (session["desp_unidade_id"],)).fetchone()
    return render_template(
        "despacho/solicitante.html",
        nome=session["desp_nome"],
        unidade=unidade["nome"] if unidade else "?",
    )


@despacho_bp.route("/entregador")
@login_required_desp("entregador")
def desp_entregador():
    return render_template("despacho/entregador.html", nome=session["desp_nome"])
