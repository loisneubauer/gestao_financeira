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
        ("Aluguel do consultório", 2200.00, "Imprescindível", "Moradia", "Empresa", 5, "Pago"),
        ("Energia elétrica", 340.50, "Imprescindível", "Moradia", "Casa", 10, "Pago"),
        ("Agulhas e insumos", 620.00, "Necessário", "Insumos", "Empresa", 12, "Pago"),
        ("Streaming (3 serviços)", 110.00, "Supérfluo", "Lazer", "Casa", 7, "Pago"),
        ("Bolsa que vi na vitrine", 520.00, "Impulso", "Lazer", "Casa", 14, "Pago"),
        ("Manutenção do ar", 260.00, None, "Moradia", "Empresa", 25, "Pendente"),
    ]
    for descricao, valor, importancia, categoria, esfera, dia, status in despesas:
        database.inserir_lancamento(tid, {
            "descricao": descricao, "tipo": "Pagar", "esfera": esfera,
            "categoria_id": categorias.get(categoria), "valor": valor,
            "vencimento": venc(dia), "status": status,
            "data_pagamento": venc(dia) if status == "Pago" else None,
            "forma_pagamento": "Pix", "frequencia_recorrencia": "Nenhuma",
            "observacoes": "", "importancia": importancia,
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
    checa("dashboard calcula o gasto evitável", "evitável" in html)

    print("\nContas a pagar", flush=True)
    status, html = c.get("/pagar")
    checa("listagem carrega", status == 200, f"status {status}")
    checa("mostra despesa semeada", "Aluguel do consultório" in html)
    checa("mostra o nível de importância", "Imprescindível" in html)

    token = c.csrf("/pagar")
    status, _ = c.post("/pagar/novo", {
        "csrf_token": token, "descricao": "Despesa criada pelo driver",
        "esfera": "Casa", "valor": "99.90",
        "vencimento": date.today().isoformat(), "status": "Pendente",
        "forma_pagamento": "Pix", "frequencia_recorrencia": "Nenhuma",
        "importancia": "Impulso", "observacoes": "",
    })
    checa("cria uma despesa nova", status == 302, f"status {status}")

    _, html = c.get("/pagar")
    checa("a despesa nova aparece na lista", "Despesa criada pelo driver" in html)
    checa("com a importância escolhida", "Impulso" in html)

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
        "importancia": "", "observacoes": "",
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
        "importancia": "", "observacoes": "",
    })
    _, html = c.get("/pagar")
    checa("valor válido continua gravando (sem regressão)",
          status == 302 and "valor valido do smoke" in html, f"status {status}")

    print("\nContas a receber", flush=True)
    status, html = c.get("/receber")
    checa("listagem carrega", status == 200, f"status {status}")
    checa("mostra receita semeada", "Atendimentos da semana" in html)

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
