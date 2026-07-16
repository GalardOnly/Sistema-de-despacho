"""Controles de segurança e observabilidade compartilhados pela aplicação."""

import hashlib
import json
import logging
import os
import secrets
import sys
import threading
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import wraps

from flask import abort, g, jsonify, make_response, render_template, request, session
from flask.logging import default_handler
from werkzeug.exceptions import HTTPException

import redis
from redis.exceptions import RedisError


def _env_bool(nome, padrao=True):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().casefold() not in {"0", "false", "nao", "não", "off"}


def _env_int(nome, padrao, minimo=1, maximo=86400):
    try:
        valor = int(os.environ.get(nome, padrao))
    except (TypeError, ValueError):
        return padrao
    return min(max(valor, minimo), maximo)


class LoginRateLimiter:
    """Limita falhas de login em memória ou em um Redis compartilhado."""

    _RESERVAR_REDIS = """
    local agora = tonumber(ARGV[1])
    local token = ARGV[2]
    local janela_usuario = tonumber(ARGV[3])
    local janela_ip = tonumber(ARGV[4])
    local max_usuario = tonumber(ARGV[5])
    local max_ip = tonumber(ARGV[6])

    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', agora - janela_usuario)
    redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', agora - janela_ip)
    if redis.call('ZCARD', KEYS[1]) >= max_usuario then
        return 0
    end
    if redis.call('ZCARD', KEYS[2]) >= max_ip then
        return 0
    end

    redis.call('ZADD', KEYS[1], agora, token)
    redis.call('ZADD', KEYS[2], agora, token)
    redis.call('PEXPIRE', KEYS[1], janela_usuario)
    redis.call('PEXPIRE', KEYS[2], janela_ip)
    return 1
    """

    def __init__(self):
        self.max_usuario = _env_int("DESPACHO_LOGIN_MAX_USUARIO", 5, maximo=100)
        self.janela_usuario = _env_int(
            "DESPACHO_LOGIN_JANELA_USUARIO_SEG", 300, maximo=86400
        )
        self.max_ip = _env_int("DESPACHO_LOGIN_MAX_IP", 30, maximo=1000)
        self.janela_ip = _env_int("DESPACHO_LOGIN_JANELA_IP_SEG", 3600, maximo=86400)
        self._por_usuario = defaultdict(deque)
        self._por_ip = defaultdict(deque)
        self._lock = threading.Lock()
        self._ultima_limpeza = 0.0
        self._redis = None
        self._redis_script = None
        self._redis_obrigatorio = False

    def configurar(self, ambiente):
        url = (os.environ.get("REDIS_URL") or "").strip()
        self._redis_obrigatorio = ambiente == "production"
        if self._redis_obrigatorio and not url:
            raise RuntimeError("Produção exige REDIS_URL para o rate limit compartilhado.")
        if not url:
            self._redis = None
            self._redis_script = None
            return
        if not url.startswith(("redis://", "rediss://")):
            raise RuntimeError("REDIS_URL precisa usar redis:// ou rediss://.")
        self._redis = redis.Redis.from_url(
            url,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
        self._redis_script = self._redis.register_script(self._RESERVAR_REDIS)

    @staticmethod
    def _hash_chave(valor):
        return hashlib.sha256(valor.encode("utf-8")).hexdigest()

    def _chaves_redis(self, ip, usuario):
        ip_hash = self._hash_chave(ip)
        usuario_hash = self._hash_chave(f"{ip}:{usuario}")
        return (
            f"despacho:login:usuario:{usuario_hash}",
            f"despacho:login:ip:{ip_hash}",
        )

    def _iniciar_redis(self, ip, usuario):
        token = secrets.token_hex(16)
        chaves = self._chaves_redis(ip, usuario)
        try:
            permitido = self._redis_script(
                keys=chaves,
                args=(
                    int(time.time() * 1000),
                    token,
                    self.janela_usuario * 1000,
                    self.janela_ip * 1000,
                    self.max_usuario,
                    self.max_ip,
                ),
            )
        except RedisError as exc:
            raise RuntimeError("rate limit compartilhado indisponível") from exc
        return token if permitido == 1 else None

    @staticmethod
    def _podar(fila, limite):
        while fila and fila[0][1] <= limite:
            fila.popleft()

    def _limpar_expirados(self, agora):
        if agora - self._ultima_limpeza < 60:
            return
        for chave, fila in list(self._por_usuario.items()):
            self._podar(fila, agora - self.janela_usuario)
            if not fila:
                self._por_usuario.pop(chave, None)
        for chave, fila in list(self._por_ip.items()):
            self._podar(fila, agora - self.janela_ip)
            if not fila:
                self._por_ip.pop(chave, None)
        self._ultima_limpeza = agora

    def iniciar(self, ip, usuario):
        if self._redis is not None:
            return self._iniciar_redis(ip, usuario)
        agora = time.monotonic()
        chave_usuario = (ip, usuario)
        with self._lock:
            self._limpar_expirados(agora)
            fila_ip = self._por_ip.get(ip)
            if fila_ip is not None:
                self._podar(fila_ip, agora - self.janela_ip)
                if len(fila_ip) >= self.max_ip:
                    return None
            fila_usuario = self._por_usuario.get(chave_usuario)
            if fila_usuario is not None:
                self._podar(fila_usuario, agora - self.janela_usuario)
            if fila_usuario is not None and len(fila_usuario) >= self.max_usuario:
                return None
            token = secrets.token_hex(8)
            item = (token, agora)
            self._por_usuario[chave_usuario].append(item)
            self._por_ip[ip].append(item)
            return token

    @staticmethod
    def _remover_token(fila, token):
        return deque(item for item in fila if item[0] != token)

    def concluir(self, ip, usuario, token, falhou, autenticado=False):
        if self._redis is not None:
            if falhou:
                return
            chave_usuario, chave_ip = self._chaves_redis(ip, usuario)
            try:
                pipeline = self._redis.pipeline(transaction=True)
                if autenticado:
                    pipeline.delete(chave_usuario)
                else:
                    pipeline.zrem(chave_usuario, token)
                pipeline.zrem(chave_ip, token)
                pipeline.execute()
            except RedisError as exc:
                raise RuntimeError("rate limit compartilhado indisponível") from exc
            return
        if falhou:
            return
        chave_usuario = (ip, usuario)
        with self._lock:
            if autenticado:
                self._por_usuario.pop(chave_usuario, None)
            elif chave_usuario in self._por_usuario:
                fila = self._remover_token(self._por_usuario[chave_usuario], token)
                if fila:
                    self._por_usuario[chave_usuario] = fila
                else:
                    self._por_usuario.pop(chave_usuario, None)
            if ip in self._por_ip:
                fila = self._remover_token(self._por_ip[ip], token)
                if fila:
                    self._por_ip[ip] = fila
                else:
                    self._por_ip.pop(ip, None)

    def reset(self):
        with self._lock:
            self._por_usuario.clear()
            self._por_ip.clear()
            self._ultima_limpeza = 0.0


login_limiter = LoginRateLimiter()


def limitar_falhas_login(funcao):
    @wraps(funcao)
    def wrapper(*args, **kwargs):
        if request.method != "POST":
            return funcao(*args, **kwargs)
        ip = request.remote_addr or "ip-desconhecido"
        usuario = (request.form.get("username") or "").strip().casefold() or "sem-usuario"
        try:
            token = login_limiter.iniciar(ip, usuario)
        except RuntimeError:
            return render_template(
                "despacho/login.html",
                erro="Autenticação temporariamente indisponível. Tente novamente em instantes.",
            ), 503
        if token is None:
            abort(429)
        try:
            response = make_response(funcao(*args, **kwargs))
        except Exception:
            try:
                login_limiter.concluir(ip, usuario, token, falhou=False)
            except RuntimeError:
                pass
            raise
        falhou = response.status_code == 401
        autenticado = 300 <= response.status_code < 400
        try:
            login_limiter.concluir(ip, usuario, token, falhou, autenticado)
        except RuntimeError:
            session.clear()
            return render_template(
                "despacho/login.html",
                erro="Autenticação temporariamente indisponível. Tente novamente em instantes.",
            ), 503
        return response

    return wrapper


class JsonLogFormatter(logging.Formatter):
    """Formata eventos técnicos sem incluir corpo, formulário ou query string."""

    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        for campo in (
            "request_id",
            "user_id",
            "method",
            "path",
            "status",
            "duration_ms",
        ):
            valor = getattr(record, campo, None)
            if valor is not None:
                payload[campo] = valor
        if record.exc_info:
            tipo, _valor, pilha = record.exc_info
            payload["exception_type"] = tipo.__name__
            payload["traceback"] = [
                {
                    "file": frame.filename,
                    "line": frame.lineno,
                    "function": frame.name,
                }
                for frame in traceback.extract_tb(pilha)
            ]
        return json.dumps(payload, ensure_ascii=False)


def _log_extra(evento, **campos):
    dados = {
        "event": evento,
        "request_id": getattr(g, "request_id", None),
        "user_id": session.get("desp_uid"),
        "method": request.method,
        "path": request.path,
    }
    dados.update(campos)
    return dados


def _configurar_logging(app):
    if default_handler in app.logger.handlers:
        app.logger.removeHandler(default_handler)
    if not any(getattr(handler, "despacho_json", False) for handler in app.logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.despacho_json = True
        handler.setFormatter(JsonLogFormatter())
        app.logger.addHandler(handler)
    app.logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def _aplicar_headers(response):
    response.headers["X-Request-ID"] = g.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
    nonce = g.csp_nonce
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://unpkg.com; "
        "script-src-attr 'none'; "
        f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com https://unpkg.com; "
        "style-src-attr 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https://unpkg.com https://*.tile.openstreetmap.org; "
        "connect-src 'self'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.path.startswith("/despacho"):
        response.headers["Cache-Control"] = "no-store, private"
    return response


def configurar_seguranca(app):
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_env_bool("DESPACHO_COOKIE_SECURE", True),
    )
    login_limiter.configurar(app.config.get("APP_ENV", "development"))
    _configurar_logging(app)

    @app.before_request
    def iniciar_contexto_requisicao():
        g.request_id = secrets.token_hex(12)
        g.csp_nonce = secrets.token_urlsafe(24)
        g.request_started = time.perf_counter()

    @app.after_request
    def finalizar_requisicao(response):
        _aplicar_headers(response)
        duracao = round((time.perf_counter() - g.request_started) * 1000, 2)
        if response.status_code >= 400 or request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            nivel = logging.WARNING if response.status_code >= 400 else logging.INFO
            app.logger.log(
                nivel,
                "request_completed",
                extra=_log_extra(
                    "request_completed",
                    status=response.status_code,
                    duration_ms=duracao,
                ),
            )
        return response

    @app.errorhandler(429)
    def muitas_tentativas(_erro):
        mensagem = "Muitas tentativas. Aguarde alguns minutos e tente novamente."
        if request.path == "/despacho/login":
            return render_template("despacho/login.html", erro=mensagem), 429
        return jsonify(error=mensagem, request_id=g.request_id), 429

    @app.errorhandler(413)
    def corpo_muito_grande(_erro):
        mensagem = "Conteúdo enviado excede o limite permitido."
        if request.path.startswith("/despacho/api/"):
            return jsonify(error=mensagem, request_id=g.request_id), 413
        return render_template("despacho/erro.html", request_id=g.request_id), 413

    @app.errorhandler(Exception)
    def erro_nao_tratado(erro):
        if isinstance(erro, HTTPException):
            return erro
        app.logger.exception(
            "unhandled_exception",
            extra=_log_extra("unhandled_exception"),
        )
        if request.path.startswith("/despacho/api/"):
            return jsonify(error="erro interno", request_id=g.request_id), 500
        return render_template("despacho/erro.html", request_id=g.request_id), 500
