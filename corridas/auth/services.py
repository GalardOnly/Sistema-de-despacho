"""Autorização, sessão e proteção CSRF do módulo de despacho."""

import secrets
from functools import wraps

from flask import jsonify, redirect, request, session, url_for

from ..config import CSRF_SESSION_KEY, SESSION_VERSION_KEY
from ..database import get_db_desp


def _limpar_sessao_desp():
    for chave in (
        "desp_uid",
        "desp_nome",
        "desp_papel",
        "desp_unidade_id",
        SESSION_VERSION_KEY,
        CSRF_SESSION_KEY,
    ):
        session.pop(chave, None)


def _sessao_desp_valida():
    uid = session.get("desp_uid")
    versao = session.get(SESSION_VERSION_KEY)
    if not uid or versao is None:
        return False

    usuario = get_db_desp().execute(
        "SELECT id,nome,papel,unidade_id,ativo,sessao_versao FROM usuarios WHERE id=?",
        (uid,),
    ).fetchone()
    if not usuario or not usuario["ativo"] or usuario["sessao_versao"] != versao:
        return False

    session["desp_nome"] = usuario["nome"]
    session["desp_papel"] = usuario["papel"]
    session["desp_unidade_id"] = usuario["unidade_id"]
    return True


def _resposta_nao_autenticado():
    _limpar_sessao_desp()
    if request.path.startswith("/despacho/api/"):
        return jsonify(error="não autenticado"), 401
    return redirect(url_for("despacho.desp_login"))


def login_required_desp(*papeis_permitidos):
    def decorator(f):
        @wraps(f)
        def wrap(*a, **k):
            if not _sessao_desp_valida():
                return _resposta_nao_autenticado()
            if papeis_permitidos and session.get("desp_papel") not in papeis_permitidos:
                return jsonify(error="sem permissão para este papel"), 403
            return f(*a, **k)

        return wrap

    return decorator


def csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token
