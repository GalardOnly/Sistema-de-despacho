# Migrations de banco

Esta pasta receberá as migrations Alembic da futura base PostgreSQL.

O esquema SQLite atual é criado e atualizado dentro de `corridas/despacho/__init__.py`. Ele não deve ser convertido automaticamente em produção porque contém comandos e comportamentos específicos do SQLite.

Antes da primeira migration PostgreSQL devem ser definidos:

- modelos e tipos de dados;
- chaves públicas UUID;
- isolamento por unidade;
- índices e restrições de unicidade;
- trilha de auditoria;
- estratégia de migração e rollback;
- retenção de chat, notificações, GPS e dados de pacientes.
