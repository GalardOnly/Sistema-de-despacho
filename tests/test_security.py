import os
import unittest
from unittest.mock import Mock, patch

from corridas import security


class LoginRateLimiterTests(unittest.TestCase):
    def test_production_requires_shared_redis(self):
        limiter = security.LoginRateLimiter()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "REDIS_URL"):
                limiter.configurar("production")

    def test_redis_backend_uses_hashed_keys_and_atomic_script(self):
        limiter = security.LoginRateLimiter()
        redis_client = Mock()
        script = Mock(return_value=1)
        redis_client.register_script.return_value = script
        pipeline = Mock()
        pipeline.delete.return_value = pipeline
        pipeline.zrem.return_value = pipeline
        redis_client.pipeline.return_value = pipeline

        with patch.dict(os.environ, {"REDIS_URL": "redis://cache:6379/0"}, clear=False):
            with patch.object(security.redis.Redis, "from_url", return_value=redis_client):
                limiter.configurar("production")

        token = limiter.iniciar("192.0.2.10", "administrador")
        self.assertIsNotNone(token)
        chaves = script.call_args.kwargs["keys"]
        self.assertTrue(all("192.0.2.10" not in chave for chave in chaves))
        self.assertTrue(all("administrador" not in chave for chave in chaves))

        limiter.concluir(
            "192.0.2.10",
            "administrador",
            token,
            falhou=False,
            autenticado=True,
        )
        pipeline.delete.assert_called_once_with(chaves[0])
        pipeline.zrem.assert_called_once_with(chaves[1], token)
        pipeline.execute.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
