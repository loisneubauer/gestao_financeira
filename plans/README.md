# Planos de Implementação

Gerados pela skill `improve` em 2026-08-25, contra o commit `b7ffa07`, no modo
`quick` (correção, segurança e testes nos pontos quentes).

Execute na ordem abaixo, salvo indicação em contrário nas dependências. Cada
executor: leia o plano inteiro antes de começar, respeite as STOP conditions, e
atualize sua linha na tabela ao terminar.

## Ordem de execução e status

| Plano | Título | Prioridade | Esforço | Depende de | Status |
|---|---|---|---|---|---|
| 001 | Validar o corpo do webhook para que nenhuma receita fique invisível | P1 | S | — | DONE |
| 002 | Fazer valor inválido devolver aviso na tela em vez de erro 500 | P2 | S | 001 | DONE |
| 003 | Fazer o saldo atravessar o mês, em regime de caixa | P1 | M | — | DONE |
| 004 | Fechar o mês sozinho, na primeira visita depois da virada | P2 | M | 003 | TODO |

Valores de status: TODO | IN PROGRESS | DONE | BLOCKED (com motivo em uma linha) | REJECTED (com justificativa em uma linha)

**001 — executado e aprovado em 2026-08-25.** Executor despachado em worktree
isolado; commit `974f982` no branch `advisor/001-validar-entrada-do-webhook`,
sobre `b7ffa07`. Revisão do advisor: todos os critérios de aceitação rodados de
novo no worktree (py_compile, script do Step 3, smoke com 25 checagens, ausência
de `float(dados["valor"])`, escopo limpo). As três checagens novas foram
auditadas revertendo `app.py` ao estado anterior: duas falham sem a correção
(201 e 500 em vez de 400) e a terceira continua passando — são regressão de
verdade, não decoração. Mesclado ao `main` local por fast-forward em
2026-08-25, **sem push**.

**002 — executado e aprovado em 2026-08-25**, em duas rodadas. Commit `9a24fda`,
mesclado ao `main` local por fast-forward, **sem push**.

A primeira rodada precisou de revisão por erro do advisor, não do executor: o
worktree do executor é criado a partir de um commit **anterior** ao merge do
plano 001, então `_valor_numerico` não estava lá e ele criou uma segunda cópia
(seguindo corretamente o fallback do próprio plano, e reportando a
discrepância). Simulação com `git merge-tree` confirmou que o merge geraria
duas definições da mesma função. Resolvido com rebase sobre o `main` e remoção
da cópia.

**Lição para planos encadeados**: mesclar o plano anterior não basta — o
executor seguinte precisa rebasear, porque o worktree dele não parte do `main`
no momento do despacho.

Revisão do advisor: todos os critérios rodados de novo (py_compile, helper
único, ausência de `float(request.form...)`, smoke com 28 checagens, 4 rotas
cobertas). As checagens novas foram auditadas revertendo `app.py`: falham sem a
correção (HTTP 500).

## Fechamento mensal (planos 003 e 004)

Desenhado com a Lois em 25/08/2026, a partir do problema que ela levantou: o
dashboard mostra o mês isolado, então o caixa nunca reflete a realidade.

Ao levantar o terreno apareceu um problema anterior ao pedido: o saldo de hoje
**não é caixa**. As listagens filtram por `vencimento` e somam por `status`, então
uma conta que venceu em julho e foi paga em agosto conta como saída de julho.
Carregar esse número entre meses só propagaria o erro — por isso o plano 003
troca a base de cálculo para a data de pagamento antes de qualquer coisa.

Decisões dela, que os planos implementam e não devem ser revisitadas por conta
própria:

- **Caixa separado por esfera** (Empresa e Casa são contas diferentes).
- **A data que define o mês do dinheiro é `data_pagamento`**, com `vencimento`
  como fallback onde ela faltar.
- **Fechar não trava** edição de mês passado.
- **Fechamento é automático**, na primeira visita ao dashboard depois da virada
  do mês. **Sem botão manual** e sem cobrar nada de ninguém.

Como não há agendador utilizável (Tarefa Agendada do PythonAnywhere roda 1x/dia
no plano grátis), o fechamento acontece "de carona" numa visita de tela — mesmo
padrão que a V3 da clínica já usa para o Google Calendar e para a sincronização
com este sistema.

**Limite conhecido, registrado de propósito**: o fechamento automático grava o
que o sistema apurou, não o que o banco diz. A conferência com o extrato existe
(plano 004, Step 5) mas é opcional — enquanto ninguém conferir, o saldo é a
melhor estimativa do sistema, não a verdade do extrato.

## Notas de dependência

- **004 depende de 003**: usa `calcular_saldo_do_mes` como base e substitui o
  ponto de partida pelo saldo do fechamento anterior. Executar o 004 antes é
  STOP condition explícita nele.
- **002 depende de 001** apenas por reaproveitamento: o helper `_valor_numerico`
  é criado no 001 e usado nos dois. O plano 002 traz instruções para criá-lo
  caso o 001 ainda não tenha sido executado, então a ordem é preferível mas não
  obrigatória.
- Os dois compartilham a mesma raiz: **o código converte entrada externa sem
  guarda**. O 001 trata a porta da API (impacto maior: dinheiro que some das
  telas), o 002 trata a porta do formulário (impacto menor: erro 500 e
  formulário perdido).

## Como verificar qualquer mudança neste projeto

Não existe suíte de testes nem CI. A única verificação automatizada é:

```bash
./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke
```

Ela sobe o app num banco temporário, exercita os fluxos principais e sai com 0
ou 1. **Nunca toca no `financeiro.db` real.** Os dois planos acrescentam
checagens a ela — é assim que estas correções ficam protegidas contra regressão.

## Achados considerados e não planejados

Levantados e verificados nesta auditoria, mas **não** selecionados para plano
nesta rodada (a escolha foi da dona do projeto). Registrados para não serem
reauditados do zero:

- **Excluir categoria em uso derruba a tela.** `DELETE` em categoria referenciada
  por lançamento levanta `sqlite3.IntegrityError: FOREIGN KEY constraint failed`
  e devolve HTTP 500 (`database.py:359`, rota em `app.py:734`). Confirmado
  executando. Correção: checar uso antes de apagar e devolver aviso, ou decidir
  o comportamento (bloquear vs. desvincular). Esforço S.
- **Contador de tentativas de login cresce sem limite.** `_tentativas_login`
  (`app.py:151`) só descarta entradas expiradas quando aquela mesma chave é
  consultada de novo; 300 tentativas com e-mails diferentes deixaram 300 chaves
  na memória. Confirmado executando. Correção: varredura periódica ou limite de
  tamanho. Esforço S.
- **Upload de foto de perfil sem limite de tamanho.**
  `app.config["MAX_CONTENT_LENGTH"]` é `None`; o upload em `app.py:274` aceita
  arquivo de qualquer tamanho. Correção: definir `MAX_CONTENT_LENGTH` e tratar
  o `RequestEntityTooLarge`. Esforço S.

## Achados considerados e rejeitados

- **Linha consolidada de atraso com id textual.** A tela `/receber` monta uma
  linha sintética com `id="atraso_consolidado"` (texto), enquanto as rotas
  esperam `<int:id_lancamento>`. Verificado no HTML renderizado: **o template
  não gera link nem formulário para essa linha**, então nenhuma rota é
  alcançada com o id textual. Não é um defeito.

## O que não foi auditado

O modo `quick` cobre correção, segurança e testes nos pontos de maior risco.
Ficaram de fora: performance, dívida técnica e arquitetura, dependências e
migrações, DX/tooling e documentação. Os templates foram varridos apenas em
busca de XSS (`|safe`, autoescape) — nenhum problema encontrado. O projeto
`acupuntura_sistema V3`, que se integra a este pelo webhook, não foi auditado.
