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

## Estrutura

```text
corridas/
  app.py
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
```

As pastas `database`, `instance`, `logs`, `scripts` e `static` ficam preparadas para próximas fases. Arquivos locais de banco, logs, ZIPs e segredos ficam fora do Git pelo `.gitignore`.

## Rodando localmente

```bash
pip install -r requirements.txt
python corridas/app.py
```

Acesse:

```text
http://localhost:5000/despacho/login
```

Usuário inicial:

- usuário: `admin`
- senha: `mudar123`

Para produção, defina variáveis de ambiente como `APP_SECRET` e, se quiser trocar a senha inicial antes da criação do banco, `DESPACHO_ADMIN_SENHA_INICIAL`.

## PythonAnywhere

Veja as instruções em:

```text
corridas/despacho/DEPLOY.md
```

## Testes

```bash
python -m unittest discover -s corridas/tests -v
```
