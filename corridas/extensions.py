"""Extensões compartilhadas pela aplicação Flask."""

from flask import Blueprint


despacho_bp = Blueprint(
    "despacho",
    __name__,
    url_prefix="/despacho",
    template_folder="templates",
)
