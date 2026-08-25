#!/usr/bin/env python3
"""
driver.py — sobe o Sistema_Financeiro num banco DESCARTÁVEL e o dirige por HTTP.

Existe porque `python app.py` não é dirigível: abre um servidor, espera para
sempre e usa o `financeiro.db` real. Aqui o banco é sempre um arquivo temporário
semeado com dados fictícios, então rodar isto nunca toca nos dados de verdade.

    ./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py smoke
    ./venv/bin/python .claude/skills/run-sistema-financeiro/driver.py serve

Só usa a biblioteca padrão e as dependências que o próprio app já tem.
"""
import argparse
import http.cookiejar
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

# O driver mora em <unit>/.claude/skills/run-sistema-financeiro/ — a raiz do
# projeto é três níveis acima.
AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(AQUI)))
sys.path.insert(0, RAIZ)

ORG_SLUG = "demo"
ORG_NOME = "Clínica Demo"
EMAIL = "demo@exemplo.com"
SENHA = "demo1234"
API_TOKEN = "token-demo-do-driver"


# ─────────────────────────── banco descartável ───────────────────────────

def preparar_banco():
    """Cria um banco temporário e o registra ANTES de o app ser importado.

    A ordem é obrigatória: app.py chama database.criar_tabelas() já no import,
    então importar o app antes de trocar NOME_DO_BANCO cria/migra o
    financeiro.db real.
    """
    import database

    pasta = tempfile.mkdtemp(prefix="financeiro-driver-")
    database.NOME_DO_BANCO = os.path.join(pasta, "demo.db")
    database.criar_tabelas()
    return database, database.NOME_DO_BANCO


def semear(database):
    """Popula o banco com uma clínica fictícia: organização, usuária, categorias,
    despesas classificadas por importância e receitas."""
    from werkzeug.security import generate_password_hash

    tid = database.criar_tenant(ORG_NOME, ORG_SLUG, API_TOKEN)
    database.criar_usuario(
        tid, "Lois", EMAIL, generate_password_hash(SENHA), saudacao="Sra.", is_admin=1
    )

    for nome, esfera in [("Moradia", "Casa"), ("Alimentação", "Casa"),
                         ("Insumos", "Empresa"), ("Lazer", "Casa")]:
        database.inserir_categoria(tid, nome, "Pagar", esfera)
    categorias = {c["nome"]: c["id"] for c in database.listar_categorias(tid)}

    def venc(dia):
        return date.today().replace(day=min(dia, 28)).isoformat()

    despesas = [
        ("Aluguel do consultório", 2200.00, 1, "Moradia", "Empresa", 5, "Pago"),
        ("Energia elétrica", 340.50, 1, "Moradia", "Casa", 10, "Pago"),
        ("Agulhas e insumos", 620.00, 2, "Insumos", "Empresa", 12, "Pago"),
        ("Streaming (3 serviços)", 110.00, 3, "Lazer", "Casa", 7, "Pago"),
        ("Bolsa que vi na vitrine", 520.00, 4, "Lazer", "Casa", 14, "Pago"),
        ("Manutenção do ar", 260.00, None, "Moradia", "Empresa", 25, "Pendente"),
    ]
    for descricao, valor, nivel, categoria, esfera, dia, status in despesas:
        database.inserir_lancamento(tid, {
            "descricao": descricao, "tipo": "Pagar", "esfera": esfera,
            "categoria_id": categorias.get(categoria), "valor": valor,
            "vencimento": venc(dia), "status": status,
            "data_pagamento": venc(dia) if status == "Pago" else None,
            "forma_pagamento": "Pix", "frequencia_recorrencia": "Nenhuma",
            "observacoes": "", "importancia_nivel": nivel,
        })

    for descricao, valor, dia, status in [
        ("Atendimentos da semana 1", 3200.00, 7, "Recebido"),
        ("Atendimentos da semana 2", 2850.00, 14, "Pendente"),
    ]:
        database.inserir_lancamento(tid, {
            "descricao": descricao, "tipo": "Receber", "esfera": "Empresa",
            "categoria_id": None, "valor": valor, "vencimento": venc(dia),
            "status": status, "data_pagamento": venc(dia) if status == "Recebido" else None,
            "forma_pagamento": "Pix", "frequencia_recorrencia": "Nenhuma", "observacoes": "",
        })

    return tid


def porta_livre(preferida):
    """Devolve a porta preferida se estiver livre, senão uma qualquer do SO."""
    for tentativa in (preferida, 0):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", tentativa))
            porta = s.getsockname()[1]
            s.close()
            return porta
        except OSError:
            s.close()
    raise RuntimeError("sem porta livre")


def subir_app(porta, em_thread=True):
    """Importa o app (já com o banco temporário registrado) e o coloca no ar."""
    import app as modulo_app

    if not em_thread:
        modulo_app.app.run(port=porta, debug=False, use_reloader=False)
        return None

    t = threading.Thread(
        target=lambda: modulo_app.app.run(port=porta, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()

    base = f"http://127.0.0.1:{porta}"
    for _ in range(80):
        try:
            urllib.request.urlopen(base + "/login", timeout=1)
            return base
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"o app não respondeu em {base} depois de 20s")


# ─────────────────────────── cliente HTTP ───────────────────────────

class Cliente:
    """Sessão HTTP com cookies, que sabe achar o token CSRF das telas."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def get(self, caminho):
        req = urllib.request.Request(self.base + caminho)
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def post(self, caminho, campos, seguir=False):
        """POST de formulário. Devolve (status, corpo). Sem seguir redirect por
        padrão: o 302 é justamente o sinal de que a ação deu certo."""
        dados = urllib.parse.urlencode(campos).encode()
        req = urllib.request.Request(self.base + caminho, data=dados, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        classe = self.opener if seguir else urllib.request.build_opener(
            _SemRedirect, urllib.request.HTTPCookieProcessor(self.opener.handlers[-1].cookiejar
                                                             if hasattr(self.opener.handlers[-1], "cookiejar")
                                                             else http.cookiejar.CookieJar())
        )
        # Reaproveita o mesmo cookiejar da sessão
        if not seguir:
            classe = urllib.request.build_opener(_SemRedirect)
            for h in self.opener.handlers:
                if isinstance(h, urllib.request.HTTPCookieProcessor):
                    classe.add_handler(h)
        try:
            with classe.open(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def csrf(self, caminho="/pagar"):
        """Lê o token CSRF de uma tela. Todo POST autenticado precisa dele —
        só /login, /esqueci-senha e /redefinir-senha são isentos.

        O token NÃO vem num <input hidden> pronto: o base.html injeta ele por
        JavaScript em cada <form method="post"> no carregamento da página
        (`campo.value = "{{ csrf_token() }}"`). Um cliente HTTP não executa
        esse script, então o jeito de obtê-lo é ler o valor direto do JS."""
        _, html = self.get(caminho)
        for padrao in (r'campo\.value\s*=\s*"([0-9a-f]{32,})"',      # injeção via JS (base.html)
                       r'name="csrf_token"\s+value="([^"]+)"'):       # formulários que já trazem o campo
            m = re.search(padrao, html)
            if m:
                return m.group(1)
        raise RuntimeError(
            f"não achei o csrf_token em {caminho} — o base.html mudou a forma de injetá-lo?"
        )


class _SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def chamar_webhook(base, payload, token=API_TOKEN):
    """POST no webhook de integração. token=None simula chamador sem credencial."""
    req = urllib.request.Request(
        base + "/api/v1/receber/webhook",
        data=json.dumps(payload).encode(), method="POST",
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Api-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


# ─────────────────────────── comandos ───────────────────────────

def cmd_serve(args):
    database, caminho_db = preparar_banco()
    semear(database)
    porta = porta_livre(args.port)

    print(f"banco descartável: {caminho_db}")
    print(f"o financeiro.db real NÃO é usado nem alterado")
    print()
    print(f"  abra:  http://127.0.0.1:{porta}/login?org={ORG_SLUG}")
    print(f"  login: {EMAIL}  /  {SENHA}")
    print(f"  token do webhook: {API_TOKEN}")
    print()
    print("Ctrl-C para parar.")
    subir_app(porta, em_thread=False)


def cmd_smoke(args):
    resultados = []

    def checa(nome, condicao, detalhe=""):
        resultados.append((nome, bool(condicao)))
        marca = " ok " if condicao else "FALHA"
        linha = f"  [{marca}] {nome}"
        if detalhe and not condicao:
            linha += f"  — {detalhe}"
        print(linha, flush=True)

    database, caminho_db = preparar_banco()
    tid = semear(database)
    porta = porta_livre(args.port)
    base = subir_app(porta)
    print(f"app no ar em {base} (banco temporário: {caminho_db})\n", flush=True)

    c = Cliente(base)

    print("Autenticação", flush=True)
    status, _ = c.get("/login")
    checa("tela de login responde", status == 200, f"status {status}")

    status, corpo = c.post("/login", {"organizacao": ORG_SLUG, "email": EMAIL, "senha": SENHA})
    checa("login com organização + email + senha", status == 302, f"status {status}")

    status, html = c.get("/")
    checa("dashboard carrega", status == 200, f"status {status}")
    checa("dashboard traz o card de importância", "Importância dos Gastos" in html)
    checa("dashboard calcula quanto dava para cortar", "cortar" in html)

    print("\nContas a pagar", flush=True)
    status, html = c.get("/pagar")
    checa("listagem carrega", status == 200, f"status {status}")
    checa("mostra despesa semeada", "Aluguel do consultório" in html)
    checa("mostra o nível de importância", "Indispensável" in html)

    token = c.csrf("/pagar")
    status, _ = c.post("/pagar/novo", {
        "csrf_token": token, "descricao": "Despesa criada pelo driver",
        "esfera": "Casa", "valor": "99.90",
        "vencimento": date.today().isoformat(), "status": "Pendente",
        "forma_pagamento": "Pix", "frequencia_recorrencia": "Nenhuma",
        "importancia_nivel": "4", "observacoes": "",
    })
    checa("cria uma despesa nova", status == 302, f"status {status}")

    _, html = c.get("/pagar")
    checa("a despesa nova aparece na lista", "Despesa criada pelo driver" in html)
    checa("com a importância escolhida", "Evitável" in html)

    checa("POST sem CSRF é recusado",
          c.post("/pagar/novo", {"descricao": "sem token", "valor": "1",
                                 "vencimento": date.today().isoformat()})[0] == 400)

    # Valor inválido não pode derrubar a rota com HTTP 500 nem gravar nada —
    # o navegador restringe o campo (type="number"), mas o servidor não pode
    # depender só disso.
    qtd_antes_valor_invalido = len(database.listar_lancamentos(tid))
    token = c.csrf("/pagar")
    status, _ = c.post("/pagar/novo", {
        "csrf_token": token, "descricao": "valor invalido do smoke",
        "esfera": "Casa", "valor": "abc",
        "vencimento": date.today().isoformat(), "status": "Pendente",
        "forma_pagamento": "Pix", "frequencia_recorrencia": "Nenhuma",
        "importancia_nivel": "", "observacoes": "",
    })
    checa("valor não numérico não derruba a rota (sem 500)", status != 500, f"status {status}")
    checa("valor não numérico não grava lançamento",
          len(database.listar_lancamentos(tid)) == qtd_antes_valor_invalido)

    token = c.csrf("/pagar")
    status, _ = c.post("/pagar/novo", {
        "csrf_token": token, "descricao": "valor valido do smoke",
        "esfera": "Casa", "valor": "77.50",
        "vencimento": date.today().isoformat(), "status": "Pendente",
        "forma_pagamento": "Pix", "frequencia_recorrencia": "Nenhuma",
        "importancia_nivel": "", "observacoes": "",
    })
    _, html = c.get("/pagar")
    checa("valor válido continua gravando (sem regressão)",
          status == 302 and "valor valido do smoke" in html, f"status {status}")

    print("\nFiltro por nível (clique no card do dashboard)", flush=True)
    _, dash = c.get("/")
    import re as _re
    linkados = set(_re.findall(r"/pagar\?mes=[\d-]+&(?:amp;)?nivel=(\w+)", dash))
    checa("dashboard gera link para cada nível", linkados >= {"1", "2", "3", "4"}, f"{linkados}")
    checa("e para os não classificados", "sem" in linkados, f"{linkados}")

    _, p1 = c.get("/pagar?nivel=1")
    checa("filtro traz só o nível pedido",
          "Aluguel do consultório" in p1 and "Bolsa que vi na vitrine" not in p1)
    checa("mostra qual filtro está ativo", "Indispensável" in p1)

    _, psem = c.get("/pagar?nivel=sem")
    checa("filtro 'sem classificação' funciona",
          "Manutenção do ar" in psem and "Aluguel do consultório" not in psem)

    st, pinv = c.get("/pagar?nivel=99")
    checa("nível inválido não quebra, mostra tudo",
          st == 200 and "Aluguel do consultório" in pinv and "Bolsa que vi na vitrine" in pinv)

    _, csv_f = c.get("/exportar/Pagar?nivel=4")
    linhas_f = csv_f.splitlines()[1:-1]
    checa("exportação respeita o filtro",
          len(linhas_f) >= 1 and all("Evitável" in l for l in linhas_f), f"{len(linhas_f)} linhas")

    print("\nContas a receber", flush=True)
    status, html = c.get("/receber")
    checa("listagem carrega", status == 200, f"status {status}")
    checa("mostra receita semeada", "Atendimentos da semana" in html)

    # Conta a receber vencida e lancada a mao precisa continuar editavel: se
    # for engolida pela linha consolidada de atraso, nao ha como corrigir nem
    # excluir. Consolidacao existe so para as linhas vindas da clinica.
    from datetime import timedelta as _td
    venc_passado = (date.today() - _td(days=5)).isoformat()
    id_atrasado = database.inserir_lancamento(tid, {
        "descricao": "Recebivel atrasado lancado a mao", "tipo": "Receber", "esfera": "Casa",
        "categoria_id": None, "valor": 777.0, "vencimento": venc_passado, "status": "Pendente",
        "data_pagamento": None, "forma_pagamento": "Transferência",
        "frequencia_recorrencia": "Nenhuma", "observacoes": ""})
    c.get("/trocar-esfera/Casa")
    _, html_casa = c.get("/receber")
    checa("recebível atrasado manual continua na lista",
          "Recebivel atrasado lancado a mao" in html_casa)
    checa("e continua editável", f"modalEditarReceita{id_atrasado}" in html_casa)
    checa("na Casa não aparece selo de somente leitura", "Somente Leitura" not in html_casa)
    c.get("/trocar-esfera/Todas")

    print("\nWebhook de integração", flush=True)
    status, _ = chamar_webhook(base, {"descricao": "x", "valor": 1}, token=None)
    checa("recusa chamada sem token", status == 401, f"status {status}")

    status, _ = chamar_webhook(base, {"descricao": "y", "valor": 1}, token="token-errado")
    checa("recusa token inválido", status == 401, f"status {status}")

    ref = "clinic_resumo_pago_2026-08"
    status, corpo = chamar_webhook(base, {
        "descricao": "Receitas Clínica - Total Recebido (08/2026)",
        "valor": 1234.56, "vencimento": date.today().isoformat(),
        "status": "Pago", "referencia_id": ref,
    })
    checa("aceita com token válido", status == 201, f"status {status}")

    status, corpo = chamar_webhook(base, {
        "descricao": "Receitas Clínica - Total Recebido (08/2026)",
        "valor": 999.00, "vencimento": date.today().isoformat(),
        "status": "Pago", "referencia_id": ref,
    })
    checa("reenviar atualiza em vez de duplicar",
          status == 200 and corpo and corpo.get("acao") == "atualizado", f"{status} {corpo}")

    status, _ = chamar_webhook(base, {"descricao": "z", "valor": 1, "vencimento": "31/12/2026"})
    checa("recusa vencimento em formato brasileiro", status == 400, f"status {status}")

    status, _ = chamar_webhook(base, {"descricao": "z", "valor": "abc"})
    checa("recusa valor não numérico", status == 400, f"status {status}")

    status, _ = chamar_webhook(base, {"descricao": "z", "valor": 1, "vencimento": "2026-12-31"})
    checa("aceita vencimento ISO válido", status == 201, f"status {status}")

    print("\nSaldo que atravessa o mês", flush=True)
    _, dash = c.get("/")
    for rotulo in ["Vem do mês anterior", "Entrou no mês", "Saiu no mês", "Fecha o mês com"]:
        checa(f"dashboard mostra '{rotulo}'", rotulo in dash)
    checa("avisa que falta saldo inicial", "saldo inicial definido" in dash)

    # O caso central: pagar em mês diferente do vencimento. O dinheiro tem que
    # contar no mês em que saiu da conta, não no mês em que a conta venceu.
    import calculos  # importado aqui: o banco temporário já está registrado
    database.definir_saldo_inicial(tid, "Casa", 1000.0, "2026-06-01")
    database.definir_saldo_inicial(tid, "Empresa", 0.0, "2026-06-01")
    # Mede a DIFERENÇA que este lançamento causa: os dados semeados já têm
    # despesas de Casa em agosto, então o total absoluto não diz nada.
    jul_antes = calculos.calcular_saldo_do_mes(tid, "Casa", "2026-07")["saiu"]
    ago_antes = calculos.calcular_saldo_do_mes(tid, "Casa", "2026-08")["saiu"]
    database.inserir_lancamento(tid, {
        "descricao": "Vence em julho, paga em agosto", "tipo": "Pagar", "esfera": "Casa",
        "categoria_id": None, "valor": 500.0, "vencimento": "2026-07-28",
        "status": "Pago", "data_pagamento": "2026-08-05", "forma_pagamento": "Pix",
        "frequencia_recorrencia": "Nenhuma", "observacoes": ""})

    jul = calculos.calcular_saldo_do_mes(tid, "Casa", "2026-07")
    ago = calculos.calcular_saldo_do_mes(tid, "Casa", "2026-08")
    checa("pagamento não conta no mês do vencimento (julho)",
          abs(jul["saiu"] - jul_antes) < 0.01, f"julho variou {jul['saiu'] - jul_antes}")
    checa("e conta no mês em que o dinheiro saiu (agosto)",
          abs((ago["saiu"] - ago_antes) - 500) < 0.01, f"agosto variou {ago['saiu'] - ago_antes}")
    checa("o saldo de um mês é o ponto de partida do seguinte",
          abs(jul["saldo_final"] - ago["saldo_inicial_periodo"]) < 0.01,
          f"{jul['saldo_final']} vs {ago['saldo_inicial_periodo']}")
    checa("a aritmética do extrato fecha",
          abs((ago["saldo_inicial_periodo"] + ago["entrou"] - ago["saiu"]) - ago["saldo_final"]) < 0.01)

    # Lançamento vindo do webhook chega Pago sem data_pagamento — não pode sumir
    database.inserir_lancamento(tid, {
        "descricao": "Recebido sem data de pagamento", "tipo": "Receber", "esfera": "Empresa",
        "categoria_id": None, "valor": 900.0, "vencimento": "2026-08-10",
        "status": "Recebido", "data_pagamento": None, "forma_pagamento": "Pix",
        "frequencia_recorrencia": "Nenhuma", "observacoes": ""})
    emp = calculos.calcular_saldo_do_mes(tid, "Empresa", "2026-08")
    checa("efetivado sem data de pagamento não some do saldo",
          emp["entrou"] >= 900, f"entrou {emp['entrou']}")

    casa = calculos.calcular_saldo_do_mes(tid, "Casa", "2026-08")
    checa("Empresa e Casa têm saldos independentes",
          casa["entrou"] != emp["entrou"] or casa["saiu"] != emp["saiu"])
    todas = calculos.calcular_saldo_do_mes(tid, "Todas", "2026-08")
    checa("'Todas' soma as duas esferas",
          abs(todas["saldo_final"] - (casa["saldo_final"] + emp["saldo_final"])) < 0.01)

    # O seam para a visão por competência existe e responde diferente do caixa
    _, saiu_competencia = database.somar_movimentacoes(
        tid, "Casa", "2026-07-01", "2026-07-31", base="competencia")
    checa("o seam de competência existe e difere do caixa",
          saiu_competencia == 500 and jul["saiu"] == 0,
          f"competência {saiu_competencia}, caixa {jul['saiu']}")

    status, html_si = c.get("/configuracoes/saldo-inicial")
    checa("tela de saldo inicial abre", status == 200, f"status {status}")
    # A data de início governa o saldo: sem ela, a tela recusa gravar valor.
    token = c.csrf("/configuracoes/saldo-inicial")
    status, _ = c.post("/configuracoes/saldo-inicial", {
        "csrf_token": token, "acao": "data_inicio", "data_inicio": "2026-06-01"})
    checa("salvar a data de início funciona", status == 302, f"status {status}")
    checa("e a data foi gravada", database.obter_data_inicio(tid) == "2026-06-01")

    token = c.csrf("/configuracoes/saldo-inicial")
    status, _ = c.post("/configuracoes/saldo-inicial", {
        "csrf_token": token, "esfera": "Casa", "valor": "1500.00"})
    checa("salvar saldo inicial funciona", status == 302, f"status {status}")
    checa("e o valor foi gravado",
          database.obter_saldos_iniciais(tid)["Casa"]["valor"] == 1500.0)

    print("\nData de início do sistema", flush=True)
    # A Lois decidiu começar do zero numa data em vez de reconstruir o passado.
    # Nada anterior pode entrar no caixa nem ficar navegável — inclusive as
    # receitas que a clínica já sincronizou de meses passados.
    database.inserir_lancamento(tid, {
        "descricao": "Receita da clínica de julho", "tipo": "Receber", "esfera": "Empresa",
        "categoria_id": None, "valor": 6268.0, "vencimento": "2026-07-28",
        "status": "Recebido", "data_pagamento": "2026-07-28", "forma_pagamento": "Pix",
        "frequencia_recorrencia": "Nenhuma",
        "observacoes": "Gerado via Integração Clínica. ID Ref: clinic_resumo_pago_2026-07"})

    database.definir_data_inicio(tid, "2026-08-01")
    database.definir_saldo_inicial(tid, "Empresa", 5000.0, "2026-08-01")

    emp = calculos.calcular_saldo_do_mes(tid, "Empresa", "2026-08")
    checa("o saldo parte do valor informado, ignorando o passado",
          abs(emp["saldo_inicial_periodo"] - 5000.0) < 0.01,
          f"veio {emp['saldo_inicial_periodo']}")
    checa("receita da clínica de antes do início não entra no caixa",
          emp["saldo_inicial_periodo"] == 5000.0, "6268 de julho vazou")

    import re as _re
    for tela in ["/", "/pagar", "/receber"]:
        _, html_ant = c.get(f"{tela}?mes=2026-06")
        m = _re.search(r'name="mes" value="([0-9-]+)"', html_ant)
        checa(f"{tela} não navega para antes do início",
              m is not None and m.group(1) == "2026-08", f"mostrou {m.group(1) if m else '?'}")

    _, html_dash = c.get("/")
    checa("o seletor de mês trava no navegador também", 'min="2026-08"' in html_dash)

    _, html_fut = c.get("/?mes=2026-09")
    m_fut = _re.search(r'name="mes" value="([0-9-]+)"', html_fut)
    checa("mês futuro continua livre",
          m_fut is not None and m_fut.group(1) == "2026-09", f"mostrou {m_fut.group(1) if m_fut else '?'}")

    checa("o lançamento antigo continua no banco, só não conta",
          any("clínica de julho" in l["descricao"] for l in database.listar_lancamentos(tid)))

    print("\nExportação", flush=True)
    status, csv_pagar = c.get("/exportar/Pagar")
    checa("exporta contas a pagar", status == 200, f"status {status}")
    linhas_csv = csv_pagar.splitlines()
    checa("usa ';' como separador (Excel em português)", ";" in linhas_csv[0])
    checa("cabeçalho traz a coluna Importância", "Importância" in linhas_csv[0])
    checa("valor com vírgula decimal", any(",00" in l or "," in l.split(";")[5] for l in linhas_csv[1:-1]))
    checa("data em DD/MM/AAAA", "/" in linhas_csv[1].split(";")[0])
    checa("última linha é o TOTAL", linhas_csv[-1].startswith("TOTAL"))

    import csv as _csv, io as _io
    _linhas = list(_csv.reader(_io.StringIO(csv_pagar), delimiter=";"))
    _i = _linhas[0].index("Valor")
    _soma = sum(float(l[_i].replace(",", ".")) for l in _linhas[1:-1])
    _declarado = float(_linhas[-1][_i].replace(",", "."))
    checa("o TOTAL bate com a soma das linhas", abs(_soma - _declarado) < 0.01,
          f"soma {_soma} vs total {_declarado}")

    status, csv_receber = c.get("/exportar/Receber")
    checa("exporta contas a receber", status == 200, f"status {status}")
    checa("receber não tem coluna de importância",
          "Importância" not in csv_receber.splitlines()[0])

    status, corpo_invalido = c.get("/exportar/Qualquer")
    checa("tipo inválido não devolve CSV", "Vencimento;" not in corpo_invalido)

    print("\nTabela de importância", flush=True)
    status, html = c.get("/configuracoes/importancia")
    checa("tela da tabela abre para admin", status == 200, f"status {status}")
    for esperado in ["Indispensável", "Importante", "Desejável", "Evitável"]:
        checa(f"mostra o nível {esperado}", esperado in html)

    token = c.csrf("/configuracoes/importancia")
    status, _ = c.post("/configuracoes/importancia/3/editar", {
        "csrf_token": token, "nome": "Conforto (renomeado)", "apelido": "Teste",
        "significado": "s", "exemplo_empresa": "e", "exemplo_casa": "c",
    })
    checa("renomear um nível funciona", status == 302, f"status {status}")

    _, html = c.get("/pagar")
    checa("o novo nome aparece na listagem", "Conforto (renomeado)" in html)
    checa("e nenhum lançamento perdeu a classificação",
          len([l for l in database.listar_lancamentos(tid, tipo="Pagar")
               if l["importancia_nivel"] == 3]) > 0)

    # Cada organização tem a sua tabela: renomear numa não pode vazar na outra.
    from werkzeug.security import generate_password_hash as _hash
    tid2 = database.criar_tenant("Outra Clínica", "outra-clinica", "tok-outra")
    database.criar_usuario(tid2, "Outra", "outra@ex.com", _hash("senha1234"), is_admin=1)
    niveis2 = {n["nivel"]: n["nome"] for n in database.listar_niveis_importancia(tid2)}
    checa("organização nova já nasce com a escala padrão",
          niveis2.get(3) == "Desejável", f"{niveis2}")
    checa("renomear numa organização não vaza para a outra",
          niveis2.get(3) != "Conforto (renomeado)", f"nivel 3 da outra: {niveis2.get(3)}")

    c2 = Cliente(base)
    c2.post("/login", {"organizacao": "outra-clinica", "email": "outra@ex.com", "senha": "senha1234"})
    _, html2 = c2.get("/configuracoes/importancia")
    checa("a outra organização vê a sua própria tabela",
          "Desejável" in html2 and "Conforto (renomeado)" not in html2)

    token = c.csrf("/configuracoes/importancia")
    c.post("/configuracoes/importancia/restaurar", {"csrf_token": token})
    _, html = c.get("/configuracoes/importancia")
    checa("restaurar padrão devolve o nome original", "Desejável" in html)

    print("\nAdministração", flush=True)
    status, html = c.get("/admin/tenants")
    checa("área de organizações abre para admin", status == 200, f"status {status}")
    checa("mostra o token de API da organização", API_TOKEN in html)

    print("\nEncerramento", flush=True)
    # /logout é GET (a rota não declara methods) — um POST devolve 405 e a
    # sessão continua de pé.
    c.get("/logout")
    _, html = c.get("/")
    checa("depois do logout o dashboard não abre", "Painel Financeiro" not in html)
    checa("e a tela de login pede a organização", 'name="organizacao"' in html)

    print()
    falhas = [n for n, ok in resultados if not ok]
    total = len(resultados)
    if falhas:
        print(f"{len(falhas)} de {total} falharam: {falhas}")
        return 1
    print(f"todas as {total} checagens passaram")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("smoke", help="sobe o app, exercita os fluxos e sai com 0/1")
    s.add_argument("--port", type=int, default=5099)
    s.set_defaults(func=cmd_smoke)

    v = sub.add_parser("serve", help="sobe o app com dados de demonstração e mantém no ar")
    v.add_argument("--port", type=int, default=5099)
    v.set_defaults(func=cmd_serve)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return 2
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
