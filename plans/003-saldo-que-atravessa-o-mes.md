# Plan 003: Fazer o saldo atravessar o mês, em regime de caixa

> **Executor instructions**: Siga este plano passo a passo. Rode cada comando de
> verificação e confirme o resultado esperado antes de seguir. Se algo na seção
> "STOP conditions" acontecer, pare e reporte — não improvise. Ao terminar,
> atualize a linha deste plano em `plans/README.md`.
>
> **Drift check (rode primeiro)**:
> `git diff --stat dec3b87..HEAD -- app.py calculos.py database.py templates/index.html`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED — muda o número mais visível do sistema (o saldo do dashboard)
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `dec3b87`, 2026-08-25

## Why this matters

O dashboard hoje é uma fotografia isolada de cada mês: o "Saldo Atual em Caixa"
é `recebido do mês − pago do mês`. Nada atravessa a virada. Se sobrou dinheiro
em agosto, setembro começa do zero — e a Lois nunca vê a posição real de caixa.

Pior: o número atual **não é caixa**. As listagens filtram por `vencimento`, mas
somam por `status`. Uma conta que venceu em julho e foi paga em agosto conta
como saída de julho, embora o dinheiro tenha saído da conta em agosto. Carregar
esse número entre meses só propagaria o erro.

Depois deste plano, o dashboard mostra de onde o saldo veio e para onde vai:

```
Vem do mês anterior      R$ 2.400,00
(+) Entrou no mês        R$ 6.050,00
(−) Saiu no mês          R$ 4.050,00
Fecha o mês com          R$ 4.400,00
```

Decisões já tomadas com a Lois em 25/08/2026, que este plano implementa:

- **Caixa separado por esfera** (Empresa e Casa são contas bancárias
  diferentes). O filtro do topo escolhe qual saldo aparece; "Todas" soma os dois.
- **A data que define o mês do dinheiro é `data_pagamento`**, não o vencimento.
  Onde ela faltar, usar `vencimento` como aproximação.
- Fechamento de mês **não** trava edição e **não** é obrigatório — é assunto do
  plano 004 e não deve ser antecipado aqui.

### Deixar a porta aberta para a visão por competência

Pedido explícito da Lois em 25/08/2026: a visão por **competência** (a que mês o
gasto pertence, independente de quando o dinheiro se moveu) pode ser útil no
futuro — para o contador, ou para responder "esse mês deu lucro?".

Este plano **não** implementa competência. O que ele faz é não fechar a porta: a
escolha da data que define o mês fica num **lugar só**, parametrizada, em vez de
espalhada em SQL pelo código.

Concretamente, a consulta do Step 2 recebe `base="caixa"` como padrão e resolve a
expressão de data assim:

- `caixa` → `COALESCE(data_pagamento, vencimento)` e só lançamentos efetivados
- `competencia` → `vencimento`, independente de status

Só `caixa` é exercitado agora. O ramo de competência existe como seam de três
linhas, não como funcionalidade — não construa tela, cálculo nem teste para ele.
Quando a visão por competência for pedida, o trabalho é a tela, não a
reengenharia da consulta.

## Current state

- `calculos.py` — `calcular_resumo_financeiro(tenant_id, esfera, mes_ano)` monta
  os números do dashboard. O saldo sai assim:

```python
    resumo["saldo_atual"] = resumo["receber_pago"] - resumo["pagar_pago"]
    resumo["saldo_projetado"] = resumo["receber_total"] - resumo["pagar_total"]
```

- `database.py` — `listar_lancamentos(tenant_id, tipo, esfera, mes_ano, ...)`
  filtra por mês assim (é o filtro por **vencimento** citado acima):

```python
query += " AND (strftime('%Y-%m', lancamentos.vencimento) = ? OR (lancamentos.vencimento < ? AND lancamentos.status NOT IN ('Pago', 'Recebido')))"
```

- `app.py` — `pagina_inicial()` chama `calcular_resumo_financeiro`,
  `calcular_despesas_por_categoria` e `calcular_gastos_por_importancia`, e
  renderiza `templates/index.html`.
- `templates/index.html` — quatro cartões no topo; o primeiro é "Saldo Atual em
  Caixa", ligado a `resumo.saldo_atual`.

Convenções a respeitar:

- **Sem dependências novas.** `requirements.txt` tem três pacotes.
- **Migrações**: `criar_tabelas()` em `database.py` cria tabelas com
  `CREATE TABLE IF NOT EXISTS` e usa os helpers `_tabela_existe` /
  `_coluna_existe` para alterações. Siga esse padrão; nunca escreva um script de
  migração separado.
- **Multi-tenant**: toda query de dado carrega `tenant_id`. Veja
  `listar_lancamentos` como exemplar.
- **Comentários e textos de tela em português.**

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Compilar | `./venv/bin/python -m py_compile app.py calculos.py database.py` | exit 0 |
| Smoke | `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke` | exit 0, 60+ checagens |
| Ver na tela | `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py serve` | imprime a URL e as credenciais |

Não existe suíte de testes nem linter. O smoke é a rede de regressão, e **nunca
toca no `financeiro.db` real** — usa banco temporário.

## Scope

**In scope**: `database.py`, `calculos.py`, `app.py`, `templates/index.html`,
uma tela nova de saldo inicial em `templates/`, e o driver do smoke.

**Out of scope**:
- Fechamento de mês, tela de conferência, tabela `fechamentos_mensais` — é o
  plano 004. Este plano só calcula; não registra nada que a Lois confirme.
- Travar edição de meses passados — decisão dela foi **não travar**.
- Mudar `listar_lancamentos` — outras telas dependem do filtro por vencimento e
  quebrariam. Este plano **acrescenta** uma consulta nova, não altera a que existe.
- `templates/pagar.html` e `receber.html` — as listagens continuam por vencimento.

## Steps

### Step 1: Guardar o saldo inicial de cada esfera

Em `database.py`, dentro de `criar_tabelas()`, crie:

```sql
CREATE TABLE IF NOT EXISTS saldos_iniciais (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants (id),
    esfera TEXT NOT NULL,            -- 'Empresa' ou 'Casa'
    valor REAL NOT NULL DEFAULT 0,
    data_referencia TEXT NOT NULL,   -- AAAA-MM-DD: a partir de quando vale
    UNIQUE (tenant_id, esfera)
)
```

Por que existe: o sistema não conhece a vida da clínica antes dos primeiros
lançamentos. Sem um ponto de partida, todo saldo acumulado nasce errado.

Funções: `obter_saldos_iniciais(tenant_id)` (devolve dict por esfera) e
`definir_saldo_inicial(tenant_id, esfera, valor, data_referencia)`.

**Verify**: `./venv/bin/python -m py_compile database.py` → exit 0.

### Step 2: Consultar movimentação por data de pagamento

Ainda em `database.py`, crie `listar_movimentacoes_caixa(tenant_id, esfera, data_inicio, data_fim)`.

Devolve os lançamentos **efetivados** (status `Pago` ou `Recebido`) cuja data de
caixa cai no intervalo. A data de caixa é `COALESCE(data_pagamento, vencimento)`
— muitos lançamentos vindos do webhook têm status `Pago` mas `data_pagamento`
nulo, e sem o fallback eles sumiriam do saldo.

`data_inicio` pode ser `None` (significa "desde sempre"). `esfera` igual a
`"Todas"` não filtra esfera.

**Verify**: com o app rodando via driver, conferir que a soma das movimentações
de um mês bate com o que a tela mostra como pago/recebido daquele mês.

### Step 3: Calcular o saldo acumulado

Em `calculos.py`, crie `calcular_saldo_do_mes(tenant_id, esfera, mes_ano)`.

Devolve um dicionário com:

- `saldo_inicial_periodo` — o que vem do mês anterior: saldo inicial da esfera
  mais tudo que se movimentou entre a `data_referencia` e o último dia do mês
  anterior;
- `entrou` / `saiu` — movimentação de caixa **do mês**, por data de pagamento;
- `saldo_final` — `saldo_inicial_periodo + entrou - saiu`;
- `tem_saldo_inicial` — `False` quando a esfera ainda não tem saldo inicial
  definido, para a tela poder avisar em vez de mostrar um número enganoso.

Com `esfera="Todas"`, some as duas esferas.

**Verify**:
```bash
./venv/bin/python -c "
import database, calculos
# rode contra um banco temporário do driver, não contra financeiro.db
print('funções existem:', hasattr(calculos, 'calcular_saldo_do_mes'))
"
```

### Step 4: Mostrar no dashboard

Em `app.py`, `pagina_inicial()` passa o novo cálculo ao template.

Em `templates/index.html`, o primeiro cartão ("Saldo Atual em Caixa") passa a
mostrar `saldo_final`, e ganha logo abaixo as quatro linhas do extrato do mês
(vem do anterior / entrou / saiu / fecha com). Siga o estilo dos cartões
existentes; Bootstrap 5.

Quando a esfera não tiver saldo inicial definido, o cartão mostra o valor
calculado **e** um aviso discreto com link para a tela do Step 5, dizendo que o
saldo ainda não tem ponto de partida.

**Verify**: `driver.py serve`, abrir o dashboard e conferir que as quatro linhas
aparecem e que `vem do anterior + entrou − saiu = fecha com`.

### Step 5: Tela para definir o saldo inicial

Rota `/configuracoes/saldo-inicial` (GET e POST), protegida por `@login_required`
— não é `@admin_required`: o saldo é da organização e quem usa o sistema precisa
poder ajustar.

Um formulário por esfera: valor e data de referência. Salva com
`definir_saldo_inicial`.

Adicionar no dropdown da engrenagem em `templates/base.html`, no grupo
"Configurações", junto de Categorias.

**Verify**: definir um saldo inicial, voltar ao dashboard e confirmar que "vem do
mês anterior" mudou de acordo.

### Step 6: Blindar no smoke

Em `driver.py`, na função `cmd_smoke`, acrescente uma seção "Saldo do mês" com o
helper `checa` já existente, cobrindo:

- as quatro linhas aparecem no dashboard;
- `vem do anterior + entrou − saiu == fecha com` (calcule pelos números do banco,
  não leia da tela);
- um pagamento com `data_pagamento` no mês seguinte ao vencimento entra no saldo
  do **mês do pagamento**, não no do vencimento — é a correção central deste plano;
- lançamento `Pago` sem `data_pagamento` continua contando (fallback do Step 2);
- esfera Empresa e Casa têm saldos independentes;
- sem saldo inicial definido, a tela avisa em vez de mostrar número seco.

**Verify**: `driver.py smoke` → exit 0, contagem maior que 60.

## Done criteria

- [ ] `./venv/bin/python -m py_compile app.py calculos.py database.py` sai 0
- [ ] `driver.py smoke` sai 0 com mais de 60 checagens
- [ ] O dashboard mostra as quatro linhas e elas fecham aritmeticamente
- [ ] Trocar a esfera no topo muda o saldo exibido
- [ ] `git status --short` mostra só os arquivos em escopo
- [ ] Linha do plano 003 atualizada em `plans/README.md`

## STOP conditions

- O código não corresponde aos trechos de "Current state".
- Uma verificação falha duas vezes após uma tentativa razoável.
- Você concluir que precisa alterar `listar_lancamentos` — não precisa, e mexer
  nela afeta todas as telas.
- Você descobrir que a soma por `data_pagamento` faz o total do mês **divergir
  muito** do que a tela mostra hoje (mais de uns poucos lançamentos deslocados).
  Isso significaria que a base tem muito pagamento em mês diferente do
  vencimento, e a Lois precisa saber o tamanho da mudança antes de ela entrar.
  Reporte com números.

## Maintenance notes

- **O que revisar**: que a esfera "Todas" some as duas e não conte nada duas
  vezes; e que o fallback `COALESCE(data_pagamento, vencimento)` esteja em um
  lugar só, para o plano 004 reaproveitar.
- **Interação futura**: o plano 004 (fechar o mês) vai usar
  `calcular_saldo_do_mes` como base e substituir o `saldo_inicial_periodo` pelo
  saldo confirmado no fechamento anterior, quando existir. Deixe essa função com
  assinatura estável.
- **Fora deste plano de propósito**: preencher as datas de pagamento que faltam
  nos lançamentos antigos. O fallback resolve o cálculo; uma tela de arrumação é
  trabalho separado, e a Lois preferiu não gastar tempo nisso agora.
