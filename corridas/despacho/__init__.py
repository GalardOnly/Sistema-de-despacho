"""Fachada compatível do módulo de despacho.

As implementações ficam separadas por domínio. A fachada mantém os nomes usados
pelos scripts de inicialização e por integrações existentes.
"""

from ..config import DESP_DB_PATH, LIMITE_TEXTO_CHAT, senha_admin_inicial_configurada
from ..database import get_db_desp, init_db_desp, verificar_db_desp
from ..extensions import despacho_bp
from ..pedidos.services import agora_ms

from ..admin import routes as _admin_routes
from ..auth import routes as _auth_routes
from ..chat import routes as _chat_routes
from ..entregadores import routes as _entregadores_routes
from ..notificacoes import routes as _notificacoes_routes
from ..pedidos import routes as _pedidos_routes
from ..relatorios import routes as _relatorios_routes


__all__ = [
    "DESP_DB_PATH",
    "LIMITE_TEXTO_CHAT",
    "agora_ms",
    "despacho_bp",
    "get_db_desp",
    "init_db_desp",
    "senha_admin_inicial_configurada",
    "verificar_db_desp",
]
