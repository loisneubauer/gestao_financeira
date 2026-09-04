# Plan 002: Fazer valor inválido devolver aviso na tela em vez de erro 500

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

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: `plans/001-validar-entrada-do-webhook.md` — reaproveita o
  helper `_valor_numerico` criado lá. Se o 001 ainda não foi executado, veja
  a nota no Step 1.
- **Category**: bug
- **Planned at**: commit `b7ffa07`, 2026-08-25

## Why this matters

As quatro rotas que gravam lançamento fazem `float(request.form.get("valor", 0))`
sem nenhuma proteção. Qualquer coisa que não seja número derruba a requisição
com `ValueError` e a usuária vê a página de erro do Flask — perdendo tudo o que
tinha digitado no formulário.

Foi reproduzido: enviar `valor=abc` e `valor=` (vazio) devolve **HTTP 500** com
`ValueError: could not convert string to float`.

Sendo honesto sobre a gravidade: no navegador o campo é
`<input type="number" step="0.01" required>`, então o browser já barra a maior
parte dessas entradas antes de enviar. **No uso normal isso quase não acontece.**
O problema é que validação no cliente não é garantia no servidor — basta um
navegador antigo, uma extensão, um formulário reenviado, ou uma automação. E a
falha é desproporcional: erro 500 e formulário perdido, em vez de "digite um
valor válido".

Depois deste plano, valor inválido devolve a tela de volta com um aviso, e o
sistema nunca responde 500 por causa disso.

## Current state

Arquivo: `app.py` — a aplicação Flask inteira.

As quatro ocorrências, todas idênticas:

| Linha | Função | Rota |
|---|---|---|
| 468 | `novo_pagar` | `POST /pagar/novo` |
| 508 | `editar_pagar` | `POST /pagar/<id>/editar` |
| 624 | `novo_receber` | `POST /receber/novo` |
| 669 | `editar_receber` | `POST /receber/<id>/editar` |

O bloco em `novo_pagar` (`app.py:459-476`), representativo das quatro:

```python
@app.route("/pagar/novo", methods=["POST"])
@login_required
def novo_pagar():
    freq = request.form.get("frequencia_recorrencia")
    if not freq:
        freq = "Mensal" if request.form.get("recorrente") else "Nenhuma"

    dados = {
        "descricao": request.form.get("descricao", "").strip(),
        "tipo": "Pagar",
        "esfera": request.form.get("esfera", "Empresa"),
        "categoria_id": request.form.get("categoria_id") or None,
        "valor": float(request.form.get("valor", 0)),
        "vencimento": request.form.get("vencimento", date.today().isoformat()),
        "status": request.form.get("status", "Pendente"),
        "forma_pagamento": request.form.get("forma_pagamento", "Pix"),
        "recorrente": 1 if freq != "Nenhuma" else 0,
        "frequencia_recorrencia": freq,
        "observacoes": request.form.get("observacoes", ""),
        "importancia": _importancia_valida(request.form.get("importancia"))
```

As quatro rotas terminam com `return redirect(url_for("listar_pagar"))` ou
`return redirect(url_for("listar_receber"))`.

Convenções deste repositório que o plano deve respeitar:

- **Como as telas comunicam erro**: por querystring no redirect. Já existe no
  código, em `gerar_recorrencias` (`app.py`):
  `return redirect(url_for("pagina_inicial", mes=mes_destino, aviso=f"{qtd} despesas recorrentes geradas!"))`
  As telas de listagem leem `request.args`. **Use esse mesmo mecanismo** — não
  invente flash messages, o projeto não usa `flask.flash`.
- **Comentários e mensagens em português.**
- **Sem dependências novas** — o `requirements.txt` tem três pacotes.
- **Helpers privados com prefixo `_`**, definidos acima de quem os usa. Exemplar:
  `_importancia_valida` (`app.py:139`), que valida um campo de formulário e
  devolve `None` quando o valor não serve.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Instalar deps | `./venv/bin/pip install -r requirements.txt` | exit 0 |
| Compilar | `./venv/bin/python -m py_compile app.py` | exit 0, sem saída |
| Smoke test | `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke` | exit 0 |

Não existe suíte de testes nem linter. O `driver.py smoke` é a única
verificação automatizada — sobe o app num banco temporário e **nunca toca no
`financeiro.db` real**.

## Scope

**In scope**:
- `app.py` — apenas as quatro funções listadas na tabela acima, mais o uso do
  helper de conversão.
- `templates/pagar.html` e `templates/receber.html` — apenas para exibir o
  aviso de erro (Step 3).
- `.claude/skills/run-sistema-financeiro/driver.py` — apenas checagens novas em
  `cmd_smoke`.

**Out of scope**:
- A rota `api_webhook_receber` — é o assunto do `plans/001-*.md`. Se o 001 já
  foi executado, ela já está tratada; não mexa nela aqui.
- `database.py` — a conversão é responsabilidade da camada de rota; a camada de
  dados recebe o valor já convertido.
- Trocar o `<input type="number">` por `type="text"` com máscara de moeda —
  seria uma mudança de produto (aceitar vírgula como separador decimal), não
  uma correção de bug. Se parecer necessário, é STOP condition.
- As demais conversões do arquivo (`categoria_id`, datas) — fora do escopo
  deste plano.

## Git workflow

- Branch: `advisor/002-valor-invalido-nao-derruba-a-tela`
- Um commit ao final, ou um por passo. Mensagem em português, no imperativo.
  Exemplo real de `git log`:
  `Corrige atraso zerado e valor contado duas vezes na sincronizacao`
- **Não** faça push nem abra PR a menos que o operador peça.

## Steps

### Step 1: Garantir que o helper de conversão existe

Este plano usa `_valor_numerico(valor)` — uma função que devolve `float` quando
a entrada é convertível e `None` caso contrário.

- **Se o `plans/001-*.md` já foi executado**, a função já existe em `app.py`.
  Confirme com `grep -n "_valor_numerico" app.py` e siga para o Step 2.
- **Se ainda não foi executado**, crie a função agora, logo abaixo de
  `_importancia_valida` (`app.py:139`): envolva `float(valor)` em
  `try/except (TypeError, ValueError)` e devolva `None` na falha. Docstring
  curto em português.

**Verify**:
```bash
./venv/bin/python -c "
import app
assert app._valor_numerico('10.50') == 10.5
assert app._valor_numerico('abc') is None
assert app._valor_numerico('') is None
assert app._valor_numerico(None) is None
print('helper ok')
"
```
→ imprime `helper ok`.

### Step 2: Tratar valor inválido nas quatro rotas

Em cada uma das quatro funções (`novo_pagar`, `editar_pagar`, `novo_receber`,
`editar_receber`), **antes** de montar o dicionário `dados`:

1. Converta com `_valor_numerico(request.form.get("valor"))`.
2. Se vier `None`, **não grave nada**: retorne um redirect para a listagem
   correspondente com um parâmetro de erro na querystring, no mesmo estilo do
   `aviso` já usado em `gerar_recorrencias`. A mensagem deve ser em português e
   dizer o que fazer, por exemplo que o valor precisa ser um número usando
   ponto como separador decimal.
   - `novo_pagar` e `editar_pagar` → `url_for("listar_pagar", erro=...)`
   - `novo_receber` e `editar_receber` → `url_for("listar_receber", erro=...)`
3. Use a variável já convertida no dicionário `dados`, no lugar do
   `float(request.form.get("valor", 0))`.

Um comentário curto em português explicando por que a guarda existe (validação
do navegador não é garantia no servidor) ajuda o próximo leitor.

**Verify**: `./venv/bin/python -m py_compile app.py` → exit 0.

**Verify**: `grep -c 'float(request.form.get("valor", 0))' app.py` → `0`.

### Step 3: Mostrar o aviso nas telas

As telas hoje não exibem `erro` vindo da querystring. Em `templates/pagar.html`
e `templates/receber.html`, adicione um bloco que renderiza o alerta quando o
parâmetro existir. O projeto usa Bootstrap 5; siga o padrão de alerta já
presente em `templates/login.html`:

```html
{% if erro %}
    <div class="alert alert-danger text-start">{{ erro }}</div>
{% endif %}
```

Para a variável chegar ao template, passe `erro=request.args.get("erro")` no
`render_template` das rotas `listar_pagar` e `listar_receber` — ou use
`request.args.get("erro")` direto no template, já que `request` está disponível
no contexto do Jinja2 do Flask. Escolha uma das duas formas e **use a mesma nas
duas telas**.

Coloque o alerta perto do topo do conteúdo, onde a usuária vê sem rolar.

**Verify**: `./venv/bin/python -m py_compile app.py` → exit 0 (garante que o
`render_template` não ficou com erro de sintaxe).

### Step 4: Provar o comportamento novo contra o app rodando

```bash
./venv/bin/python -u - <<'PY'
import sys, os, importlib.util
from datetime import date
RAIZ = os.getcwd()
sys.path.insert(0, RAIZ)
spec = importlib.util.spec_from_file_location(
    "drv", os.path.join(RAIZ, ".claude/skills/run-sistema-financeiro/driver.py"))
drv = importlib.util.module_from_spec(spec); spec.loader.exec_module(drv)
database, _ = drv.preparar_banco(); tid = drv.semear(database)
base = drv.subir_app(drv.porta_livre(5093))
c = drv.Cliente(base)
c.post("/login", {"organizacao": drv.ORG_SLUG, "email": drv.EMAIL, "senha": drv.SENHA})

antes = len(database.listar_lancamentos(tid))
falhas = []
for rota, valor in [("/pagar/novo", "abc"), ("/pagar/novo", ""), ("/pagar/novo", "1234,56"),
                    ("/receber/novo", "abc"), ("/receber/novo", "")]:
    tok = c.csrf("/pagar")
    status, _ = c.post(rota, {
        "csrf_token": tok, "descricao": "teste valor invalido", "esfera": "Casa",
        "valor": valor, "vencimento": date.today().isoformat(), "status": "Pendente",
        "forma_pagamento": "Pix", "frequencia_recorrencia": "Nenhuma",
        "importancia": "", "observacoes": "",
    })
    ok = status != 500
    print(f"  [{'ok' if ok else 'FALHA'}] {rota} com valor={valor!r}: HTTP {status} (nao pode ser 500)")
    if not ok: falhas.append(f"{rota} {valor!r}")

depois = len(database.listar_lancamentos(tid))
print(f"  [{'ok' if depois == antes else 'FALHA'}] nada foi gravado ({antes} -> {depois})")
if depois != antes: falhas.append("gravou lancamento invalido")

# o caminho feliz continua funcionando
tok = c.csrf("/pagar")
status, _ = c.post("/pagar/novo", {
    "csrf_token": tok, "descricao": "valor valido", "esfera": "Casa", "valor": "123.45",
    "vencimento": date.today().isoformat(), "status": "Pendente", "forma_pagamento": "Pix",
    "frequencia_recorrencia": "Nenhuma", "importancia": "Necessário", "observacoes": "",
})
gravou = any(l["descricao"] == "valor valido" for l in database.listar_lancamentos(tid))
print(f"  [{'ok' if gravou else 'FALHA'}] valor valido continua gravando (HTTP {status})")
if not gravou: falhas.append("regressao no caminho feliz")

print("PASSOU" if not falhas else f"FALHOU: {falhas}")
os._exit(1 if falhas else 0)
PY
```

**Verify**: imprime `PASSOU` e sai com 0.

### Step 5: Incorporar as checagens ao smoke permanente

Em `.claude/skills/run-sistema-financeiro/driver.py`, dentro de `cmd_smoke`, na
seção `print("\nContas a pagar", flush=True)`, acrescente com o helper `checa`:

- valor não numérico **não** devolve 500
- valor não numérico não grava lançamento
- valor válido continua gravando (proteção contra regressão)

Siga o estilo das checagens vizinhas.

**Verify**: `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke`
→ exit 0, contagem final maior que a anterior.

## Test plan

Este projeto **não tem suíte de testes** — a rede de regressão é o
`driver.py smoke`. As checagens do Step 5 são os testes desta correção.

- Casos cobertos: valor texto, valor vazio, valor com vírgula decimal, nas
  rotas de pagar e de receber; e o caminho feliz, para provar que não houve
  regressão.
- Padrão estrutural: as checagens já existentes em `cmd_smoke` sob o cabeçalho
  "Contas a pagar".
- Verificação: `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke`
  → todas passam.

## Done criteria

Todos precisam valer:

- [ ] `./venv/bin/python -m py_compile app.py` sai 0
- [ ] `grep -c 'float(request.form.get("valor", 0))' app.py` retorna `0`
- [ ] O script do Step 4 imprime `PASSOU` e sai 0
- [ ] `./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke` sai 0
- [ ] `git status --short` mostra apenas `app.py`, `templates/pagar.html`,
      `templates/receber.html` e `.claude/skills/run-sistema-financeiro/driver.py`
- [ ] A linha do plano 002 em `plans/README.md` foi atualizada para DONE

## STOP conditions

Pare e reporte (não improvise) se:

- O código em `app.py` não corresponder aos trechos da seção "Current state".
- Uma verificação falhar duas vezes após uma tentativa razoável de correção.
- Você concluir que a correção exige mudar o tipo do campo no formulário
  (`type="number"` → `type="text"` com máscara). Aceitar vírgula como separador
  decimal é uma decisão de produto, não uma correção de bug — reporte e pare.
- As quatro rotas **não** forem idênticas no trecho relevante. O plano assume
  que são; se uma divergir, reporte antes de aplicar a mesma mudança nela.

## Maintenance notes

- **O que revisar no PR**: que nenhuma das quatro rotas grave lançamento quando
  o valor é inválido (o redirect precisa vir **antes** de qualquer chamada a
  `database.inserir_lancamento` / `atualizar_lancamento`), e que o caminho feliz
  não regrediu.
- **Interação futura**: se um dia o projeto aceitar valores no formato
  brasileiro (`1.234,56`), o lugar de normalizar é `_valor_numerico` — as quatro
  rotas não precisam mudar de novo.
- **Deliberadamente fora deste plano**: o campo `vencimento` das rotas de
  formulário também não é validado, mas ali o `<input type="date">` do navegador
  já entrega o formato ISO, e uma data inválida não derruba a rota — ela grava
  e some das telas, que é o mesmo sintoma tratado no plano 001, só que por outra
  porta. Vale um plano próprio se o problema aparecer na prática.
