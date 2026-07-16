# Preparação para Google Cloud

## Escopo desta fase

O projeto está preparado para ser empacotado em contêiner e executado no Cloud Run com Gunicorn, health checks, logs em stdout, segredos por variáveis de ambiente e suporte aos headers encaminhados pelo proxy do Google.

O deploy definido em `cloudbuild.yaml` é exclusivamente de homologação. Ele usa SQLite em `/tmp`, portanto os dados podem desaparecer quando o contêiner reiniciar. Use somente dados fictícios.

A aplicação bloqueia `APP_ENV=production` enquanto o backend PostgreSQL não estiver implementado. Esse bloqueio é intencional.

## Arquitetura de homologação

```text
Internet
  -> Cloud Run: sistema-despacho-hml
      -> Gunicorn, um processo e oito threads
      -> SQLite efêmero em /tmp
      -> Cloud Logging por stdout/stderr
      -> Secret Manager para os segredos
```

O serviço fica limitado a uma instância porque bancos SQLite separados em múltiplos contêineres produziriam dados divergentes.

## Arquitetura de produção planejada

```text
Cloud Load Balancing e Cloud Armor
  -> Cloud Run com no mínimo duas instâncias
      -> Cloud SQL PostgreSQL com alta disponibilidade
      -> Memorystore Redis
      -> Cloud Storage
      -> Secret Manager
      -> Cloud Monitoring e alertas
```

## Arquivos adicionados

- `Dockerfile`: imagem Linux para Cloud Run.
- `gunicorn.conf.py`: servidor WSGI de produção.
- `.dockerignore`: reduz e protege o contexto do contêiner.
- `.gcloudignore`: impede envio de banco, segredos, ZIPs e arquivos locais.
- `cloudbuild.yaml`: build, push e deploy da homologação.
- `/healthz`: confirma que o processo Flask está respondendo.
- `/readyz`: confirma que o banco atual está acessível.

## Pré-requisitos no Google Cloud

1. Criar um projeto com faturamento ativo.
2. Selecionar a região `southamerica-east1`.
3. Instalar e autenticar o Google Cloud CLI.
4. Habilitar as APIs necessárias:

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com
```

5. Criar o repositório de contêineres:

```bash
gcloud artifacts repositories create sistema-despacho \
  --repository-format=docker \
  --location=southamerica-east1
```

6. Criar uma conta de serviço exclusiva para o Cloud Run:

```bash
gcloud iam service-accounts create sistema-despacho-run \
  --display-name="Sistema de Despacho Cloud Run"
```

Conceda a essa conta apenas acesso de leitura aos segredos necessários. A conta usada pelo Cloud Build também precisará de permissão para publicar no Artifact Registry, implantar no Cloud Run e utilizar a conta de execução.

## Segredos

Crie os contêineres de segredo:

```bash
gcloud secrets create despacho-app-secret --replication-policy=automatic
gcloud secrets create despacho-admin-senha-inicial --replication-policy=automatic
```

Gere e envie o `APP_SECRET` sem salvá-lo no repositório:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))" \
  | gcloud secrets versions add despacho-app-secret --data-file=-
```

Para a senha inicial, use um valor forte e adicione uma versão pelo Console do Secret Manager ou pela entrada padrão do terminal. Não coloque a senha no `cloudbuild.yaml`.

## Build e deploy da homologação

O arquivo considera a conta `sistema-despacho-run` criada nos passos anteriores. Se utilizar outro nome, ajuste o argumento `--service-account` no `cloudbuild.yaml`.

Execute a validação local antes do envio:

```bash
python scripts/gcp_preflight.py
```

```bash
gcloud builds submit --config cloudbuild.yaml
```

Depois do deploy, valide:

```text
https://URL_DO_SERVICO/healthz
https://URL_DO_SERVICO/readyz
https://URL_DO_SERVICO/despacho/login
```

Os dois health checks devem responder com HTTP 200.

## Variáveis de homologação

O `cloudbuild.yaml` configura:

- `APP_ENV=homologation`
- `ALLOW_EPHEMERAL_SQLITE=1`
- `TRUST_PROXY_HEADERS=1`
- `DESPACHO_DB_PATH=/tmp/despacho.db`
- `GUNICORN_WORKERS=1`

Essas configurações não podem ser reaproveitadas como produção.

## Trabalho obrigatório antes da produção

1. Modelar o banco PostgreSQL.
2. Adicionar SQLAlchemy e migrations Alembic.
3. Migrar os dados válidos do SQLite.
4. Remover `ALLOW_EPHEMERAL_SQLITE`.
5. Substituir o rate limit em memória por Redis.
6. Configurar Cloud SQL com alta disponibilidade e backups.
7. Configurar Memorystore Redis.
8. Configurar duas ou mais instâncias no Cloud Run.
9. Configurar Cloud Armor, domínio, alertas e política de backup.
10. Executar testes de carga, restauração, isolamento entre unidades e segurança.

Somente depois desses itens o ambiente deve utilizar `APP_ENV=production`.
