Este projeto é o **Sistema Financeiro** (Python/Flask + SQLite): controle de contas a pagar e a receber, multi-tenant. Esta pasta (`~/Documents/Sistema_Financeiro`) é a pasta de trabalho oficial — é aqui que devo consultar e atualizar os arquivos.

Repositório: `github.com/loisneubauer/gestao_financeira`
Em produção: https://sistemafinanceiro.pythonanywhere.com

Existe uma cópia velha em `~/Desktop/gestao_financeira`, parada em 03/08/2026 e cerca de 20 commits atrás. **Não é a pasta boa** — se ela ainda existir, ignorar.

## Contexto: está em uso real desde 25/08/2026

O sistema atende mais de uma organização. A do Lois e da Laila é **`tenant_id = 1`, "Laila Acupuntura", slug `laila-acupuntura`**, e o link de entrada delas é
`https://sistemafinanceiro.pythonanywhere.com/login?org=laila-acupuntura` (por ele o campo da organização já vem preenchido).

Há outras organizações no mesmo banco (id 7 "L2 Tecnologia em IA", id 8 "Joge Gourmet"). **Nunca supor que a da clínica é a única.** Em 04/09/2026 a clínica passou dias sincronizando na organização errada porque o token no WSGI dela era de outro tenant — e tudo respondia "sucesso".

Desde 25/08/2026 tem dado de verdade dentro: data de início, saldos iniciais de Empresa e Casa, e os gastos do mês classificados por nível de importância. A partir de setembro a Laila alimenta o sistema no dia a dia.

A documentação de arquitetura, schema e rotas está em `Documentacao_Financeiro.md`, na raiz.

## Regras de segurança

- **`financeiro.db` desta pasta NÃO é produção** — é uma cópia local de desenvolvimento, de 11/08/2026. Os dados reais vivem só no PythonAnywhere. Nunca supor que o que está aqui reflete o que a cliente vê, e nunca gerar relatório ou diagnóstico sobre "o estado atual" a partir dele.
- `.env`, `.secret_key` e `financeiro.db` são ignorados pelo git — nunca expor nem commitar.
- O `.env` guarda a conta Gmail que envia os convites de organização (`GMAIL_USER`, `GMAIL_APP_PASSWORD`). Senha de app, não a senha da conta.
- O webhook que recebe os dados da clínica exige o cabeçalho `X-Api-Token`. O token de cada organização fica no banco; do lado da clínica ele vem da variável `TOKEN_GESTAO_FINANCEIRA`.
- **Nunca importar este projeto e o `acupuntura_sistema V3` no mesmo processo Python.** Os dois têm `app.py`, `database.py` e `calculos.py` na raiz; o `sys.path` resolve para o primeiro e o segundo import devolve o módulo errado.

## Como trabalhar aqui

Sempre que o Lois pedir algo, devo analisar o cenário primeiro e descrever o que encontrei ou entendi, para que ele possa validar antes de eu dar andamento — não sair implementando direto sem esse passo.

**Sempre testar antes de commit/push**, nunca contra o banco real:

```bash
./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke
```

Sobe o app num banco temporário, roda **112 checagens** e sai com 0 ou 1. **É a única verificação automatizada do projeto** — não existe suíte de testes nem CI. Rodar depois de qualquer mudança, e acrescentar checagens novas junto com correções, para que fiquem protegidas contra regressão.

Para olhar a interface: `driver.py serve` sobe com dados de demonstração descartáveis e imprime as credenciais. Detalhes e armadilhas na skill `run-sistema-financeiro`, em `.claude/skills/`.

**Autorização permanente para commitar e dar push sozinho, sem perguntar antes** (pedido explícito do Lois, 04/09/2026), desde que **todas** estas condições valham:

- o `driver.py smoke` passou inteiro;
- quando a mudança mexe em tela, o teste visual foi feito de verdade (servidor local numa porta separada com banco descartável + Playwright, navegando e conferindo);
- nenhum erro apareceu no caminho;
- **e não há ponto de atenção ou algo que dependa de uma decisão dele** — dado que só ele sabe, ambiguidade de regra de negócio, escolha de design ainda aberta.

Havendo qualquer coisa nessas condições, voltar a pedir autorização explícita antes de commitar, descrevendo o ponto em aberto.

O push é meu; o **deploy continua sendo dele** — sempre nomear os passos abaixo depois de cada push.

## Deploy

Não tenho acesso ao servidor. O deploy é feito pelo Lois, em três passos, e devo **sempre nomear os três explicitamente**:

1. `git push` aqui no Mac
2. `git pull` no console do PythonAnywhere
3. **Reload** na aba Web

## Decisões de negócio já tomadas (não revisitar por conta própria)

- **O login exige a organização**, além de email e senha. Decisão de previsibilidade. Aceita o nome por extenso ("Clínica Demo" vira `clinica-demo`) e aceita `?org=slug` na URL.
- **Regime de caixa.** A data que define o mês do dinheiro é `data_pagamento`, com `vencimento` como fallback. O saldo atravessa o mês e o caixa é separado por esfera (Empresa e Casa são contas diferentes). Existe um seam em `database._BASES_DE_DATA` deixado de propósito para, no futuro, oferecer também a visão por competência.
- **Data de início por organização.** A da Laila é 01/08/2026: nada anterior entra na conta, e meses anteriores nem aparecem na navegação.
- **Nível de importância do gasto** em 4 níveis (Imprescindível → Necessário → Supérfluo → Impulso). "Evitável" = os dois últimos, calculado só sobre o que já foi classificado. A tabela é **editável, uma por organização**, só para administrador, com a da Laila como padrão de fábrica.
- **A integração com a clínica é opcional por organização** (campo `integracao_ativa`). Desligada, o lançamento é manual, como no módulo da Casa.
- **Fechamento de mês é automático**, de carona na primeira visita ao dashboard depois da virada — sem botão manual. Não há agendador utilizável no PythonAnywhere.
- Linhas vindas da integração são consolidadas e só-leitura. Lançamentos manuais **nunca** — mesmo em atraso, continuam editáveis.
- **A tela nunca mostra número da integração sem dizer de quando ele é.** Contas a Receber exibe a data da última sincronização; passados 3 dias vira alerta; organização com histórico e integração desligada vira alerta vermelho. Isso existe porque números velhos com cara de novos custaram uma semana de diagnóstico.

## O que está pendente

Os planos ficam em `plans/`, com o status de cada um em `plans/README.md`.

- **Plano 004 — fechamento automático do mês**: escrito, `TODO`, não executado. Falta decidir se o número congela no fechamento ou continua recalculando enquanto o mês ainda está sendo conferido.
- **Renomear o rótulo "Fecha o mês com"** no dashboard — nome ruim, colide com "fechamento de mês" e já confundiu o Lois. Sugestão em aberto: "Saldo hoje".
- **Três achados de auditoria levantados e não corrigidos**, com evidência em `plans/README.md`: excluir categoria em uso derruba a tela (HTTP 500 por FOREIGN KEY); `_tentativas_login` cresce sem limite; upload de foto sem `MAX_CONTENT_LENGTH`.
- Próximo na fila de funcionalidade: **fluxo de caixa e projeção** a partir das recorrências.
