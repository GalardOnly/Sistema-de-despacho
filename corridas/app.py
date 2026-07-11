import os

from flask import Flask, jsonify, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

if __package__:
    from .despacho import despacho_bp, init_db_desp, verificar_db_desp
    from .security import configurar_seguranca
else:
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
    if ambiente == "production":
        raise RuntimeError(
            "Produção bloqueada enquanto a persistência ainda usar SQLite. "
            "Conclua a migração PostgreSQL antes de definir APP_ENV=production."
        )
    if em_cloud_run and not _env_bool("ALLOW_EPHEMERAL_SQLITE"):
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
    app.config["SESSION_COOKIE_SECURE"] = False
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
