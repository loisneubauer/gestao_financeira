# 📊 Documentação: Sistema Financeiro (Gestão Financeira — Empresa & Casa)

> Atualizado em 2026-08-10 a partir do código real (`app.py`, `database.py`, `calculos.py`, `templates/`). A versão anterior deste documento descrevia um sistema de clínica de acupuntura (tabelas `pagamentos`, `atendimentos`, `fechamentos_mensais`, integração Climed) que **não existe no código atual** — provavelmente era de outro projeto ou uma versão futura ainda não implementada. Este documento substitui aquele conteúdo.

Este é um sistema web (Flask + SQLite) de controle de contas a pagar e a receber, compartilhado por duas usuárias (Lois e Laila) que dividem o mesmo login/base de dados, separando os lançamentos por **esfera**: "Empresa" (clínica) ou "Casa" (pessoal).

---

## 1. Arquitetura

- **Backend**: Flask (`app.py`), rodando localmente na porta 5002 (`debug=True`).
- **Banco de dados**: SQLite (`financeiro.db`), acessado via `sqlite3` em `database.py` (sem ORM).
- **Regras de negócio**: isoladas em `calculos.py` (cálculo de resumo financeiro, despesas por categoria, geração de recorrências).
- **Templates**: Jinja2 + Bootstrap 5 (`templates/`), com filtros customizados `moeda_br` e `data_br`.
- **Autenticação**: sessão de servidor (Flask `session`), sem separação por tenant/organização — todos os usuários cadastrados em `usuarios` enxergam o mesmo banco de dados, filtrado apenas pela "esfera" escolhida.
- **Segurança**: proteção CSRF manual (token gerado por sessão, injetado via JS em todo `<form method="post">`), cabeçalhos `X-Frame-Options` e `X-Content-Type-Options`, senha com hash (`werkzeug.security`), limite de tentativas de login declarado mas não totalmente aplicado (`_tentativas_login`/`LIMITE_TENTATIVAS_LOGIN` existem como variáveis mas o bloqueio não está implementado nas rotas).
- **Chave secreta**: gerada uma vez e persistida em `.secret_key` na raiz do projeto.

---

## 2. Estrutura de Banco de Dados

### 2.1 `usuarios`
Login e perfil das usuárias do sistema.

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `nome` | TEXT | |
| `email` | TEXT UNIQUE | usado como login |
| `senha_hash` | TEXT | gerado com `werkzeug.security.generate_password_hash` |
| `foto_perfil` | TEXT | nome do arquivo em `static/uploads/avatars/` |
| `saudacao` | TEXT | ex: "Sr.", "Dra." — exibido no cumprimento da navbar |

Hoje só existem duas usuárias cadastradas via `criar_usuarios.py`: Lois e Laila (Dra., acupuntura). Recuperação de senha via link temporário assinado (`itsdangerous`, expira em 30 min).

### 2.2 `categorias`
Categorias de despesas/receitas, reutilizáveis entre lançamentos.

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `nome` | TEXT | ex: Aluguel, Luz, Insumos, Consultas |
| `tipo` | TEXT | `'Pagar'` ou `'Receber'` |
| `esfera` | TEXT | `'Empresa'`, `'Casa'` ou `'Ambos'` |

### 2.3 `lancamentos`
Tabela central: cada linha é uma despesa (Pagar) ou receita (Receber), sem parcelamento — cada parcela/recorrência é uma linha própria.

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `descricao` | TEXT | |
| `tipo` | TEXT | `'Pagar'` ou `'Receber'` |
| `esfera` | TEXT | `'Empresa'` ou `'Casa'` |
| `categoria_id` | INTEGER FK | → `categorias.id` |
| `valor` | REAL | |
| `vencimento` | TEXT | formato `AAAA-MM-DD` |
| `data_pagamento` | TEXT | preenchido só quando pago/recebido |
| `status` | TEXT | `'Pendente'`, `'Pago'`, `'Recebido'`, `'Atrasado'` (ver §3.1 sobre status calculado) |
| `forma_pagamento` | TEXT | Pix, Boleto, Cartão, Dinheiro, Transferência |
| `recorrente` | INTEGER | 0/1 — mantido por compatibilidade, ver `frequencia_recorrencia` |
| `frequencia_recorrencia` | TEXT | `'Nenhuma'`, `'Mensal'`, `'Quinzenal'`, `'Semanal'` (coluna adicionada via migração automática em `criar_tabelas()`) |
| `observacoes` | TEXT | também usado para marcar origem de integração (ver §4) |

Não há tabela de parcelamento — cada parcela ou ocorrência recorrente é gerada como um novo registro em `lancamentos` (ver §3.3).

---

## 3. Regras de Negócio (`calculos.py`)

### 3.1 Status "calculado" (Contas a Receber)
O campo `status` salvo no banco é só `Pendente`/`Pago`/`Recebido`. A tela `/receber` calcula um `status_calculado` em tempo real:
- **Recebido**: `status` já é `Pago` ou `Recebido`.
- **Atrasado**: `vencimento` é anterior a hoje e ainda não recebido.
- **No Prazo**: vencimento futuro (ou hoje) e ainda não recebido.

Itens atrasados são agrupados em uma única linha consolidada no topo da lista ("⚠️ Total de Receitas em Atraso"), somando todos os atrasados em vez de listar um por um.

### 3.2 Esfera (Empresa / Casa / Todas)
Filtro salvo na sessão (`session["esfera_filtro"]`), trocado via `/trocar-esfera/<esfera>`. Não é um filtro de segurança/tenant — é apenas uma lente de visualização sobre o mesmo conjunto de dados, disponível para qualquer usuário logado.

### 3.3 Recorrências
Duas rotinas complementares em `calculos.py`:
- **`gerar_recorrencias_do_mes(mes_destino)`**: acionada manualmente (botão "Gerar Recorrências") — copia lançamentos `Mensal` do mês anterior para o mês de destino (mantendo o dia do vencimento), e lançamentos `Semanal`/`Quinzenal` avançando a data em blocos de 7/14 dias até cobrir o mês de destino. Evita duplicidade comparando `(descrição, tipo, esfera, valor, vencimento)`.
- **`projetar_recorrencias_do_mes(dados)`**: acionada automaticamente ao criar/editar um lançamento `Semanal` ou `Quinzenal` — gera de uma vez todas as ocorrências restantes dentro do mesmo mês.

### 3.4 Resumo Financeiro (Dashboard `/`)
`calcular_resumo_financeiro(esfera, mes_ano)` retorna, para o mês selecionado: total pago/atrasado/a vencer de Pagar e de Receber, saldo atual (recebido − pago) e saldo projetado (receber total − pagar total). `calcular_despesas_por_categoria` agrupa despesas do mês por categoria para o gráfico. `dias_uteis_restantes_no_mes` conta dias úteis (seg–sex) restantes no mês corrente.

---

## 4. Integração externa (Webhook)

`POST /api/v1/receber/webhook` — endpoint público, **sem autenticação**, que cria ou atualiza um lançamento de `Receber` na esfera `Empresa`. Usa `observacoes` para gravar uma tag `ID Ref: {referencia_id}` e localizar o lançamento em atualizações futuras (`buscar_lancamento_por_referencia`). Lançamentos criados por essa via ficam **somente leitura** na interface (edição/exclusão/toggle de status bloqueados quando `observacoes` contém `"ID Ref:"`).

Isso indica que já existe (ou existiu) a intenção de integrar com outro sistema (possivelmente o que gerou a documentação antiga de clínica) — mas hoje o endpoint não tem nenhum controle de origem/autenticação.

---

## 5. Módulos / Rotas do Sistema

| Rota | Descrição |
|---|---|
| `/login`, `/logout` | Autenticação por email/senha, sessão de 8h |
| `/esqueci-senha`, `/redefinir-senha/<token>` | Recuperação de senha via link assinado (30 min de validade) |
| `/meu-perfil` | Editar nome, saudação e foto de perfil |
| `/alterar-senha` | Troca de senha autenticada |
| `/trocar-esfera/<esfera>` | Alterna o filtro Empresa/Casa/Todas |
| `/` | Dashboard: resumo financeiro do mês, gráfico de despesas por categoria, dias úteis restantes |
| `/pagar`, `/pagar/novo`, `/pagar/<id>/editar`, `/pagar/<id>/toggle-status`, `/pagar/<id>/excluir` | CRUD de Contas a Pagar |
| `/receber`, `/receber/novo`, `/receber/<id>/editar`, `/receber/<id>/toggle-status`, `/receber/<id>/excluir` | CRUD de Contas a Receber (com bloqueio de edição para itens sincronizados via webhook) |
| `/categorias`, `/categorias/nova`, `/categorias/<id>/editar`, `/categorias/<id>/excluir` | CRUD de categorias |
| `/gerar-recorrencias` | Dispara geração de lançamentos recorrentes para um mês |
| `/api/v1/receber/webhook` | Integração externa (ver §4) |

---

## 6. Observações relevantes para evolução do sistema

- **Não há isolamento multi-tenant hoje** — é a base do plano descrito em [`Plano_Multi_Tenancy.md`](./Plano_Multi_Tenancy.md), que propõe adicionar `tenant_id` em `usuarios`, `categorias` e `lancamentos`.
- O webhook sem autenticação e as funções de exclusão em massa (`excluir_lancamentos_detalhados_clinica`) são pontos de atenção que o plano de multi-tenancy também endereça (token por tenant, filtro de tenant em toda query).
- A documentação antiga sobre convênio/Climed/parcelamento em 3x não corresponde ao código atual; se esse escopo ainda for desejado, precisa ser tratado como funcionalidade nova a ser planejada, não como algo já existente.
