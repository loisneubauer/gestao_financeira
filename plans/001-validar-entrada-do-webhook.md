# Plan 001: Validar o corpo do webhook para que nenhuma receita fique invisível

> **Executor instructions**: Siga este plano passo a passo. Rode cada comando de
> verificação e confirme o resultado esperado antes de seguir. Se algo na seção
> "STOP conditions" acontecer, pare e reporte — não improvise. Ao terminar,
> atualize a linha deste plano em `plans/README.md`.
>
> **Drift check (rode primeiro)**:
> `git diff --stat b7ffa07..HEAD -- app.py`
> Se `app.py` mudou desde que este plano foi escrito, compare os trechos da
> seção "Current state" com o código real antes de continuar; se não baterem,
> trate como STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `b7ffa07`, 2026-08-25

## Why this matters

O webhook `/api/v1/receber/webhook` grava o campo `vencimento` exatamente como
recebe, sem validar o formato. O sistema inteiro assume `AAAA-MM-DD`: as
listagens filtram com `strftime('%Y-%m', vencimento)`, que devolve `NULL` para
qualquer outro formato. O efeito foi reproduzido: uma receita de R$ 5.000,00
enviada com `"31/12/2026"` foi gravada no banco e **não apareceu em nenhuma tela
mensal** — nem em agosto, nem em dezembro. O dinheiro existe na tabela e some da
interface.

Isso é pior do que um valor errado: ninguém procura o que não sabe que existe. O
webhook é a porta de entrada das receitas vindas do sistema da clínica, então um
erro de formato do lado de lá vira receita invisível do lado de cá,
silenciosamente.

Depois deste plano, o webhook rejeita entrada malformada com `400` e uma
mensagem que diz o que está errado, em vez de aceitar e esconder.

## Current state

Arquivos relevantes:

- `app.py` — aplicação Flask inteira; a rota do webhook está no fim do arquivo
  (função `api_webhook_receber`, a partir da linha 997).
- `database.py` — camada de acesso; `listar_lancamentos` (linha 368) é quem
  filtra por mês com `strftime`.

O corpo da rota hoje, em `app.py:1005-1020`:

```python
    dados = request.json
    if not dados or "descricao" not in dados or "valor" not in dados:
        return jsonify({"erro": "Dados incompletos"}), 400

    ref_id = dados.get("referencia_id")
    existente = database.buscar_lancamento_por_referencia(tenant["id"], ref_id) if ref_id else None

    dados_lancamento = {
        "descricao": dados["descricao"],
        "tipo": "Receber",
        "esfera": "Empresa",
        "categoria_id": dados.get("categoria_id"),
        "valor": float(dados["valor"]),
        "vencimento": dados.get("vencimento", date.today().isoformat()),
        "status": dados.get("status", "Pendente"),
        "forma_pagamento": dados.get("forma_pagamento", "Pix"),
        "recorrente": 0,
        "observacoes": f"Gerado via Integração Clínica. ID Ref: {ref_id}"
    }
```

Note que a única validação é a presença das chaves `descricao` e `valor`. Nada
verifica o **formato** de `vencimento` nem o **tipo** de `valor`.

O filtro que quebra, em `database.py` dentro de `listar_lancamentos`:

```python
query += " AND (strftime('%Y-%m', lancamentos.vencimento) = ? OR (lancamentos.vencimento < ? AND lancamentos.status NOT IN ('Pago', 'Recebido')))"
```

Convenções deste repositório que o plano deve respeitar:

- **Comentários e mensagens em português**, incluindo as mensagens de erro da
  API. Veja a mensagem que já existe na mesma rota: `"Token de API inválido ou
  ausente. Envie o header X-Api-Token."`
- **Sem dependências novas.** O `requirements.txt` tem exatamente três pacotes
  (Flask, Werkzeug, itsdangerous) e o projeto não usa nenhuma biblioteca de
  validação de schema. Use a biblioteca padrão (`datetime`).
- **Funções auxiliares privadas usam prefixo `_`** e ficam acima de quem as usa.
  Exemplo no próprio arquivo: `_importancia_valida` (`app.py:139`), que valida um
  campo vindo de formulário e devolve `None` quando o valor não é aceitável.
- O `datetime` já está importado no topo de `app.py`:
  `from datetime import datetime, timedelta, date`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Instalar deps | `./venv/bin/pip install -r requirements.txt` | exit 0 |
| Compilar | `./venv/bin/python -m py_compile app.py` | exit 0, sem saída |
| Smoke test | `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke` | exit 0, "todas as N checagens passaram" |

Não existe suíte de testes nem linter neste projeto. O `driver.py smoke` é a
única verificação automatizada — ele sobe o app num banco temporário e exercita
os fluxos principais. **Nunca toca no `financeiro.db` real.**

## Scope

**In scope** (os únicos arquivos que você deve modificar):
- `app.py` — apenas a função `api_webhook_receber` e a adição de helpers
  privados imediatamente antes dela.
- `.claude/skills/run-sistema-financeiro/driver.py` — apenas para acrescentar
  checagens novas na função `cmd_smoke` (ver "Test plan").

**Out of scope** (NÃO altere, mesmo parecendo relacionado):
- `database.py` — a correção é validar na entrada, não mudar como o banco
  filtra. Mexer no `strftime` afetaria todas as telas do sistema.
- As rotas de formulário (`novo_pagar`, `editar_pagar`, `novo_receber`,
  `editar_receber`) — elas têm o mesmo problema com `float()`, mas são o
  assunto do `plans/002-*.md`. Não antecipe esse trabalho aqui.
- O formato do JSON de resposta em caso de **sucesso** — o sistema da clínica
  (`acupuntura_sistema V3`) depende das chaves `sucesso`, `acao` e
  `id_lancamento`. Só respostas de erro ganham conteúdo novo.
- `templates/` — este plano não tem efeito visual.

## Git workflow

- Branch: `advisor/001-validar-entrada-do-webhook`
- Um commit ao final, ou um por passo. Mensagens em português, no imperativo,
  seguindo o estilo do repositório. Exemplo real de `git log`:
  `Corrige atraso zerado e valor contado duas vezes na sincronizacao`
- **Não** faça push nem abra PR a menos que o operador peça.

## Steps

### Step 1: Escrever os helpers de validação

Em `app.py`, imediatamente **antes** da linha `@app.route("/api/v1/receber/webhook", methods=["POST"])`,
adicione duas funções privadas:

1. `_data_iso_valida(valor)` — recebe qualquer coisa e devolve a string
   `AAAA-MM-DD` se ela for uma data ISO válida, ou `None` caso contrário. Use
   `datetime.strptime(valor, "%Y-%m-%d")` dentro de um `try/except (ValueError, TypeError)`.
   Aceite apenas `str`.

2. `_valor_numerico(valor)` — recebe qualquer coisa e devolve um `float` se for
   convertível, ou `None` caso contrário. Envolva `float(valor)` em
   `try/except (TypeError, ValueError)`. Rejeite `None` explicitamente (hoje
   `float(None)` levanta `TypeError` e derruba a rota com HTTP 500).

Escreva um docstring curto em português em cada uma, explicando **por que**
existem — o próximo leitor precisa entender que data fora do padrão ISO faz o
lançamento sumir das telas mensais. Siga o estilo de `_importancia_valida`
(`app.py:139`) como exemplar.

**Verify**: `./venv/bin/python -m py_compile app.py` → exit 0, sem saída.

**Verify**: 
```bash
./venv/bin/python -c "
import app
assert app._data_iso_valida('2026-12-31') == '2026-12-31'
assert app._data_iso_valida('31/12/2026') is None
assert app._data_iso_valida('2026-13-45') is None
assert app._data_iso_valida(None) is None
assert app._data_iso_valida(20261231) is None
assert app._valor_numerico('1234.56') == 1234.56
assert app._valor_numerico(10) == 10.0
assert app._valor_numerico('1234,56') is None
assert app._valor_numerico('') is None
assert app._valor_numerico(None) is None
print('helpers ok')
"
```
→ imprime `helpers ok` e sai com 0.

### Step 2: Usar os helpers na rota do webhook

Dentro de `api_webhook_receber`, **depois** do bloco que já verifica
`"descricao" not in dados or "valor" not in dados` e **antes** de montar
`dados_lancamento`:

1. Converta o valor com `_valor_numerico(dados["valor"])`. Se vier `None`,
   devolva `400` com uma mensagem em português dizendo que `valor` precisa ser
   numérico e citando o que foi recebido.
2. Resolva o vencimento: se a chave `vencimento` não veio, use
   `date.today().isoformat()` (comportamento atual, mantenha). Se veio,
   valide com `_data_iso_valida`. Se for inválida, devolva `400` com uma
   mensagem em português explicando que o formato esperado é `AAAA-MM-DD`.
3. Em `dados_lancamento`, troque `float(dados["valor"])` pela variável já
   convertida, e `dados.get("vencimento", ...)` pela variável já validada.

As mensagens de erro devem citar o campo problemático pelo nome. Não inclua o
corpo inteiro da requisição na resposta.

**Verify**: `./venv/bin/python -m py_compile app.py` → exit 0.

### Step 3: Provar o comportamento novo contra o app rodando

Rode este script — ele sobe o app num banco descartável usando o driver do
projeto e exercita o webhook:

```bash
./venv/bin/python -u - <<'PY'
import sys, os, importlib.util
RAIZ = os.getcwd()
sys.path.insert(0, RAIZ)
spec = importlib.util.spec_from_file_location(
    "drv", os.path.join(RAIZ, ".claude/skills/run-sistema-financeiro/driver.py"))
drv = importlib.util.module_from_spec(spec); spec.loader.exec_module(drv)
database, _ = drv.preparar_banco(); tid = drv.semear(database)
base = drv.subir_app(drv.porta_livre(5094))

casos = [
    ("data BR e rejeitada",      {"descricao": "x", "valor": 10, "vencimento": "31/12/2026"}, 400),
    ("data impossivel rejeitada",{"descricao": "x", "valor": 10, "vencimento": "2026-13-45"}, 400),
    ("valor com virgula",        {"descricao": "x", "valor": "1234,56"},                      400),
    ("valor texto",              {"descricao": "x", "valor": "abc"},                          400),
    ("valor nulo",               {"descricao": "x", "valor": None},                           400),
    ("data ISO e aceita",        {"descricao": "x", "valor": 10, "vencimento": "2026-12-31"}, 201),
    ("sem vencimento usa hoje",  {"descricao": "y", "valor": 10},                             201),
    ("valor numerico em texto",  {"descricao": "z", "valor": "99.90"},                        201),
]
falhas = []
for nome, payload, esperado in casos:
    status, _ = drv.chamar_webhook(base, payload)
    ok = status == esperado
    print(f"  [{'ok' if ok else 'FALHA'}] {nome}: HTTP {status} (esperado {esperado})")
    if not ok: falhas.append(nome)

# nenhuma linha gravada pode ter vencimento fora do padrao ISO
c = database.conectar()
ruins = c.execute("SELECT COUNT(*) FROM lancamentos WHERE strftime('%Y-%m', vencimento) IS NULL").fetchone()[0]
c.close()
print(f"  [{'ok' if ruins == 0 else 'FALHA'}] nenhum vencimento invisivel no banco ({ruins})")
if ruins: falhas.append("vencimento invisivel")

print("PASSOU" if not falhas else f"FALHOU: {falhas}")
os._exit(1 if falhas else 0)
PY
```

**Verify**: imprime `PASSOU` e sai com 0.

### Step 4: Incorporar as checagens ao smoke permanente

Em `.claude/skills/run-sistema-financeiro/driver.py`, dentro da função
`cmd_smoke`, na seção que já existe com o cabeçalho
`print("\nWebhook de integração", flush=True)`, acrescente checagens usando o
helper `checa(...)` que já está definido ali:

- vencimento em formato brasileiro devolve 400
- valor não numérico devolve 400
- vencimento ISO válido devolve 201

Siga o estilo das checagens vizinhas (nome descritivo em português, comparando
o status devolvido por `chamar_webhook`). Não reescreva as checagens que já
existem.

**Verify**: `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke`
→ exit 0, e a contagem final de checagens é **maior** que 22.

## Test plan

Este projeto **não tem suíte de testes** — a rede de regressão é o
`driver.py smoke`. Portanto:

- As checagens novas do Step 4 **são** os testes desta correção. Elas ficam
  versionadas junto com o código e rodam a cada `driver.py smoke`.
- Casos a cobrir, todos listados no Step 3: data brasileira, data impossível,
  valor com vírgula, valor texto, valor nulo, data ISO válida, ausência de
  vencimento, valor numérico enviado como string.
- Padrão estrutural a seguir: as checagens já existentes em `cmd_smoke` sob o
  cabeçalho "Webhook de integração" — elas mostram a forma
  `checa("descrição", condição, detalhe)`.
- Verificação: `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke`
  → todas passam.

## Done criteria

Todos precisam valer:

- [ ] `./venv/bin/python -m py_compile app.py` sai 0
- [ ] O script do Step 3 imprime `PASSOU` e sai 0
- [ ] `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke` sai 0, com mais de 22 checagens
- [ ] `grep -n 'float(dados\["valor"\])' app.py` não retorna nada
- [ ] `git status --short` mostra apenas `app.py` e
      `.claude/skills/run-sistema-financeiro/driver.py` modificados
- [ ] A linha do plano 001 em `plans/README.md` foi atualizada para DONE

## STOP conditions

Pare e reporte (não improvise) se:

- O código em `app.py` não corresponder ao trecho da seção "Current state" — o
  arquivo mudou desde que este plano foi escrito.
- Uma verificação falhar duas vezes após uma tentativa razoável de correção.
- Você concluir que a correção exige mudar `database.py` — não exige, e mudar o
  filtro de mês afeta todas as telas do sistema.
- Você descobrir que **algum chamador legítimo já envia data fora do padrão
  ISO**. Nesse caso, passar a devolver 400 quebraria a integração em produção,
  e a decisão (rejeitar vs. converter) é do dono do sistema, não sua. Para
  investigar: `grep -rn "vencimento" "../acupuntura_sistema V3/integracao_financeiro.py"`.

## Maintenance notes

- **O que revisar no PR**: que as mensagens de erro não vazem o corpo inteiro da
  requisição, e que o formato de resposta de **sucesso** não mudou — o sistema
  da clínica depende das chaves `sucesso`, `acao` e `id_lancamento`.
- **Interação futura**: se um dia o webhook aceitar outros formatos de data
  (por exemplo ISO com hora), `_data_iso_valida` é o único lugar a mudar. O
  resto da rota já trabalha com a string normalizada.
- **Deliberadamente fora deste plano**: as quatro rotas de formulário têm o
  mesmo problema com `float()` (ver `plans/002-*.md`), e o campo `status`
  recebido pelo webhook também não é validado contra a lista de status
  conhecidos — anotado, mas de impacto muito menor, porque um status
  desconhecido aparece na tela em vez de sumir dela.
