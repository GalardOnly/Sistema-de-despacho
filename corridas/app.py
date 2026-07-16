import os

from flask import Flask, jsonify, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

if __package__:
    from .database import banco_postgres_configurado, usuario_postgres_runtime
    from .despacho import despacho_bp, init_db_desp, verificar_db_desp
    from .security import configurar_seguranca
else:
    from database import banco_postgres_configurado, usuario_postgres_runtime
    from despacho import despacho_bp, init_db_desp, verificar_db_desp
    from security import configurar_seguranca


SEGREDOS_INSEGUROS = {
    "troque-este-segredo-em-producao",
    "change-me",
    "changeme",
    "secret",
    "dev",
    "1234",
}


def parece_placeholder(valor):
    texto = valor.casefold()
    return texto in SEGREDOS_INSEGUROS or any(
        termo in texto for termo in ("troque", "change", "cole_aqui", "placeholder")
    )


def carregar_app_secret():
    secret = (os.environ.get("APP_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("Defina APP_SECRET com uma chave segura antes de iniciar a aplicação.")
    if len(secret) < 32 or parece_placeholder(secret):
        raise RuntimeError("APP_SECRET precisa ter pelo menos 32 caracteres e não pode ser um valor padrão.")
    return secret


def _env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().casefold() not in {"0", "false", "nao", "não", "off"}


def _ambiente():
    return (os.environ.get("APP_ENV") or "development").strip().casefold()


def validar_persistencia_cloud_run():
    ambiente = _ambiente()
    em_cloud_run = bool(os.environ.get("K_SERVICE"))
    postgres = banco_postgres_configurado()
    if ambiente == "production" and not postgres:
        raise RuntimeError(
            "Produção exige DATABASE_URL apontando para PostgreSQL."
        )
    if ambiente == "production" and usuario_postgres_runtime() != "despacho_app":
        raise RuntimeError(
            "Produção exige a role limitada despacho_app na DATABASE_URL."
        )
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

    init_db_desp()
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
