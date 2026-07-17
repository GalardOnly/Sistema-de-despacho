"""Fábrica e ponto de entrada da aplicação Flask."""

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corridas.config import ambiente as _ambiente
from corridas.config import carregar_app_secret, env_bool as _env_bool, env_int as _env_int
from corridas.config import parece_placeholder
from corridas.database import banco_postgres_configurado, usuario_postgres_runtime
from corridas.despacho import despacho_bp, verificar_db_desp
from corridas.security import configurar_seguranca


def validar_persistencia_cloud_run():
    ambiente_atual = _ambiente()
    em_cloud_run = bool(os.environ.get("K_SERVICE"))
    postgres = banco_postgres_configurado()
    if ambiente_atual == "production" and not postgres:
        raise RuntimeError("Produção exige DATABASE_URL apontando para PostgreSQL.")
    if ambiente_atual == "production" and usuario_postgres_runtime() != "despacho_app":
        raise RuntimeError("Produção exige a role limitada despacho_app na DATABASE_URL.")
    if em_cloud_run and not postgres and not _env_bool("ALLOW_EPHEMERAL_SQLITE"):
        raise RuntimeError(
            "Cloud Run com SQLite exige ALLOW_EPHEMERAL_SQLITE=1 e deve ser usado "
            "somente em homologação com dados fictícios."
        )


def criar_app():
    validar_persistencia_cloud_run()
    flask_app = Flask(__name__)
    flask_app.secret_key = carregar_app_secret()
    flask_app.config["APP_ENV"] = _ambiente()
    flask_app.config["MAX_CONTENT_LENGTH"] = _env_int(
        "MAX_CONTENT_LENGTH_BYTES", 65536, 4096, 1048576
    )
    if _env_bool("TRUST_PROXY_HEADERS", flask_app.config["APP_ENV"] != "development"):
        flask_app.wsgi_app = ProxyFix(
            flask_app.wsgi_app,
            x_for=1,
            x_proto=1,
            x_host=1,
            x_port=1,
        )
    configurar_seguranca(flask_app)
    flask_app.register_blueprint(despacho_bp)

    @flask_app.route("/")
    def index():
        return redirect(url_for("despacho.desp_home"))

    @flask_app.route("/login", methods=["GET", "POST"])
    def login():
        return redirect(url_for("despacho.desp_login"))

    @flask_app.get("/healthz")
    def healthz():
        return jsonify(status="ok", service="sistema-despacho"), 200

    @flask_app.get("/readyz")
    def readyz():
        try:
            verificar_db_desp()
        except Exception:
            flask_app.logger.exception("readiness_database_failed")
            return jsonify(status="indisponivel"), 503
        return jsonify(status="pronto"), 200

    return flask_app


app = criar_app()


if __name__ == "__main__":
    debug = _env_bool("FLASK_DEBUG", False)
    if debug and _ambiente() != "development":
        raise RuntimeError("FLASK_DEBUG só pode ser ativado em desenvolvimento.")
    if _ambiente() == "development":
        app.config["SESSION_COOKIE_SECURE"] = False
    app.run(
        debug=debug,
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
    )
