# Deploy no PythonAnywhere

Este projeto roda como uma aplicação Flask simples. O banco SQLite é criado automaticamente no primeiro acesso.

## 1. Enviar os arquivos

No PythonAnywhere, envie o repositório para uma pasta como:

```text
/home/GalardOnly/Sistema-de-despacho
```

Se preferir usar Git no console do PythonAnywhere:

```bash
git clone https://github.com/GalardOnly/Sistema-de-despacho.git
cd Sistema-de-despacho
```

## 2. Instalar dependências

No console do PythonAnywhere:

```bash
pip install -r requirements.txt
```

Se estiver usando virtualenv, ative o ambiente antes do comando.

Gere uma chave segura para o Flask:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Guarde esse valor para usar no `APP_SECRET`.

## 3. Configurar o app web

Na aba Web do PythonAnywhere:

1. Crie ou edite o app web.
2. Escolha Flask/Python.
3. Aponte o arquivo WSGI para importar `app` de `corridas/app.py`.

Exemplo de trecho WSGI:

```python
import os
import sys

project_home = "/home/GalardOnly/Sistema-de-despacho"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

os.environ["APP_SECRET"] = "cole_aqui_a_chave_gerada_com_secrets"
os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = "defina_uma_senha_forte_para_o_admin"

from corridas.app import app as application
```

Depois clique em Reload.

Não use os textos de exemplo literalmente. A aplicação não inicia sem `APP_SECRET`, e o primeiro administrador não é criado sem `DESPACHO_ADMIN_SENHA_INICIAL`.

## 4. Segurança do MVP

O rate limit do MVP faz parte do próprio projeto e usa memória do processo. Ele não exige biblioteca adicional. Na futura infraestrutura com múltiplos processos, substitua essa implementação por um limitador distribuído com Redis.

Os limites padrão são 5 falhas em 300 segundos por combinação de IP e usuário, e 30 falhas em 3600 segundos por IP. Os valores podem ser ajustados com `DESPACHO_LOGIN_MAX_USUARIO`, `DESPACHO_LOGIN_JANELA_USUARIO_SEG`, `DESPACHO_LOGIN_MAX_IP` e `DESPACHO_LOGIN_JANELA_IP_SEG`.

Em uma execução local sem HTTPS, defina `DESPACHO_COOKIE_SECURE=0`. No PythonAnywhere e em produção, mantenha o valor padrão seguro.

## 5. Primeiro acesso

Abra:

```text
https://galardonly.pythonanywhere.com/despacho/login
```

Usuário inicial:

- usuário: `admin`
- senha: valor definido em `DESPACHO_ADMIN_SENHA_INICIAL`

A senha inicial só é usada quando o banco ainda não tem nenhum usuário cadastrado.

## 6. Arquivos que não devem ir para o Git

O `.gitignore` já deixa fora:

- bancos SQLite locais;
- arquivos `.env`;
- chaves e certificados;
- logs;
- caches Python;
- ZIPs gerados para upload manual.

Se precisar preservar o banco de produção, faça backup pela área de arquivos do PythonAnywhere antes de qualquer troca estrutural.
