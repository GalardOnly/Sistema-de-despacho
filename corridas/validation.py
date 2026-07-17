"""Normaliza dados recebidos antes de aplicar as regras de negócio."""

from flask import request


def dados_json():
    dados = request.get_json(silent=True)
    return dados if isinstance(dados, dict) else {}


def texto(valor):
    return valor if isinstance(valor, str) else ""
