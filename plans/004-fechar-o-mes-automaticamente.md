# Plan 004: Fechar o mês sozinho, na primeira visita depois da virada

> **Executor instructions**: Siga este plano passo a passo. Rode cada comando de
> verificação e confirme o resultado esperado antes de seguir. Se algo na seção
> "STOP conditions" acontecer, pare e reporte — não improvise. Ao terminar,
> atualize a linha deste plano em `plans/README.md`.
>
> **Drift check (rode primeiro)**:
> `git diff --stat dec3b87..HEAD -- app.py calculos.py database.py templates/index.html`

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW — só registra; não altera nenhum lançamento
- **Depends on**: `plans/003-saldo-que-atravessa-o-mes.md` — usa
  `calculos.calcular_saldo_do_mes` como base. **Não comece antes do 003 estar DONE.**
- **Category**: direction
- **Planned at**: commit `dec3b87`, 2026-08-25

## Why this matters

Com o plano 003, o saldo atravessa o mês, mas é sempre recalculado do zero: a
partir do saldo inicial, somando todo o histórico. Isso funciona, e tem dois
buracos.

O primeiro é histórico: não fica registro de quanto o mês fechou. Se um
lançamento antigo for corrigido em novembro, o saldo de agosto muda
retroativamente e ninguém percebe — não há com o que comparar.

O segundo é a realidade: o saldo é o que o **sistema** acha, não o que o **banco**
diz. Enquanto ninguém comparar com o extrato, o caixa pode não bater e o desvio
se arrasta.

Decisão da Lois em 25/08/2026: **fechar deve ser automático, no dia primeiro do
mês seguinte, sem botão manual.** Este plano implementa isso — e deixa a
conferência com o extrato disponível para quando ela quiser, sem nunca cobrar.

Depois deste plano, na primeira vez que alguém abrir o dashboard depois da
virada, o mês anterior é fechado sozinho e vira um registro permanente.

## Current state

Depende do plano 003, que já terá entregue:

- Tabela `saldos_iniciais` e funções `obter_saldos_iniciais` /
  `definir_saldo_inicial`.
- `database.listar_movimentacoes_caixa(tenant_id, esfera, data_inicio, data_fim)`
  — movimentação efetivada por data de caixa (`COALESCE(data_pagamento, vencimento)`).
- `calculos.calcular_saldo_do_mes(tenant_id, esfera, mes_ano)` devolvendo
  `saldo_inicial_periodo`, `entrou`, `saiu`, `saldo_final`, `tem_saldo_inicial`.
- Dashboard mostrando as quatro linhas do extrato do mês.

**Confirme que existem antes de começar**:
```bash
grep -n "def calcular_saldo_do_mes" calculos.py
grep -n "def listar_movimentacoes_caixa" database.py
```
Se não existirem, o plano 003 não foi executado — isso é STOP condition.

Padrão já usado neste projeto para trabalho periódico **sem agendador** (é o que
este plano deve seguir): a V3 da clínica sincroniza os bloqueios do Google
Calendar "de carona" numa visita de tela, no máximo 1x a cada N minutos, porque
Tarefa Agendada do PythonAnywhere só roda 1x/dia no plano grátis. Aqui vale a
mesma ideia, com granularidade de mês em vez de minutos.

## Commands you will need

| Purpose | Command | Expected |
|---|---|---|
| Compilar | `./venv/bin/python -m py_compile app.py calculos.py database.py` | exit 0 |
| Smoke | `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke` | exit 0 |
| Ver na tela | `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py serve` | imprime URL e credenciais |

## Scope

**In scope**: `database.py`, `calculos.py`, `app.py`, `templates/index.html`,
uma tela nova de histórico de fechamentos, e o driver do smoke.

**Out of scope**:
- **Botão "Fechar mês"** — decisão explícita da Lois: fechamento é automático,
  sem ação manual. Não crie o botão.
- **Travar edição de mês fechado** — decisão dela foi não travar. No máximo um
  aviso; nunca um bloqueio.
- **Cobrar/alertar fechamento pendente** — não existe pendência: o sistema fecha
  sozinho.
- Alterar lançamentos. Este plano só lê e registra.

## Steps

### Step 1: Guardar os fechamentos

Em `database.py`, dentro de `criar_tabelas()`:

```sql
CREATE TABLE IF NOT EXISTS fechamentos_mensais (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL REFERENCES tenants (id),
    esfera TEXT NOT NULL,             -- 'Empresa' ou 'Casa'
    mes TEXT NOT NULL,                -- 'AAAA-MM'
    saldo_calculado REAL NOT NULL,    -- o que o sistema apurou ao fechar
    saldo_informado REAL,             -- o que a Lois confirmou pelo extrato (NULL = não conferido)
    entrou REAL NOT NULL DEFAULT 0,
    saiu REAL NOT NULL DEFAULT 0,
    fechado_em TEXT NOT NULL,
    observacao TEXT,
    UNIQUE (tenant_id, esfera, mes)
)
```

`saldo_informado` nasce nulo: o fechamento automático registra o que o sistema
apurou, e a conferência com o extrato é opcional e posterior (Step 5).

Funções: `buscar_fechamento(tenant_id, esfera, mes)`,
`listar_fechamentos(tenant_id)`, `registrar_fechamento(...)` e
`informar_saldo_real(tenant_id, esfera, mes, saldo_informado, observacao)`.

**Verify**: `./venv/bin/python -m py_compile database.py` → exit 0.

### Step 2: Fechar os meses que já viraram

Em `calculos.py`, crie `fechar_meses_pendentes(tenant_id)`.

Regra: fecha todo mês **anterior ao mês corrente** que ainda não tem fechamento,
para cada esfera (`Empresa` e `Casa`), do mais antigo para o mais novo. O mês
corrente nunca fecha — ainda está em curso.

Por onde começar: o mês do `data_referencia` do saldo inicial da esfera; se não
houver saldo inicial, o mês do lançamento mais antigo. Se não houver nem
lançamento, não há o que fechar.

Cada fechamento grava `saldo_calculado`, `entrou` e `saiu` daquele mês, vindos de
`calcular_saldo_do_mes`.

Devolve quantos meses fechou, para o smoke poder verificar.

**Nunca levanta exceção**: fechar é conveniência, não pode derrubar o dashboard.
Erro vira aviso no log, como em `sincronizar_se_necessario` da V3.

**Verify**: `./venv/bin/python -m py_compile calculos.py` → exit 0.

### Step 3: Disparar na visita ao dashboard

Em `app.py`, no começo de `pagina_inicial()`, chame `fechar_meses_pendentes(tenant_atual())`.

É o padrão "de carona" já adotado no projeto: nada de tarefa agendada. Barato,
porque na esmagadora maioria das visitas não há mês pendente — a função sai cedo
depois de uma consulta.

**Verify**: `./venv/bin/python -m py_compile app.py` → exit 0.

### Step 4: Usar o fechamento como âncora do mês seguinte

Em `calculos.calcular_saldo_do_mes`, o `saldo_inicial_periodo` passa a vir, nesta
ordem de preferência:

1. do `saldo_informado` do fechamento do mês anterior, se a Lois tiver conferido;
2. senão, do `saldo_calculado` daquele fechamento;
3. senão, do cálculo desde o saldo inicial (comportamento do plano 003).

É isso que faz a conferência valer a pena: quando ela corrige um mês pelo
extrato, o desvio para de se arrastar para os meses seguintes.

**Verify**: incluído no Step 6.

### Step 5: Deixar conferir com o extrato, sem cobrar

Tela `/configuracoes/fechamentos` (GET), com o histórico: mês, esfera, entrou,
saiu, saldo calculado, saldo informado e a diferença quando houver.

Cada linha tem um campo para informar o saldo real do extrato e uma observação.
Ao salvar (POST), grava `saldo_informado` e mostra a diferença.

Duas regras de tom, que vêm de decisão da Lois:

- **Não é pendência.** Nada de badge vermelho, alerta ou "você não conferiu".
  Meses sem conferência aparecem neutros, com o saldo calculado.
- **Nada trava.** Informar um saldo diferente não altera lançamento nenhum; só
  reancora o mês seguinte.

Adicionar no dropdown da engrenagem (`templates/base.html`), grupo
"Configurações".

**Verify**: informar um saldo diferente do calculado e conferir que o mês
seguinte, no dashboard, passa a partir do valor informado.

### Step 6: Blindar no smoke

Em `driver.py`, seção nova "Fechamento de mês", cobrindo:

- com lançamentos em meses passados, abrir o dashboard fecha os meses anteriores
  sozinho, e o número de fechamentos criados bate com o esperado;
- **o mês corrente nunca é fechado**;
- abrir o dashboard de novo não duplica fechamento (idempotente);
- Empresa e Casa fecham separadamente;
- informar um saldo real diferente muda o ponto de partida do mês seguinte;
- fechamento existente não é sobrescrito por uma visita nova.

**Verify**: `driver.py smoke` → exit 0.

## Done criteria

- [ ] `./venv/bin/python -m py_compile app.py calculos.py database.py` sai 0
- [ ] `driver.py smoke` sai 0, com as checagens novas passando
- [ ] Abrir o dashboard duas vezes seguidas não cria fechamento duplicado
- [ ] O mês corrente não aparece em `fechamentos_mensais`
- [ ] `grep -rn "Fechar mês" templates/` não retorna nada (não existe botão manual)
- [ ] `git status --short` mostra só os arquivos em escopo
- [ ] Linha do plano 004 atualizada em `plans/README.md`

## STOP conditions

- `calcular_saldo_do_mes` ou `listar_movimentacoes_caixa` não existirem — o plano
  003 não foi executado.
- Uma verificação falhar duas vezes após uma tentativa razoável.
- Você concluir que precisa de tarefa agendada, cron ou processo em segundo
  plano. Não precisa, e o plano grátis do PythonAnywhere não oferece isso de
  forma útil (1x/dia).
- O fechamento automático ficar lento a ponto de pesar na abertura do dashboard
  (mais de ~1s numa base com poucos meses). Reporte com números em vez de
  otimizar por conta própria.

## Maintenance notes

- **O que revisar**: idempotência (visitar o dashboard N vezes cria no máximo um
  fechamento por mês/esfera) e a garantia de que o mês corrente nunca fecha.
- **Interação futura**: se um dia um mês precisar ser reaberto (correção grande
  em lançamento antigo), vai faltar uma forma de apagar o fechamento. Não faz
  parte deste plano — a Lois não pediu, e inventar isso agora seria adivinhação.
  Se aparecer a necessidade, é um plano curto.
- **Limite conhecido**: enquanto ninguém informar o saldo do extrato, o número é
  o que o sistema apura, não o que o banco diz. O fechamento automático registra
  e dá continuidade, mas **não** valida contra a realidade — só a conferência do
  Step 5 faz isso.
