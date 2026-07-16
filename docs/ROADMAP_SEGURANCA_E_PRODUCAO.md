# Roadmap de segurança, dados de pacientes e produção

Este documento concentra as decisões e perguntas levantadas durante o MVP. Ele deve ser consultado antes de transformar qualquer módulo visual em funcionalidade com dados reais.

## Decisão de infraestrutura

- Provedor escolhido para a próxima fase: Google Cloud.
- Região planejada: São Paulo, `southamerica-east1`.
- Cloud Run será usado para a aplicação Flask.
- Cloud SQL PostgreSQL será usado para persistência de produção.
- Memorystore Redis será usado para rate limit e eventos distribuídos.
- O deploy inicial no Google Cloud será apenas de homologação com dados fictícios.

## Situação atual do MVP

- O PythonAnywhere é uma hospedagem temporária para demonstração e validação.
- O SQLite atende ao protótipo de baixa concorrência, sem dados reais de pacientes.
- Cadastro de pacientes e estoque são demonstrações visuais e não possuem persistência no servidor.
- O chat e as notificações usam consultas periódicas. Não há WebSocket ou fila de mensagens.
- Coletas usam chave numérica interna e protocolo `COL-XXXXX` para exibição.
- O isolamento de coletas, chat, notificações e rastreamento é feito por papel, unidade e entregador.

### Controles já implementados no MVP

- `APP_SECRET` obrigatório e rejeição de valores curtos ou conhecidos como exemplo.
- Senhas armazenadas com hash do Werkzeug e troca administrativa auditável pelo contexto autenticado.
- Mensagem genérica para usuário ou senha incorretos.
- Rate limit interno de falhas no login por IP e por combinação de IP e usuário, adequado ao processo único do MVP.
- Renovação da sessão após autenticação e proteção CSRF nas APIs mutáveis.
- Cookies de sessão `Secure`, `HttpOnly` e `SameSite=Lax` na hospedagem HTTPS.
- Headers contra clickjacking, detecção incorreta de conteúdo e vazamento de referência.
- CSP compatível com o front atual, ainda permitindo scripts e estilos embutidos temporariamente.
- Request ID por requisição, logs técnicos em JSON e resposta 500 sem detalhes internos.
- Respostas do sistema marcadas como privadas e sem cache nas rotas `/despacho`.

Dados reais de pacientes não devem ser inseridos no MVP enquanto os requisitos da próxima seção não forem atendidos.

## Obrigatório antes de persistir pacientes

### Modelo e minimização de dados

- Definir com a área assistencial quais campos são realmente necessários.
- Confirmar a necessidade de nome completo, CPF ou carteirinha, data de nascimento, telefone e endereço.
- Não armazenar campos apenas porque podem ser úteis no futuro.
- Definir regras de duplicidade usando CPF, carteirinha ou outra chave institucional.
- Manter chave inteira interna e criar `public_id` aleatório, preferencialmente UUID, para URLs e APIs.
- Não usar protocolo, UUID ou código público como mecanismo de autorização.

### Autorização e isolamento entre unidades

- Toda consulta de paciente deve incluir a unidade autorizada da sessão.
- Um solicitante não pode informar livremente o `unidade_id` que deseja consultar.
- O administrador deve ter acesso explícito e auditável às unidades sob sua responsabilidade.
- Entregadores não devem receber CPF, endereço completo, telefone ou observações clínicas quando esses dados não forem necessários para a entrega.
- Criar testes cruzados: usuário da unidade A tenta ler, editar, excluir e exportar paciente da unidade B.
- Centralizar a autorização por objeto para evitar verificações diferentes em cada rota.

### APIs e respostas

- Criar serializadores com listas permitidas de campos para cada perfil.
- Nunca retornar diretamente uma linha completa, modelo inteiro ou `__dict__`.
- Separar respostas de busca rápida, detalhes, edição e relatórios.
- Paginar buscas e limitar quantidade de resultados.
- Não expor IDs internos sequenciais no navegador quando houver `public_id`.
- Vincular uma coleta ao paciente por identificador público, validando a unidade no servidor.

### Auditoria e LGPD

- Registrar criação, consulta, alteração, exportação e exclusão lógica de pacientes.
- Registrar usuário, unidade, data, ação e identificador do registro, sem copiar dados sensíveis para o log técnico.
- Definir controlador, operador, base legal, finalidade e responsáveis pelos dados.
- Definir prazo de retenção e procedimento de anonimização ou descarte.
- Definir atendimento a solicitações do titular e correção de cadastro.
- Avaliar a necessidade de RIPD com o encarregado de dados e jurídico da instituição.
- Proibir dados clínicos, CPF, endereço, senha, token ou texto de chat nos logs.

### Segurança funcional

- Criar RBAC detalhado para administrador, gestor, solicitante, estoque, atendimento e entregador.
- Exigir senhas mais fortes e considerar MFA para administradores.
- Criar recuperação de senha com token de uso único, validade curta e trilha de auditoria.
- Revisar sessões, expiração, encerramento remoto e troca obrigatória de senha inicial.
- Fazer revisão de IDOR, CSRF, XSS, injeção SQL e upload de arquivos antes da liberação.

## Melhorias de segurança ainda previstas

- Migrar scripts e estilos embutidos para arquivos estáticos.
- Substituir `unsafe-inline` da CSP por nonces ou hashes.
- Hospedar dependências críticas localmente ou usar integridade SRI e versões fixas.
- Adicionar análise automatizada de dependências e vulnerabilidades no CI.
- Versionar a API e padronizar erros sem detalhes internos.
- Aplicar rate limit também em recuperação de senha, buscas sensíveis, exportações e endpoints de alto custo.
- Usar Redis como armazenamento compartilhado do rate limit em produção.
- Avaliar bloqueio progressivo, alertas de tentativa suspeita e listas de IP confiáveis para administração.
- Realizar teste de intrusão antes da entrada em produção.

## Observabilidade e resposta a falhas

- Centralizar logs em uma plataforma apropriada para a nuvem escolhida.
- Correlacionar logs com request ID sem registrar corpo, formulário ou query string sensível.
- Integrar monitoramento de exceções, como Sentry ou serviço equivalente.
- Criar métricas de latência, erros, disponibilidade, fila, SLA e falhas de banco.
- Criar alertas para indisponibilidade, aumento de erros 500 e lentidão.
- Disponibilizar endpoint de saúde para aplicação, banco, Redis e serviços externos.
- Definir responsáveis, contatos e procedimento de resposta a incidentes.

## Infraestrutura para produção

- Migrar de SQLite para PostgreSQL antes do uso concorrente com dados reais.
- Adotar SQLAlchemy ou camada equivalente e migrations versionadas com Alembic.
- Executar Flask em servidor WSGI de produção, como Gunicorn, atrás de proxy ou balanceador.
- Usar uma plataforma que não suspenda a aplicação por inatividade.
- Separar aplicação, PostgreSQL, Redis e armazenamento de arquivos.
- Gerenciar segredos em cofre da plataforma, nunca no Git ou no código WSGI versionado.
- Configurar TLS, domínio institucional, backups criptografados e restauração testada.
- Definir ambientes separados de desenvolvimento, homologação e produção.
- Criar pipeline de deploy com testes, migrations, rollback e aprovação.
- Planejar redundância, escalabilidade horizontal e recuperação de desastre.

## Evolução de pacientes e experiência do usuário

- Adicionar busca rápida de paciente diretamente no formulário de coleta.
- Permitir cadastro rápido sem abandonar o fluxo da coleta, respeitando autorização.
- Avaliar preenchimento de endereço por CEP por serviço confiável, com tratamento para indisponibilidade.
- Validar máscaras sem confundir formatação com validação real de CPF, telefone e carteirinha.
- Confirmar se data de nascimento é requisito clínico, operacional ou apenas cadastral.
- Criar histórico do paciente somente depois de definir finalidade e perfis autorizados.
- Evitar exibir CPF completo em tabelas; aplicar mascaramento quando possível.

## Evolução do estoque

- Definir se o estoque é por unidade, depósito ou setor.
- Definir unidades de medida, lote, validade, estoque mínimo e responsáveis.
- Registrar entradas, saídas, ajustes e inventários com trilha de auditoria.
- Criar alertas de baixo estoque e vencimento.
- Impedir que uma unidade consulte ou altere estoque de outra sem autorização.
- Avaliar vínculo entre consumo de materiais e coleta somente após validar o processo real.

## Comunicação, notificações e GPS

- Avaliar WebSocket ou Server-Sent Events para reduzir o atraso do polling atual.
- Usar Redis ou broker para distribuir eventos quando houver mais de uma instância Flask.
- Definir confirmação de entrega, repetição e expiração de notificações.
- Criar política de retenção do chat e restringir o envio de dados clínicos.
- Definir tempo de retenção das coordenadas GPS e acesso autorizado ao histórico.
- Encerrar rastreamento automaticamente após entrega ou cancelamento.
- Avaliar implicações trabalhistas e de privacidade do rastreamento de entregadores.

## Testes obrigatórios antes da produção

- Concorrência de solicitações, despachos e atualizações de estoque.
- Isolamento entre unidades e perfis em todas as operações CRUD.
- Rate limit atrás do proxy real e com usuários compartilhando a mesma rede.
- Carga, latência e estabilidade durante o volume esperado.
- Falha de banco, Redis, mapas, CEP e serviços de notificação.
- Backup e restauração em ambiente separado.
- Migração de banco com rollback.
- Navegadores e dispositivos utilizados pelas unidades e entregadores.
- Varredura de dependências, análise estática e teste de intrusão.

## Perguntas pendentes para a instituição

- Quais perfis podem cadastrar, visualizar, alterar e excluir pacientes?
- Uma unidade pode visualizar pacientes cadastrados por outra unidade?
- CPF, carteirinha e data de nascimento são obrigatórios em quais situações?
- Qual é a finalidade formal do endereço e por quanto tempo ele será mantido?
- Quem pode consultar histórico de coletas e por quanto tempo?
- Quem opera o estoque e ele é separado por unidade ou centralizado?
- Quais sistemas institucionais precisarão de integração?
- Qual volume de usuários simultâneos, pacientes, coletas e mensagens é esperado?
- Qual disponibilidade, tempo máximo de recuperação e perda máxima de dados são aceitáveis?
- Quem será responsável por suporte, segurança, privacidade e resposta a incidentes?

## Ordem recomendada de execução

1. Concluir segurança e observabilidade básica do MVP.
2. Validar campos, perfis e regras do cadastro de pacientes com a instituição.
3. Modelar PostgreSQL, migrations, RBAC e auditoria.
4. Implementar pacientes com dados fictícios em homologação.
5. Executar testes de isolamento, segurança, carga e restauração.
6. Migrar infraestrutura para nuvem de produção.
7. Autorizar dados reais somente após validação técnica, jurídica e operacional.
