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

os.environ["APP_SECRET"] = "troque-por-uma-chave-segura"
os.environ["DESPACHO_ADMIN_SENHA_INICIAL"] = "troque-a-senha-inicial"

from corridas.app import app as application
```

Depois clique em Reload.

## 4. Primeiro acesso

Abra:

```text
https://galardonly.pythonanywhere.com/despacho/login
```

Usuário inicial:

- usuário: `admin`
- senha: `mudar123`, ou o valor definido em `DESPACHO_ADMIN_SENHA_INICIAL`

A senha inicial só é usada quando o banco ainda não tem nenhum usuário cadastrado.

## 5. Arquivos que não devem ir para o Git

O `.gitignore` já deixa fora:

- bancos SQLite locais;
- arquivos `.env`;
- chaves e certificados;
- logs;
- caches Python;
- ZIPs gerados para upload manual.

Se precisar preservar o banco de produção, faça backup pela área de arquivos do PythonAnywhere antes de qualquer troca estrutural.
