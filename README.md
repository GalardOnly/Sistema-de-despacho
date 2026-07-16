# Sistema de Despacho de Coletas

MVP em Flask + SQLite para despacho de coletas de exames, com perfis de administrador, solicitante e entregador.

## Principais recursos

- Cadastro de unidades, solicitantes e entregadores.
- Código de referência/ID funcional para usuários operacionais.
- Pedidos com número de série `COL-XXXXX`.
- Seleção de veículo necessário: moto ou carro.
- Despacho com validação de disponibilidade, ocupação e compatibilidade de veículo.
- Fluxo operacional com retirada física antes de considerar o pedido despachado.
- Rastreamento por GPS via polling.
- SLA por urgência:
  - ROTINA (12 horas)
  - URGENTE (40 min)
  - EMERGÊNCIA (15 min)
- Justificativa obrigatória para atraso fora do SLA.
- Relatórios de resumo diário e inconformidades mensais.
- Chat interno entre solicitante e administrador.
- Proteção CSRF nas APIs mutáveis e limitação de falhas no login.
- Headers de segurança, cookies protegidos e respostas sem cache.
- Logs técnicos em JSON com request ID e tratamento seguro de erros internos.

## Estrutura

```text
corridas/
  app.py
  security.py
  despacho/
    __init__.py
    DEPLOY.md
    templates/despacho/
database/
  backups/
  migrations/
docs/
instance/
logs/
scripts/
static/
Dockerfile
gunicorn.conf.py
cloudbuild.yaml
```

As pastas `database`, `instance`, `logs`, `scripts` e `static` ficam preparadas para próximas fases. Arquivos locais de banco, logs, ZIPs e segredos ficam fora do Git pelo `.gitignore`.

## Rodando localmente

```bash
pip install -r requirements.txt
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copie o valor gerado e configure as variáveis antes de iniciar:

```powershell
$env:APP_SECRET = "cole_aqui_a_chave_gerada"
$env:DESPACHO_ADMIN_SENHA_INICIAL = "defina_uma_senha_forte_para_o_admin"
python corridas/app.py
```

Acesse:

```text
http://localhost:5000/despacho/login
```

Usuário inicial:

- usuário: `admin`
- senha: valor definido em `DESPACHO_ADMIN_SENHA_INICIAL`

O sistema não cria mais administrador com senha padrão. `APP_SECRET` deve ter pelo menos 32 caracteres, e `DESPACHO_ADMIN_SENHA_INICIAL` precisa ser definida antes da primeira criação do banco.

## PythonAnywhere

Veja as instruções em:

```text
corridas/despacho/DEPLOY.md
```

## Roadmap para produção e dados de pacientes

As decisões obrigatórias antes de usar dados reais estão registradas em:

```text
docs/ROADMAP_SEGURANCA_E_PRODUCAO.md
```

O cadastro de pacientes e o estoque permanecem visuais no MVP. Não utilize dados reais de pacientes nesta fase.

## Google Cloud

O projeto possui uma base de homologação para Cloud Run com Docker, Gunicorn, health checks, Secret Manager e Cloud Build. Consulte:

```text
docs/GCP_DEPLOY.md
```

O deploy atual no Cloud Run é somente para homologação com dados fictícios. O SQLite é efêmero em contêineres, por isso `APP_ENV=production` permanece bloqueado até a migração para Cloud SQL PostgreSQL.

Para validar o contêiner localmente:

```bash
docker build -t sistema-despacho .
docker run --rm -p 8080:8080 \
  -e APP_SECRET="uma-chave-segura-com-mais-de-32-caracteres" \
  -e DESPACHO_ADMIN_SENHA_INICIAL="uma-senha-inicial-forte" \
  -e DESPACHO_COOKIE_SECURE=0 \
  sistema-despacho
```

Depois acesse `http://localhost:8080/healthz` e `http://localhost:8080/despacho/login`.

## Testes

```bash
python -m unittest discover -s corridas/tests -v
```
