# 📊 Documentação: Sistema Financeiro (Gestão Financeira — Empresa & Casa)

> Atualizado em 2026-09-04 a partir do código real (`app.py`, `database.py`, `calculos.py`, `emailer.py`, `templates/`).
> Cópia única: a duplicata que existia em `Agentes/` foi apagada em 25/08/2026 — duas cópias do mesmo documento é a receita para uma ficar velha sem ninguém notar.

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
| `integracao_ativa` | INTEGER | 0/1 — a organização recebe lançamentos de um sistema externo? Ver §5.3 |
| `data_inicio` | TEXT | `AAAA-MM-DD` — antes dela nada entra no caixa e nenhum mês é navegável. Ver §3.7 |
| `ultima_integracao` | TEXT | quando a organização recebeu dado externo pela última vez. Gravado a cada chamada do webhook e da limpeza. É o que permite a tela dizer "faz N dias que nada chega" — ver §5.4 |

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
| `importancia` | TEXT | **LEGADO** — guardava o nome do nível como texto. Nada mais lê; mantida como rede de segurança da conversão feita em 25/08/2026 |
| `importancia_nivel` | INTEGER | 1 a 4, ou NULL. É o número do nível, não o nome — ver §3.5 |

### 2.5 `niveis_importancia`
A escala de classificação de gastos, **uma por organização**.

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `tenant_id` | INTEGER FK → `tenants.id` | |
| `nivel` | INTEGER | 1 a 4 — **a chave estável**, é ela que `lancamentos.importancia_nivel` guarda |
| `nome` | TEXT | "Indispensável", "Importante"… — editável |
| `apelido` | TEXT | "Crítico", "Estratégico"… |
| `significado` | TEXT | o que o nível quer dizer na prática |
| `exemplo_empresa` / `exemplo_casa` | TEXT | exemplos que ajudam a classificar |

`UNIQUE (tenant_id, nivel)`. Organização nova nasce com os quatro níveis padrão (`NIVEIS_IMPORTANCIA_PADRAO` em `database.py`), e ajustá-los numa organização não afeta as outras.

**Por que o nível é número e não texto**: se o lançamento guardasse o nome, renomear um nível deixaria órfão todo gasto classificado com o nome antigo. Guardando o número, o nome vira só rótulo e a escala fica editável sem risco.

### 2.6 `saldos_iniciais`
Ponto de partida do caixa de cada esfera.

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `tenant_id` | INTEGER FK → `tenants.id` | |
| `esfera` | TEXT | `'Empresa'` ou `'Casa'` — contas bancárias diferentes |
| `valor` | REAL | quanto havia em caixa na data de referência |
| `data_referencia` | TEXT | `AAAA-MM-DD`; na prática igual ao `tenants.data_inicio` |

`UNIQUE (tenant_id, esfera)`.

### 2.7 `recorrencias_geradas`
Registro de que mês já teve suas recorrências geradas.

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `tenant_id` | INTEGER FK → `tenants.id` | |
| `mes` | TEXT | `AAAA-MM` |
| `gerado_em` | TEXT | `AAAA-MM-DD` da geração |
| `quantidade` | INTEGER | quantos lançamentos aquela geração criou |

`UNIQUE (tenant_id, mes)`.

Existe por um motivo só: **sem ele, a geração automática ressuscitaria na visita seguinte toda conta recorrente que a organização apagasse de propósito.** A linha é gravada *antes* da geração (ver §3.4), o que de quebra impede geração em dobro com duas abas abertas.

A migração marca **todos os meses até o corrente** como já gerados, para cada organização. O automático só age da virada seguinte em diante, nunca para trás.

---

## 3. Regras de Negócio (`calculos.py`)

### 3.1 Status "calculado" (Contas a Receber)
O `status` salvo no banco é só `Pendente`/`Pago`/`Recebido`. A tela `/receber` calcula um `status_calculado` em tempo real:

- **Recebido**: `status` já é `Pago` ou `Recebido`.
- **Atrasado**: `vencimento` anterior a hoje e ainda não recebido.
- **No Prazo**: vencimento hoje ou futuro e ainda não recebido.

Itens atrasados **vindos da integração** não são listados individualmente: são somados numa única linha no topo ("⚠️ Total de Receitas em Atraso"), com `id = "atraso_consolidado"`. Essa linha leva o selo "Somatório" — ela não vem de lugar nenhum, é calculada pela própria tela.

**Recebível atrasado lançado à mão continua aparecendo sozinho**, editável como qualquer outro. Antes tudo era consolidado, e o efeito era perverso: um lançamento manual vencido sumia da lista e não havia como corrigir nem excluir (corrigido em 25/08/2026, achado pela Lois em uso real). A consolidação só faz sentido para as linhas da clínica, que se acumulam mês a mês.

Se a organização não usa integração (§5.3), nada é consolidado e nada é somente leitura.

### 3.2 Esfera (Empresa / Casa / Todas)
Filtro guardado na sessão (`session["esfera_filtro"]`), trocado via `/trocar-esfera/<esfera>`. É uma lente de visualização sobre os dados **da organização atual** — não é fronteira de segurança. O isolamento real é o `tenant_id`.

### 3.3 Listagem e atrasados de meses anteriores
`listar_lancamentos(...)` inclui, por padrão (`incluir_atrasados_anteriores=True`), lançamentos de meses anteriores que ainda não foram pagos/recebidos. Assim uma conta atrasada não "some" ao virar o mês.

**Isso já custou dados.** Em 04/09/2026 a Lois apagou, na tela de setembro, linhas que eram registros de agosto trazidos por essa regra — e elas sumiram de agosto, de vez. A tela não dava nenhum sinal de que aquelas linhas pertenciam a outro mês: só a data na coluna Vencimento, fácil de não notar no meio da lista.

Desde então, em `/pagar` e `/receber`, toda linha de mês anterior vem com:
- fundo destacado e uma tarja **"⚠ de MM/AAAA"** ao lado da data, com explicação no `title`;
- confirmação de exclusão diferente, que nomeia o mês de origem em vez de perguntar só "Excluir esta conta?".

Quem mexer nesses templates precisa preservar as duas coisas. O `set de_outro_mes = l.vencimento[:7] < mes_ano` é o que separa uma linha da outra.

### 3.4 Recorrências
Três rotinas complementares:

- **`gerar_recorrencias_do_mes(tenant_id, mes_destino)`** — copia lançamentos `Mensal` do mês anterior para o mês de destino (mantendo o dia do vencimento) e avança `Semanal`/`Quinzenal` em blocos de 7/14 dias até cobrir o mês. Não é chamada direto por nenhuma tela.
- **`gerar_recorrencias_pendentes(tenant_id, mes_corrente=None)`** — o que a tela usa. Roda de carona na visita ao painel (não há agendador) e põe em dia todos os meses ainda não gerados até o corrente, um a um. Cada mês gerado fica registrado em `recorrencias_geradas` **antes** da geração, e nunca é refeito: é isso que impede uma conta apagada de propósito de voltar sozinha na visita seguinte, e que evita geração em dobro com duas abas abertas. A varredura mês a mês existe porque cada mês copia do seu anterior — pular um deixaria o seguinte sem origem. Não existe botão manual (removido em 04/09/2026).
- **`projetar_recorrencias_do_mes(tenant_id, dados)`** — acionada automaticamente ao criar/editar um lançamento `Semanal` ou `Quinzenal`. Gera de uma vez todas as ocorrências restantes dentro do mesmo mês.

Ambas evitam duplicidade comparando a chave `(descrição, tipo, esfera, valor, vencimento)`.

### 3.5 Importância do gasto

Cada conta a pagar pode ser classificada numa escala ordenada do essencial ao evitável. Os **quatro níveis são fixos**; o que se edita é como eles se chamam e como são explicados (tela em ⚙️ → Tabela de Importância, restrita a admin de plataforma).

Escala padrão, definida com a Lois em 25/08/2026:

| Nível | Nome | Significado |
|:---:|---|---|
| 1 | **Indispensável** *(Crítico)* | Sobrevivência. Sem isso o negócio para ou a vida básica é comprometida |
| 2 | **Importante** *(Estratégico)* | Traz retorno, mas o valor pode ser negociado numa crise |
| 3 | **Desejável** *(Conforto)* | Melhora a experiência, não é vital. Primeira linha de corte |
| 4 | **Evitável** *(Impulso)* | Gasto sem planejamento, sem retorno. Desperdício |

A **linha de corte** fica entre o 2 e o 3. `calcular_gastos_por_importancia` agrupa as despesas do mês por nível e devolve quanto **dava para cortar** — a soma dos níveis 3 e 4 (`NIVEIS_CORTAVEIS`).

O relatório não chama isso de "evitável" de propósito: esse é o nome do nível 4, e dizer "X% foi evitável" somando dois níveis, um deles chamado Evitável, seria ambíguo.

Dois cuidados na regra:

- O percentual é calculado sobre o total **já classificado**, não sobre o mês inteiro. Dizer "12% dava para cortar" com metade das despesas sem classificação seria enganoso — por isso o dashboard também mostra quanto falta classificar.
- Lançamentos sem classificação aparecem como "Não classificado" e nunca recebem um nível por chute.

A classificação é **só para despesas**. Recorrências herdam o nível da origem. No dashboard, clicar numa faixa da barra ou num item da legenda leva para Contas a Pagar filtrado por aquele nível (`/pagar?nivel=N`, ou `nivel=sem` para os não classificados).

### 3.6 Resumo Financeiro (Dashboard `/`)
`calcular_resumo_financeiro(tenant_id, esfera, mes_ano)` retorna, para o mês selecionado: total pago/atrasado/a vencer de Pagar e de Receber, **saldo atual** (recebido − pago) e **saldo projetado** (receber total − pagar total). `calcular_despesas_por_categoria` agrupa despesas do mês por categoria para o gráfico. `dias_uteis_restantes_no_mes` conta dias úteis (seg–sex) restantes no mês corrente.

### 3.7 Saldo de caixa que atravessa o mês

`calcular_saldo_do_mes(tenant_id, esfera, mes_ano)` devolve `saldo_inicial_periodo`, `entrou`, `saiu` e `saldo_final` — o extrato do mês que o dashboard mostra.

Três coisas separam esse cálculo do `calcular_resumo_financeiro`:

1. **Atravessa a virada.** O saldo com que um mês fecha é o ponto de partida do seguinte. Antes, cada mês era uma fotografia isolada e o saldo nunca refletia a posição real.
2. **É regime de caixa.** Conta pela data em que o dinheiro se moveu — `COALESCE(data_pagamento, vencimento)` — e só lançamentos efetivados. As listagens continuam filtrando por vencimento; são lentes diferentes. Antes o saldo misturava as duas (filtrava por vencimento, somava por status), então uma conta que vencia em julho e era paga em agosto contava como saída de julho.
3. **É por esfera.** Empresa e Casa são contas bancárias diferentes; "Todas" soma as duas.

O `COALESCE` existe porque lançamentos vindos do webhook chegam com status `Pago` mas sem `data_pagamento` — sem o fallback eles sumiriam do saldo.

**A porta para regime de competência fica aberta**: a escolha da data vive num lugar só, `database._BASES_DE_DATA`, parametrizada em `somar_movimentacoes(..., base="caixa"|"competencia")`. Hoje só `caixa` é usado; o outro ramo existe como seam, sem tela nem cálculo. Quando a visão for pedida, o trabalho é a tela, não reescrever consulta espalhada.

### 3.8 Data de início do sistema

`tenants.data_inicio` marca quando a organização passou a usar o sistema. Antes dela:

- **nada entra no caixa** — inclusive receitas que a clínica tenha sincronizado de meses anteriores;
- **nenhum mês é navegável** no painel, em contas a pagar ou a receber. A rota trava (`_mes_navegavel` em `app.py`) e o seletor de mês ganha um `min`, então não dá para chegar lá nem pela URL.

Os lançamentos anteriores **continuam no banco**, intactos. Só deixam de ser considerados e exibidos — mover a data para trás faz tudo voltar a contar.

Existe porque reconstruir meses passados exigiria conferir conta por conta. A Lois decidiu em 25/08/2026 começar limpo em 01/08/2026 em vez de arrastar um histórico pela metade.

Organização sem data definida não muda de comportamento: nada trava e o cálculo soma desde o primeiro lançamento.

Configuração em ⚙️ → Início do Sistema: primeiro a data, que governa tudo, depois quanto havia em cada conta naquele dia.

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

**Autenticação**: header `X-Api-Token` com o `api_token` da organização. Token inválido ou ausente → `401`; organização sem integração ligada → `403` (ver §5.3). O token define em qual tenant o lançamento é gravado — não há tenant no corpo da requisição.

**Validação do corpo** (25/08/2026): `valor` precisa ser numérico e `vencimento`, se vier, precisa estar em `AAAA-MM-DD`. Fora disso, `400` com o campo problemático citado.

Isso não é preciosismo. O sistema inteiro assume `AAAA-MM-DD` e as listagens filtram com `strftime('%Y-%m', vencimento)`, que devolve `NULL` para qualquer outro formato — uma receita gravada como `"31/12/2026"` ficava no banco e **não aparecia em nenhuma tela mensal**. Dinheiro invisível é pior que dinheiro errado: ninguém procura o que não sabe que existe.

**Idempotência**: o campo `referencia_id` é gravado em `observacoes` como a tag `ID Ref: {referencia_id}`. Em chamadas seguintes, `buscar_lancamento_por_referencia` localiza o lançamento existente e o **atualiza** em vez de duplicar (`201` na criação, `200` na atualização).

**Somente leitura**: lançamentos com `"ID Ref:"` têm edição, exclusão e troca de status bloqueados — na interface **e nas rotas**, não só no template. A fonte da verdade é o sistema de origem. Vale apenas quando a organização usa integração (§5.3).

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

### 5.3 A integração é opcional, por organização

`tenants.integracao_ativa` decide se a organização recebe lançamentos de um sistema externo. Marcável em ⚙️ → Organizações, junto do token de API.

| | **Ligada** | **Desligada** |
|---|---|---|
| Lançamentos com `ID Ref:` | somente leitura | editáveis à mão |
| Atrasados | consolidados numa linha | um por um, como na esfera Casa |
| Webhook | aceita | responde **403**, mesmo com token válido |

Token válido não basta de propósito: se a organização não usa integração, um envio externo criaria linhas que ninguém pediu e que ficariam travadas.

A verificação vive num helper só — `_lancamento_da_integracao` em `app.py` — em vez de espalhada pelas rotas e pelo template.

Organização nova nasce **desligada**. A migração liga para quem já tinha lançamento vindo da clínica: desligar algo que já funcionava seria quebrar produção em silêncio.

---

### 5.4 Estado visível da integração

Toda chamada do webhook (envio ou limpeza) carimba `tenants.ultima_integracao`. A tela `/receber` usa esse carimbo para dizer, **acima dos números**, de quando eles são:

| Situação | O que aparece |
|---|---|
| Recebeu hoje/ontem | linha discreta: "Última sincronização da clínica: hoje às 19:39." |
| Silêncio ≥ 3 dias (`DIAS_ATE_ESTRANHAR_A_INTEGRACAO`) | alerta vermelho com a contagem de dias e a última data |
| Integração ligada, nada recebido ainda | aviso amarelo |
| **Tem histórico da clínica e a integração está desligada** | alerta vermelho — o webhook está devolvendo 403 em silêncio |

A tela ⚙️ → Organizações mostra o mesmo carimbo por organização, e desmarcar a integração de quem já recebe dados externos pede confirmação.

**Por que isso existe.** Em 04/09/2026 a integração passou 8 dias gravando na organização errada — o token no WSGI da clínica era de outro tenant. O webhook respondia "sucesso" a cada envio, e a tela da organização certa mostrava números de agosto sem nada indicando que eram velhos. Três dias de diagnóstico por dedução; a consulta ao banco resolveu em trinta segundos. O limite é 3 dias, e não 1, porque a clínica não atende todo dia — segunda-feira depois de um fim de semana parado não pode virar alarme falso.

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
| `/exportar/<tipo>` | Baixa os lançamentos do mês em CSV. Respeita o filtro de esfera e, em Pagar, o filtro de nível. Separador `;`, vírgula decimal e BOM `utf-8-sig` — é o que faz o arquivo abrir certo no Excel em português |

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

### Configurações (⚙️ no canto superior direito)
| Rota | Descrição | Quem acessa |
|---|---|---|
| `/categorias` | CRUD de categorias | qualquer usuário |
| `/configuracoes/saldo-inicial` | Data de início e saldo de cada esfera | qualquer usuário |
| `/configuracoes/importancia` | Tabela de importância, editável | só admin |
| `/admin/tenants` | Organizações | só admin |

O menu principal ficou só com Dashboard, Contas a Pagar e Contas a Receber (25/08/2026); o resto desceu para o dropdown da engrenagem, agrupado em "Minha conta" e "Configurações".

### API
| Rota | Descrição |
|---|---|
| `POST /api/v1/receber/webhook` | Integração externa autenticada por `X-Api-Token` (ver §5) |

---

## 7. Pontos de atenção para a evolução

1. **`debug=True` só afeta o ambiente local.** `app.run(port=5002, debug=True)` está dentro de `if __name__ == "__main__"`, e o PythonAnywhere roda via WSGI importando o objeto `app` — a linha não executa em produção. Só vale atenção se um dia o app for iniciado direto por `python app.py` num servidor exposto.

2. **Recuperação de senha e convite dependem do Gmail SMTP.** Sem `.env` configurado, o convite cai no fallback de exibir a senha na tela; vale confirmar o comportamento de `/esqueci-senha` no mesmo cenário.

3. **`marcar_deve_trocar_senha()` existe mas nenhuma rota a chama.** Mantida de propósito: é metade do caminho para um botão "resetar senha deste usuário" em Organizações, que não existe e vai fazer falta.

4. **Achados de auditoria levantados e ainda não corrigidos** (evidência em `plans/README.md`): excluir categoria em uso derruba a tela com `FOREIGN KEY constraint failed`; o contador de tentativas de login cresce sem limite na memória; o upload de foto não tem `MAX_CONTENT_LENGTH`.

5. **A coluna legada `lancamentos.importancia`** (texto) não é mais lida. Mantida como rede de segurança da conversão para `importancia_nivel` feita em 25/08/2026 — vale apagar depois de a escala nova rodar alguns meses em dados reais.

---

## 8. Como verificar o sistema

**Não existe suíte de testes nem CI.** A rede de segurança é a skill `run-sistema-financeiro`, commitada em `.claude/skills/`:

```bash
./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke
```

Sobe o app num banco temporário, exercita 112 checagens e sai com 0 ou 1. **Nunca toca no `financeiro.db` real.** Rodar depois de qualquer mudança.

`driver.py serve` sobe com dados de demonstração e imprime as credenciais, para inspeção no navegador.

O `SKILL.md` ao lado documenta as armadilhas do projeto — entre elas que trocar `database.NOME_DO_BANCO` precisa vir **antes** de importar o app, e que o token CSRF é injetado por JavaScript e não existe como campo no HTML servido.

---

## 9. Documentos relacionados

- `plans/` — planos de implementação com o histórico das decisões. `README.md` é o índice.
- `Agentes/Roteiro_Deploy_PythonAnywhere.md` — passos de publicação.
- `Agentes/Plano_Multi_Tenancy.md` e `Roteiro_Testes_Multi_Tenancy.md` — registro histórico da migração de agosto/2026, já executada.

Deploy: `git push` no Mac → **`git pull`** no PythonAnywhere → **Reload** na aba Web.
