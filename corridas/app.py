import os

from flask import Flask, redirect, url_for

try:
    from .despacho import despacho_bp, init_db_desp
except ImportError:
    from despacho import despacho_bp, init_db_desp


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

app = Flask(__name__)
app.secret_key = carregar_app_secret()
app.register_blueprint(despacho_bp)


@app.route("/")
def index():
    return redirect(url_for("despacho.desp_home"))


@app.route("/login", methods=["GET", "POST"])
def login():
    return redirect(url_for("despacho.desp_login"))


init_db_desp()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
