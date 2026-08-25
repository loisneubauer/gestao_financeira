# 📊 Documentação: Sistema Financeiro (Gestão Financeira — Empresa & Casa)

> Atualizado em 2026-08-24 a partir do código real (`app.py`, `database.py`, `calculos.py`, `emailer.py`, `templates/`).
> A versão anterior deste documento era de 2026-08-10 e descrevia o sistema **antes** do multi-tenancy — afirmava que "não há isolamento multi-tenant hoje" e que o webhook não tinha autenticação. Ambos já foram implementados. Este documento substitui aquele conteúdo.

Sistema web (Flask + SQLite) de controle de contas a pagar e a receber, organizado em **duas dimensões independentes**:

- **Organização (tenant)** — isolamento real de dados. Cada organização tem seus próprios usuários, categorias e lançamentos, e nunca enxerga os das outras.
- **Esfera** — "Empresa" (clínica) ou "Casa" (pessoal). É apenas uma lente de visualização *dentro* de uma organização, não uma fronteira de segurança.

---

## 1. Arquitetura

- **Backend**: Flask (`app.py`), rodando localmente na porta 5002 (`debug=True`).
- **Banco de dados**: SQLite (`financeiro.db`), acessado via `sqlite3` em `database.py` (sem ORM), com `PRAGMA foreign_keys = ON`.
- **Regras de negócio**: isoladas em `calculos.py` (resumo financeiro, despesas por categoria, geração de recorrências). Todas as funções recebem `tenant_id` como primeiro parâmetro.
- **Email transacional**: `emailer.py` — convite de novo usuário via Gmail SMTP (porta 587).
- **Templates**: Jinja2 + Bootstrap 5 (`templates/`), com filtros customizados `moeda_br` e `data_br`.
- **Chave secreta**: gerada uma vez e persistida em `.secret_key` na raiz do projeto.
- **Dependências** (`requirements.txt`): Flask 3.0.3, Werkzeug 3.0.3, itsdangerous 2.2.0.

### 1.1 Multi-tenancy

O isolamento é feito por `tenant_id` em **todas** as tabelas de dados, aplicado na camada de acesso (`database.py`): toda query de leitura, atualização e exclusão carrega `WHERE ... AND tenant_id = ?`. A rota obtém o tenant da sessão via `tenant_atual()`, nunca da URL ou do formulário.

`criar_tabelas()` faz **migração automática** de instalações anteriores ao multi-tenancy: cria um tenant padrão, reconstrói `usuarios` para trocar `UNIQUE(email)` por `UNIQUE(tenant_id, email)`, e adiciona `tenant_id` em `categorias` e `lancamentos`. Também há migrações legadas incrementais para `frequencia_recorrencia`, `is_admin`, `deve_trocar_senha` e `importancia`.

Uma migração à parte troca o slug automático `padrao` pelo nome da organização em formato de slug ("Acupuntura Bem-estar" → `acupuntura-bem-estar`): o slug deixou de ser detalhe interno e passou a ser digitado no login, e ninguém escolheu "padrao" — ele veio da migração de multi-tenancy. Acentos são removidos, colisões ganham sufixo numérico, e slugs escolhidos por alguém nunca são tocados.

### 1.2 Segurança

| Mecanismo | Estado |
|---|---|
| Hash de senha (`werkzeug.security`) | ✅ ativo |
| CSRF manual (token por sessão, validado em `before_request`) | ✅ ativo — isenta `/api/*`, `login`, `esqueci_senha`, `redefinir_senha` |
| Cookies `HttpOnly` + `SameSite=Lax`, sessão de 8h | ✅ ativo |
| Cabeçalhos `X-Frame-Options: SAMEORIGIN` e `X-Content-Type-Options: nosniff` | ✅ ativo |
| Autenticação do webhook por token de organização | ✅ ativo (ver §5) |
| Limite de tentativas de login | ✅ ativo — 5 falhas bloqueiam por 15 min, por (IP + organização + email). Estado em memória: reiniciar o app zera a contagem |
| Mensagem de erro de login sem distinção | ✅ ativo — organização, email ou senha errados devolvem a mesma mensagem, sem revelar o que existe |

---

## 2. Estrutura de Banco de Dados

### 2.1 `tenants`
Organizações (clínicas) que usam a plataforma.

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `nome` | TEXT | exibido na navbar |
| `slug` | TEXT UNIQUE | identificador curto. É **digitado na tela de login** (campo Organização) e serve de confirmação para excluir a organização. Instalações antigas ficaram com o slug automático `padrao`; a migração o substitui pelo nome da organização em formato de slug (ver §1.1) |
| `ativo` | INTEGER | 0 bloqueia o login de todos os usuários da organização |
| `criado_em` | TEXT | `datetime('now')` |
| `api_token` | TEXT UNIQUE | token do webhook (ver §5); gerado na criação e regenerável pelo admin |

### 2.2 `usuarios`

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `tenant_id` | INTEGER FK → `tenants.id` | |
| `nome` | TEXT | |
| `email` | TEXT | login; único **por organização** (`UNIQUE (tenant_id, email)`) |
| `senha_hash` | TEXT | `generate_password_hash` |
| `foto_perfil` | TEXT | caminho `{tenant_id}/{arquivo}` dentro de `static/uploads/avatars/` |
| `saudacao` | TEXT | ex: "Sr.", "Dra." — exibido no cumprimento da navbar |
| `is_admin` | INTEGER | **admin de plataforma** — ver §4 |
| `deve_trocar_senha` | INTEGER | 1 força a troca no próximo login |

### 2.3 `categorias`

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `tenant_id` | INTEGER FK → `tenants.id` | |
| `nome` | TEXT | ex: Aluguel, Luz, Insumos, Consultas |
| `tipo` | TEXT | `'Pagar'`, `'Receber'` ou `'Ambos'` |
| `esfera` | TEXT | `'Empresa'`, `'Casa'` ou `'Ambos'` |

Categorias marcadas como `'Ambos'` aparecem nas duas listas — o filtro em `listar_categorias` usa `(tipo = ? OR tipo = 'Ambos')`.

### 2.4 `lancamentos`
Tabela central: cada linha é uma despesa (Pagar) ou receita (Receber). **Não há tabela de parcelamento** — cada parcela ou ocorrência recorrente é uma linha própria.

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `tenant_id` | INTEGER FK → `tenants.id` | |
| `descricao` | TEXT | |
| `tipo` | TEXT | `'Pagar'` ou `'Receber'` |
| `esfera` | TEXT | `'Empresa'` ou `'Casa'` |
| `categoria_id` | INTEGER FK → `categorias.id` | |
| `valor` | REAL | |
| `vencimento` | TEXT | `AAAA-MM-DD` |
| `data_pagamento` | TEXT | preenchido só quando pago/recebido |
| `status` | TEXT | `'Pendente'`, `'Pago'`, `'Recebido'` (ver §3.1 sobre status calculado) |
| `forma_pagamento` | TEXT | Pix, Boleto, Cartão, Dinheiro, Transferência |
| `recorrente` | INTEGER | 0/1 — derivado automaticamente de `frequencia_recorrencia` |
| `frequencia_recorrencia` | TEXT | `'Nenhuma'`, `'Mensal'`, `'Quinzenal'`, `'Semanal'` |
| `observacoes` | TEXT | também marca a origem de integração (ver §5) |
| `importancia` | TEXT | `'Imprescindível'`, `'Necessário'`, `'Supérfluo'`, `'Impulso'` ou NULL (ver §3.6) |

---

## 3. Regras de Negócio (`calculos.py`)

### 3.1 Status "calculado" (Contas a Receber)
O `status` salvo no banco é só `Pendente`/`Pago`/`Recebido`. A tela `/receber` calcula um `status_calculado` em tempo real:

- **Recebido**: `status` já é `Pago` ou `Recebido`.
- **Atrasado**: `vencimento` anterior a hoje e ainda não recebido.
- **No Prazo**: vencimento hoje ou futuro e ainda não recebido.

Itens atrasados **não são listados individualmente** — são somados em uma única linha consolidada no topo ("⚠️ Total de Receitas em Atraso"), com `id = "atraso_consolidado"` e a observação informando quantas contas foram agrupadas.

### 3.2 Esfera (Empresa / Casa / Todas)
Filtro guardado na sessão (`session["esfera_filtro"]`), trocado via `/trocar-esfera/<esfera>`. É uma lente de visualização sobre os dados **da organização atual** — não é fronteira de segurança. O isolamento real é o `tenant_id`.

### 3.3 Listagem e atrasados de meses anteriores
`listar_lancamentos(...)` inclui, por padrão (`incluir_atrasados_anteriores=True`), lançamentos de meses anteriores que ainda não foram pagos/recebidos. Assim uma conta atrasada não "some" ao virar o mês.

### 3.4 Recorrências
Duas rotinas complementares:

- **`gerar_recorrencias_do_mes(tenant_id, mes_destino)`** — acionada manualmente pelo botão "Gerar Recorrências". Copia lançamentos `Mensal` do mês anterior para o mês de destino (mantendo o dia do vencimento) e avança `Semanal`/`Quinzenal` em blocos de 7/14 dias até cobrir o mês.
- **`projetar_recorrencias_do_mes(tenant_id, dados)`** — acionada automaticamente ao criar/editar um lançamento `Semanal` ou `Quinzenal`. Gera de uma vez todas as ocorrências restantes dentro do mesmo mês.

Ambas evitam duplicidade comparando a chave `(descrição, tipo, esfera, valor, vencimento)`.

### 3.5 Importância do gasto

Cada conta a pagar pode ser classificada em uma escala ordenada do essencial ao evitável, definida em `calculos.NIVEIS_IMPORTANCIA`:

| Nível | Sentido |
|---|---|
| **Imprescindível** | aluguel, luz, água, comida — cortar quebra alguma coisa |
| **Necessário** | insumos, transporte, plano — preciso, mas há margem de negociação |
| **Supérfluo** | escolhi ter, sabendo que era extra |
| **Impulso** | comprei sem planejar |

`calcular_gastos_por_importancia(tenant_id, esfera, mes_ano)` agrupa as despesas do mês por nível e devolve os totais, as fatias percentuais, e o número que motivou o recurso: **quanto do gasto era evitável** (Supérfluo + Impulso).

Dois cuidados na regra:

- O percentual evitável é calculado sobre o total **já classificado**, não sobre o total do mês. Dizer "12% foi evitável" quando metade das despesas não tem classificação seria enganoso — por isso o dashboard também mostra quanto ainda falta classificar.
- Lançamentos sem classificação (todos os anteriores à migração) aparecem como "Não classificado" e nunca recebem um nível por chute.

A classificação é **só para despesas** — não existe em Contas a Receber. Recorrências herdam o nível do lançamento de origem.

### 3.6 Resumo Financeiro (Dashboard `/`)
`calcular_resumo_financeiro(tenant_id, esfera, mes_ano)` retorna, para o mês selecionado: total pago/atrasado/a vencer de Pagar e de Receber, **saldo atual** (recebido − pago) e **saldo projetado** (receber total − pagar total). `calcular_despesas_por_categoria` agrupa despesas do mês por categoria para o gráfico. `dias_uteis_restantes_no_mes` conta dias úteis (seg–sex) restantes no mês corrente.

---

## 4. Níveis de acesso

Existem **dois** níveis, e a distinção é importante:

| Nível | Como se identifica | O que pode |
|---|---|---|
| **Usuário comum** | `is_admin = 0` | Tudo dentro da própria organização: lançamentos, categorias, perfil, senha |
| **Admin de plataforma** | `is_admin = 1` | Tudo do usuário comum **+ a área `/admin/tenants`**, que enxerga e gerencia TODAS as organizações |

`is_admin` é permissão de **dona da plataforma**, não de dona da clínica. Por isso o código força `is_admin=0` ao criar organizações e ao adicionar usuários pela tela de admin — a promoção é sempre um ato explícito e separado (`/admin/tenants/.../alternar-admin`).

### 4.1 Salvaguardas implementadas

- Não é possível remover o próprio acesso de admin.
- Não é possível remover ou excluir o **último** admin da plataforma (`contar_admins() <= 1`).
- Não é possível excluir a própria conta enquanto logada nela.
- Não é possível excluir a organização em que se está logada.
- Excluir uma organização exige **digitar o slug exato** como confirmação, e apaga em cascata usuários, categorias e lançamentos (irreversível).

### 4.2 Convite de novo usuário

`_criar_usuario_com_convite()` gera uma senha temporária (`secrets.token_urlsafe(9)`), cria o usuário com `deve_trocar_senha=1` e envia o convite por email. **Se o envio falhar, a senha temporária é exibida na tela** para repasse manual — o usuário nunca fica criado e inacessível.

No próximo login, `login_required` intercepta qualquer rota e redireciona para `/trocar-senha-obrigatoria` até a troca ser feita (só `logout` escapa).

Configuração do email em `.env` (ver `.env.example`): `GMAIL_USER` e `GMAIL_APP_PASSWORD` (senha de app do Gmail, exige verificação em duas etapas).

---

## 5. Integração externa (Webhook)

`POST /api/v1/receber/webhook` — cria ou atualiza um lançamento de `Receber` na esfera `Empresa`.

**Autenticação**: header `X-Api-Token` com o `api_token` da organização. Token inválido, ausente ou de organização inativa → `401`. O token define em qual tenant o lançamento é gravado — não há tenant no corpo da requisição.

**Idempotência**: o campo `referencia_id` é gravado em `observacoes` como a tag `ID Ref: {referencia_id}`. Em chamadas seguintes, `buscar_lancamento_por_referencia` localiza o lançamento existente e o **atualiza** em vez de duplicar (`201` na criação, `200` na atualização).

**Somente leitura na UI**: lançamentos cujo `observacoes` contém `"ID Ref:"` têm edição, exclusão e troca de status bloqueados na interface — a fonte da verdade é o sistema de origem.

### 5.1 Limpeza — `POST /api/v1/receber/limpar-clinica`

Autenticado pelo mesmo `X-Api-Token`. Apaga todos os lançamentos da organização marcados com `ID Ref: clinic_`, devolvendo quantos removeu. Lançamentos criados à mão não têm essa marca e são preservados.

Existe porque a clínica é a fonte da verdade dessas receitas e **reescreve o conjunto inteiro** a cada sincronização, em vez de atualizar linha a linha. Sem isso, uma linha que deixou de existir lá — um mês que saiu do atraso, por exemplo — ficaria aqui para sempre somando um valor que não é mais verdade.

### 5.2 O que a clínica envia

Desde 25/08/2026 são três tipos de linha, calculados pelo Painel Financeiro da própria clínica:

| Linha | Conteúdo |
|---|---|
| `clinic_resumo_pago_AAAA-MM` | Total recebido em cada mês do histórico |
| `clinic_resumo_pendente_AAAA-MM` | A receber **do mês atual** |
| `clinic_resumo_atrasado_total` | Atraso **acumulado**, uma linha só |

O atraso não é quebrado por mês de propósito: a clínica o trata como um montante que se arrasta, não como algo que pertence a um mês. O formato anterior (uma linha de atraso por mês) era invenção da integração e produzia totais que não batiam com a tela da clínica.

---

## 6. Módulos / Rotas do Sistema

### Autenticação e perfil
| Rota | Descrição |
|---|---|
| `/login`, `/logout` | Login por email/senha, sessão de 8h. Bloqueia acesso se a organização estiver inativa |
| `/esqueci-senha`, `/redefinir-senha/<token>` | Recuperação via link assinado (`itsdangerous`, 30 min) |
| `/trocar-senha-obrigatoria` | Troca forçada no primeiro login (usuário convidado) |
| `/alterar-senha` | Troca de senha autenticada (mínimo 8 caracteres) |
| `/meu-perfil` | Editar nome, saudação e foto (PNG/JPG/WEBP, salva em pasta própria do tenant) |

### Operação financeira
| Rota | Descrição |
|---|---|
| `/` | Dashboard: resumo do mês, gráfico de despesas por categoria, dias úteis restantes |
| `/trocar-esfera/<esfera>` | Alterna o filtro Empresa/Casa/Todas |
| `/pagar` + `/novo`, `/<id>/editar`, `/<id>/toggle-status`, `/<id>/excluir` | CRUD de Contas a Pagar |
| `/receber` + `/novo`, `/<id>/editar`, `/<id>/toggle-status`, `/<id>/excluir` | CRUD de Contas a Receber (bloqueado para itens vindos do webhook) |
| `/categorias` + `/nova`, `/<id>/editar`, `/<id>/excluir` | CRUD de categorias |
| `/gerar-recorrencias` | Dispara a geração de recorrentes para um mês |

### Administração de plataforma (`@admin_required`)
| Rota | Descrição |
|---|---|
| `/admin/tenants` | Lista todas as organizações e seus usuários |
| `/admin/tenants/novo` | Cria organização + primeiro usuário (com convite por email) |
| `/admin/tenants/<id>/editar` | Nome, slug e ativo/inativo |
| `/admin/tenants/<id>/excluir` | Exclusão em cascata, com confirmação por slug |
| `/admin/tenants/<id>/novo-usuario` | Adiciona usuário a uma organização existente |
| `/admin/tenants/<id>/usuarios/<id>/editar`, `/excluir`, `/alternar-admin` | Gestão de usuários |
| `/admin/tenants/<id>/gerar-token` | Regenera o `api_token` do webhook |

### API
| Rota | Descrição |
|---|---|
| `POST /api/v1/receber/webhook` | Integração externa autenticada por `X-Api-Token` (ver §5) |

---

## 7. Pontos de atenção para a evolução

1. **`debug=True` só afeta o ambiente local.** `app.run(port=5002, debug=True)` está dentro de `if __name__ == "__main__"`, e o PythonAnywhere roda via WSGI importando o objeto `app` — a linha não executa em produção. Só vale atenção se um dia o app for iniciado direto por `python app.py` num servidor exposto.

2. **`excluir_lancamentos_detalhados_clinica(tenant_id)`** existe em `database.py` mas nenhuma rota a expõe — é uma função de limpeza em massa dos lançamentos vindos do webhook (`ID Ref: clinic_pg_%`), hoje só chamável manualmente.

3. **Recuperação de senha e convite dependem do Gmail SMTP.** Sem `.env` configurado, o convite cai no fallback de exibir a senha na tela; vale confirmar o comportamento de `/esqueci-senha` no mesmo cenário.

---

## 8. Documentos relacionados (`Agentes/`)

- `Plano_Multi_Tenancy.md` — o plano que originou a arquitetura descrita em §1.1 (já executado).
- `Roteiro_Testes_Multi_Tenancy.md` — roteiro de verificação do isolamento entre organizações.
- `Roteiro_Deploy_PythonAnywhere.md` — passos de publicação.
