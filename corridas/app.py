import os, sqlite3, time
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo
from flask import (Flask, request, session, redirect, url_for,
                   render_template, jsonify, g)

APP_SECRET = os.environ.get("APP_SECRET", "troque-este-segredo-em-producao")
TEAM_CODE  = os.environ.get("TEAM_CODE", "1234")
TZ         = ZoneInfo("America/Sao_Paulo")
DB_PATH    = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "corridas.db"))

DEFAULT_CONFIG = {"valor_dia": 8.0, "valor_mad": 10.0, "mad_ini": 0, "mad_fim": 6}

app = Flask(__name__)
app.secret_key = APP_SECRET

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS corridas(
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts     INTEGER NOT NULL,
            periodo TEXT NOT NULL,
            valor  REAL NOT NULL,
            autor  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS config(
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        );
    """)
    for k, v in DEFAULT_CONFIG.items():
        con.execute("INSERT OR IGNORE INTO config(chave,valor) VALUES(?,?)", (k, str(v)))
    con.commit(); con.close()

def get_config():
    rows = db().execute("SELECT chave,valor FROM config").fetchall()
    c = dict(DEFAULT_CONFIG)
    for r in rows:
        c[r["chave"]] = float(r["valor"]) if r["chave"].startswith("valor") else int(float(r["valor"]))
    return c

def periodo_de(dt, cfg):
    h = dt.hour
    ini, fim = cfg["mad_ini"], cfg["mad_fim"]
    dentro = (ini <= h < fim) if ini <= fim else (h >= ini or h < fim)
    return "mad" if dentro else "dia"

def valor_de(p, cfg):
    return cfg["valor_mad"] if p == "mad" else cfg["valor_dia"]

def login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get("nome"):
            if request.path.startswith("/api/"):
                return jsonify(error="nao autenticado"), 401
            return redirect(url_for("login"))
        return f(*a, **k)
    return wrap

@app.route("/login", methods=["GET", "POST"])
def login():
    erro = None
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        codigo = (request.form.get("codigo") or "").strip()
        if not nome:
            erro = "Digite seu nome."
        elif codigo != TEAM_CODE:
            erro = "Código da equipe incorreto."
        else:
            session["nome"] = nome
            return redirect(url_for("index"))
    return render_template("login.html", erro=erro)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    return render_template("index.html", nome=session["nome"])

@app.route("/api/config", methods=["GET", "POST"])
@login_required
def api_config():
    if request.method == "POST":
        d = request.get_json(force=True)
        con = db()
        for k in DEFAULT_CONFIG:
            if k in d:
                con.execute("UPDATE config SET valor=? WHERE chave=?", (str(d[k]), k))
        con.commit()
        cfg = get_config()
        for r in con.execute("SELECT id,periodo FROM corridas").fetchall():
            con.execute("UPDATE corridas SET valor=? WHERE id=?",
                        (valor_de(r["periodo"], cfg), r["id"]))
        con.commit()
    return jsonify(get_config())

@app.route("/api/corridas", methods=["GET", "POST"])
@login_required
def api_corridas():
    con = db()
    if request.method == "POST":
        cfg = get_config()
        agora = datetime.now(TZ)
        p = periodo_de(agora, cfg)
        ts = int(time.time() * 1000)
        cur = con.execute(
            "INSERT INTO corridas(ts,periodo,valor,autor) VALUES(?,?,?,?)",
            (ts, p, valor_de(p, cfg), session["nome"]))
        con.commit()
        rid = cur.lastrowid
        return jsonify(id=rid, ts=ts, p=p, valor=valor_de(p, cfg), autor=session["nome"])
    rows = con.execute("SELECT id,ts,periodo,valor,autor FROM corridas ORDER BY ts DESC").fetchall()
    return jsonify([
        {"id": str(r["id"]), "ts": r["ts"], "p": r["periodo"], "valor": r["valor"], "autor": r["autor"]}
        for r in rows
    ])

@app.route("/api/corridas/<int:rid>", methods=["DELETE"])
@login_required
def api_delete(rid):
    con = db()
    con.execute("DELETE FROM corridas WHERE id=?", (rid,))
    con.commit()
    return jsonify(ok=True)

@app.route("/api/corridas/<int:rid>/toggle", methods=["POST"])
@login_required
def api_toggle(rid):
    con = db()
    r = con.execute("SELECT periodo FROM corridas WHERE id=?", (rid,)).fetchone()
    if not r:
        return jsonify(error="nao encontrada"), 404
    cfg = get_config()
    novo = "dia" if r["periodo"] == "mad" else "mad"
    con.execute("UPDATE corridas SET periodo=?, valor=? WHERE id=?",
                (novo, valor_de(novo, cfg), rid))
    con.commit()
    return jsonify(id=str(rid), p=novo, valor=valor_de(novo, cfg))

from despacho import despacho_bp, init_db_desp
app.register_blueprint(despacho_bp)

init_db()
init_db_desp()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
