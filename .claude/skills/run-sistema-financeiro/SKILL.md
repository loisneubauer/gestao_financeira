---
name: run-sistema-financeiro
description: Build, run, screenshot and smoke-test the Sistema_Financeiro Flask app (Gestão Financeira). Use when asked to run, start, launch, serve, screenshot, demo, smoke-test or verify this app — or to check that a change works in the real running app rather than in isolation.
---

# Rodar o Sistema_Financeiro

App web Flask + SQLite (contas a pagar/receber, multi-tenant). **Todos os caminhos abaixo são relativos à raiz do projeto** (`Sistema_Financeiro/`).

O app não é dirigível pelo caminho humano: `python app.py` sobe um servidor, espera para sempre e usa o `financeiro.db` **real**. O caminho de agente é o driver:

    .claude/skills/run-sistema-financeiro/driver.py

Ele cria um banco temporário, semeia uma clínica fictícia, sobe o app e o dirige por HTTP. **Nunca toca no `financeiro.db` real.**

## Pré-requisitos

Nenhum pacote de sistema. Só Python 3 (verificado no 3.14.6, macOS arm64) e as três dependências do projeto.

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Num clone limpo não existem `.secret_key` nem `financeiro.db` — os dois são criados sozinhos no primeiro import do app. Não é preciso preparar nada.

## Rodar (caminho do agente)

### Smoke test — a verificação padrão

```bash
./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke
```

Sobe o app, exercita 22 checagens (login com organização, dashboard, CRUD de contas a pagar com nível de importância, recusa de POST sem CSRF, webhook autenticado e idempotente, área de admin, logout) e sai com **0** se tudo passou, **1** se algo falhou. Leva menos de 1 segundo.

Saída real:

```
app no ar em http://127.0.0.1:5099 (banco temporário: /var/folders/.../demo.db)

Autenticação
  [ ok ] tela de login responde
  [ ok ] login com organização + email + senha
  [ ok ] dashboard carrega
  ...
todas as 22 checagens passaram
```

**Este é o único teste automatizado do projeto** — não existe suíte de testes nem CI. Rode depois de qualquer mudança.

### Ver a interface no navegador

```bash
./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py serve
```

Fica no ar até Ctrl-C e imprime as credenciais prontas:

```
  abra:  http://127.0.0.1:5099/login?org=demo
  login: demo@exemplo.com  /  demo1234
  token do webhook: token-demo-do-driver
```

Os dados semeados incluem despesas nos quatro níveis de importância e uma sem classificação, então o card "Importância dos Gastos" aparece preenchido.

Para screenshot, com o `serve` no ar em outro terminal: abra `http://127.0.0.1:5099/login?org=demo`, preencha `input[name='email']` e `input[name='senha']`, clique em `button[type='submit']` e capture. O login **exige o campo organização** — sem o `?org=demo` na URL ele vem vazio e obrigatório.

Ambos os comandos aceitam `--port N` (padrão 5099; se a porta estiver ocupada, o driver escolhe outra sozinho).

## Rodar (caminho humano)

```bash
./venv/bin/python app.py      # porta 5002, debug=True, banco REAL
```

Use só para mexer nos dados de verdade. Para desenvolver e verificar mudanças, prefira o `serve` do driver — os dados são descartáveis.

## Armadilhas

- **Trocar o banco tem que vir ANTES de importar o app.** `app.py` chama `database.criar_tabelas()` já no import. Quem fizer `import app` e só depois apontar `database.NOME_DO_BANCO` para outro arquivo já criou e migrou o `financeiro.db` real. O driver faz na ordem certa (`preparar_banco()` antes de `subir_app()`).

- **O token CSRF não está no HTML como campo pronto.** O `base.html` injeta ele por JavaScript em cada `<form method="post">` no carregamento (`campo.value = "{{ csrf_token() }}"`). Um cliente HTTP não executa esse script — é preciso ler o valor do próprio JS. O driver cobre os dois formatos em `Cliente.csrf()`. Sem o token, todo POST autenticado devolve **400**.

- **`/logout` é GET.** A rota não declara `methods`, então POST devolve 405 e a sessão continua de pé — dá a falsa impressão de que o logout não funciona.

- **O login exige a organização (slug).** Desde 24/08/2026 não basta email e senha: falta o campo `organizacao`. Ele aceita o nome por extenso ("Clínica Demo" vira `clinica-demo`), e pode vir pela URL como `?org=slug`.

- **Cinco senhas erradas travam o login por 15 minutos**, por (IP + organização + email). Um driver que fique tentando senhas erradas se auto-bloqueia — e o bloqueio vale mesmo depois com a senha certa. O contador vive em memória: reiniciar o app zera.

- **`criar_usuarios.py` cria a organização com slug `padrao`, mas o app renomeia esse slug na inicialização seguinte.** A migração troca `padrao` pelo nome da organização (`Acupuntura Bem-estar` → `acupuntura-bem-estar`). Quem seguir o script e tentar entrar com "padrao" recebe "Organização, email ou senha incorretos". Verificado:

  ```
  logo apos criar_usuarios.py -> slug: padrao
  depois de iniciar o app     -> slug: acupuntura-bem-estar
  ```

  Para descobrir o slug de verdade: `sqlite3 financeiro.db "SELECT nome, slug FROM tenants;"`

- **`criar_usuarios.py` é interativo** (`getpass`), então não serve para script. Para semear sem interação, use `database.criar_tenant()` + `database.criar_usuario()` direto, como o driver faz em `semear()`.

- **Não importe este projeto e o `acupuntura_sistema V3` no mesmo processo Python.** Os dois têm `app.py`, `database.py` e `calculos.py` na raiz; o `sys.path` resolve para o primeiro e o segundo import devolve o módulo errado, com erro do tipo `module 'calculos' has no attribute 'calcular_painel_financeiro'`.

- **`app.run()` numa thread precisa de `use_reloader=False`.** Com o reloader ligado, o Flask tenta reiniciar o processo a partir de uma thread secundária e falha.

- **Prints somem se o processo terminar com `os._exit()`** — o buffer do stdout não é descarregado. Use `flush=True` ou `sys.exit()`.

## Solução de problemas

| Sintoma | Causa | O que fazer |
|---|---|---|
| `RuntimeError: não achei o csrf_token` | o `base.html` mudou a forma de injetar o token | ajuste os padrões em `Cliente.csrf()` no driver |
| POST devolve 400 "token CSRF inválido" | POST autenticado sem o campo `csrf_token` | pegue o token com `c.csrf("/pagar")` antes |
| Login devolve 200 com "Organização, email ou senha incorretos" | falta o campo `organizacao`, ou o slug está errado | confira o slug no banco (veja a armadilha acima) |
| Login devolve "Muitas tentativas" | rate limit disparado | espere 15 min ou reinicie o app (o contador é em memória) |
| `Address already in use` | porta ocupada | use `--port N`, ou deixe o driver escolher sozinho |
| `module 'calculos' has no attribute ...` | colisão de `sys.path` com o projeto da acupuntura | rode num processo separado, a partir da raiz deste projeto |

## Testes

Não existe suíte de testes nem CI neste projeto. O `driver.py smoke` faz esse papel.
