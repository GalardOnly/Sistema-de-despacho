"""Rotas de disponibilidade dos entregadores."""

from flask import jsonify, request, session

from ..auth.services import login_required_desp
from ..config import LIMITE_JUSTIFICATIVA, TIPOS_INDISPONIBILIDADE
from ..database import get_db_desp
from ..extensions import despacho_bp
from ..pedidos.services import _entregador_ocupado, agora_ms
from ..validation import dados_json, texto


@despacho_bp.route("/api/disponibilidade", methods=["GET", "POST"])
@login_required_desp("entregador")
def api_disponibilidade():
    con = get_db_desp()
    uid = session["desp_uid"]
    ocupado = _entregador_ocupado(con, uid)
    if request.method == "POST":
        d = dados_json()
        valor = d.get("disponivel")
        if not isinstance(valor, bool):
            return jsonify(error="disponibilidade inválida"), 400
        if ocupado:
            return jsonify(error="não é possível alterar a disponibilidade durante uma entrega"), 400
        if valor is False:
            justificativa = texto(d.get("justificativa")).strip()
            tipo = texto(d.get("tipo")).strip()
            if not justificativa or tipo not in TIPOS_INDISPONIBILIDADE:
                return jsonify(error="justificativa e tipo de indisponibilidade são obrigatórios"), 400
            if len(justificativa) > LIMITE_JUSTIFICATIVA:
                return jsonify(error="justificativa excede o limite permitido"), 400
            ts = agora_ms()
            con.execute(
                "INSERT INTO indisponibilidades_entregador(entregador_id,tipo,justificativa,ts) "
                "VALUES(?,?,?,?)",
                (uid, tipo, justificativa, ts),
            )
            con.execute(
                "UPDATE usuarios SET disponivel=0, indisponibilidade_justificativa=?, "
                "indisponibilidade_tipo=?, indisponibilidade_ts=? WHERE id=?",
                (justificativa, tipo, ts, uid),
            )
        else:
            con.execute(
                "UPDATE usuarios SET disponivel=1, indisponibilidade_justificativa=NULL, "
                "indisponibilidade_tipo=NULL, indisponibilidade_ts=NULL WHERE id=?",
                (uid,),
            )
        con.commit()
    row = con.execute(
        "SELECT disponivel, indisponibilidade_justificativa, indisponibilidade_tipo, "
        "indisponibilidade_ts FROM usuarios WHERE id=?",
        (uid,),
    ).fetchone()
    return jsonify(
        disponivel=bool(row["disponivel"]),
        ocupado=_entregador_ocupado(con, uid),
        indisponibilidade_justificativa=row["indisponibilidade_justificativa"],
        indisponibilidade_tipo=row["indisponibilidade_tipo"],
        indisponibilidade_ts=row["indisponibilidade_ts"],
    )
