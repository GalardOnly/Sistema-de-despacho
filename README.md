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

## Testes

```bash
python -m unittest discover -s corridas/tests -v
```
