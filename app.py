# app.py - Arquivo Principal do Sistema Gestão Financeira
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, date
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
import database
import calculos
import emailer
import os
import secrets
import uuid
import re


def _carregar_variaveis_de_ambiente_locais():
    """Lê um arquivo .env (se existir, ignorado pelo Git) e injeta as chaves
    em os.environ, sem sobrescrever variáveis já definidas externamente
    (ex: as configuradas direto no arquivo WSGI do PythonAnywhere)."""
    caminho_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(caminho_env):
        return
    with open(caminho_env, encoding="utf-8") as arquivo:
        for linha in arquivo:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            chave = chave.strip()
            valor = valor.strip().strip('"').strip("'")
            os.environ.setdefault(chave, valor)


_carregar_variaveis_de_ambiente_locais()

app = Flask(__name__)

# Configurações de Upload de Fotos
PASTA_AVATARS = os.path.join(app.root_path, "static", "uploads", "avatars")
os.makedirs(PASTA_AVATARS, exist_ok=True)
EXTENSOES_IMAGEM = {"png", "jpg", "jpeg", "webp"}


def extensao_permitida(nome_arquivo):
    return "." in nome_arquivo and nome_arquivo.rsplit(".", 1)[1].lower() in EXTENSOES_IMAGEM


# Gerenciamento de Secret Key
ARQUIVO_CHAVE_SECRETA = ".secret_key"
if os.path.exists(ARQUIVO_CHAVE_SECRETA):
    with open(ARQUIVO_CHAVE_SECRETA) as arquivo:
        app.secret_key = arquivo.read().strip()
else:
    nova_chave = secrets.token_hex(32)
    with open(ARQUIVO_CHAVE_SECRETA, "w") as arquivo:
        arquivo.write(nova_chave)
    app.secret_key = nova_chave

# Configurações de Segurança de Sessão
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

serializer = URLSafeTimedSerializer(app.secret_key)


@app.after_request
def adicionar_cabecalhos_seguranca(resposta):
    resposta.headers["X-Frame-Options"] = "SAMEORIGIN"
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    return resposta


database.criar_tabelas()


@app.context_processor
def injetar_csrf_e_dados_globais():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    esfera_atual = session.get("esfera_filtro", "Todas")
    return {
        "csrf_token": lambda: session["csrf_token"],
        "esfera_filtro": esfera_atual,
        "tenant_nome": session.get("tenant_nome", "")
    }


@app.before_request
def verificar_csrf():
    if request.method == "POST" and not request.path.startswith("/api/") and request.endpoint not in ["login", "esqueci_senha", "redefinir_senha"]:
        token_enviado = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        token_esperado = session.get("csrf_token")
        if not token_esperado or token_enviado != token_esperado:
            return "Erro de segurança: sessão expirada ou token CSRF inválido. Recarregue a página.", 400


ROTAS_PERMITIDAS_COM_TROCA_OBRIGATORIA = {"trocar_senha_obrigatoria", "logout"}


def login_required(funcao_da_rota):
    @wraps(funcao_da_rota)
    def rota_protegida(*args, **kwargs):
        if "usuario_id" not in session or "tenant_id" not in session:
            return redirect(url_for("login"))
        if session.get("deve_trocar_senha") and request.endpoint not in ROTAS_PERMITIDAS_COM_TROCA_OBRIGATORIA:
            return redirect(url_for("trocar_senha_obrigatoria"))
        return funcao_da_rota(*args, **kwargs)
    return rota_protegida


def admin_required(funcao_da_rota):
    @wraps(funcao_da_rota)
    def rota_protegida(*args, **kwargs):
        if "usuario_id" not in session or "tenant_id" not in session:
            return redirect(url_for("login"))
        if not session.get("usuario_is_admin"):
            return "Acesso restrito a administradores.", 403
        return funcao_da_rota(*args, **kwargs)
    return rota_protegida


def tenant_atual():
    """Retorna o tenant_id do usuário logado na sessão atual."""
    return session.get("tenant_id")


# ===== AUTENTICAÇÃO E PERFIL =====

_tentativas_login = {}
LIMITE_TENTATIVAS_LOGIN = 5
TEMPO_BLOQUEIO_MINUTOS = 15


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")

        # A busca por email não é filtrada por tenant aqui de propósito: neste
        # ponto ainda não sabemos a qual organização o usuário pertence. O
        # tenant_id vem junto do registro do usuário encontrado.
        usuario = database.buscar_usuario_por_email(email)

        if usuario and check_password_hash(usuario["senha_hash"], senha):
            u_dict = dict(usuario)
            tenant = database.buscar_tenant_por_id(u_dict["tenant_id"])
            if not tenant or not tenant["ativo"]:
                return render_template("login.html", erro="Esta organização está inativa. Fale com o administrador.")

            session.clear()
            session.permanent = True
            session["usuario_id"] = u_dict["id"]
            session["usuario_nome"] = u_dict["nome"]
            session["usuario_saudacao"] = u_dict.get("saudacao") or ""
            session["usuario_foto"] = u_dict.get("foto_perfil") or ""
            session["usuario_is_admin"] = bool(u_dict.get("is_admin"))
            session["deve_trocar_senha"] = bool(u_dict.get("deve_trocar_senha"))
            session["tenant_id"] = tenant["id"]
            session["tenant_nome"] = tenant["nome"]
            session["esfera_filtro"] = "Todas"

            if session["deve_trocar_senha"]:
                return redirect(url_for("trocar_senha_obrigatoria"))
            return redirect(url_for("pagina_inicial"))

        return render_template("login.html", erro="Email ou senha incorretos.")

    sucesso = request.args.get("sucesso")
    return render_template("login.html", sucesso=sucesso)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/meu-perfil", methods=["GET", "POST"])
@login_required
def meu_perfil():
    usuario = database.buscar_usuario_por_id(tenant_atual(), session["usuario_id"])
    if not usuario:
        return redirect(url_for("logout"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        saudacao = request.form.get("saudacao", "").strip()

        if not nome:
            return render_template("meu_perfil.html", usuario=usuario, erro="O nome não pode ficar em branco.")

        nome_foto = usuario["foto_perfil"] if ("foto_perfil" in usuario.keys() and usuario["foto_perfil"]) else None

        foto = request.files.get("foto_perfil")
        if foto and foto.filename:
            if not extensao_permitida(foto.filename):
                return render_template("meu_perfil.html", usuario=usuario, erro="Formato de imagem inválido (PNG, JPG, WEBP).")

            pasta_tenant = os.path.join(PASTA_AVATARS, str(tenant_atual()))
            os.makedirs(pasta_tenant, exist_ok=True)

            ext = foto.filename.rsplit(".", 1)[1].lower()
            nome_arquivo = f"user_{usuario['id']}_{uuid.uuid4().hex[:8]}.{ext}"
            foto.save(os.path.join(pasta_tenant, nome_arquivo))
            nome_foto = f"{tenant_atual()}/{nome_arquivo}"

        database.atualizar_perfil_usuario(tenant_atual(), usuario["id"], nome, saudacao, nome_foto)
        session["usuario_nome"] = nome
        session["usuario_saudacao"] = saudacao
        session["usuario_foto"] = nome_foto or ""

        usuario_atualizado = database.buscar_usuario_por_id(tenant_atual(), usuario["id"])
        return render_template("meu_perfil.html", usuario=usuario_atualizado, sucesso="Perfil atualizado com sucesso!")

    return render_template("meu_perfil.html", usuario=usuario)


@app.route("/alterar-senha", methods=["GET", "POST"])
@login_required
def alterar_senha():
    if request.method == "POST":
        senha_atual = request.form.get("senha_atual", "")
        nova_senha = request.form.get("nova_senha", "")
        confirma_senha = request.form.get("confirma_senha", "")

        usuario = database.buscar_usuario_por_id(tenant_atual(), session["usuario_id"])
        if not usuario or not check_password_hash(usuario["senha_hash"], senha_atual):
            return render_template("alterar_senha.html", erro="Senha atual incorreta.")

        if len(nova_senha) < 8:
            return render_template("alterar_senha.html", erro="A nova senha deve ter no mínimo 8 caracteres.")

        if nova_senha != confirma_senha:
            return render_template("alterar_senha.html", erro="As senhas informadas não coincidem.")

        database.atualizar_senha_usuario(tenant_atual(), session["usuario_id"], generate_password_hash(nova_senha))
        return render_template("alterar_senha.html", sucesso="Senha alterada com sucesso!")

    return render_template("alterar_senha.html")


@app.route("/trocar-senha-obrigatoria", methods=["GET", "POST"])
@login_required
def trocar_senha_obrigatoria():
    if not session.get("deve_trocar_senha"):
        return redirect(url_for("pagina_inicial"))

    if request.method == "POST":
        senha_atual = request.form.get("senha_atual", "")
        nova_senha = request.form.get("nova_senha", "")
        confirma_senha = request.form.get("confirma_senha", "")

        usuario = database.buscar_usuario_por_id(tenant_atual(), session["usuario_id"])
        if not usuario or not check_password_hash(usuario["senha_hash"], senha_atual):
            return render_template("trocar_senha_obrigatoria.html", erro="Senha atual incorreta.")

        if len(nova_senha) < 8:
            return render_template("trocar_senha_obrigatoria.html", erro="A nova senha deve ter no mínimo 8 caracteres.")

        if nova_senha != confirma_senha:
            return render_template("trocar_senha_obrigatoria.html", erro="As senhas informadas não coincidem.")

        if nova_senha == senha_atual:
            return render_template("trocar_senha_obrigatoria.html", erro="A nova senha precisa ser diferente da atual.")

        database.atualizar_senha_usuario(tenant_atual(), session["usuario_id"], generate_password_hash(nova_senha))
        session["deve_trocar_senha"] = False
        return redirect(url_for("pagina_inicial", aviso="Senha alterada com sucesso!"))

    return render_template("trocar_senha_obrigatoria.html")


@app.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        usuario = database.buscar_usuario_por_email(email)
        if usuario:
            token = serializer.dumps(email, salt="recuperar-senha")
            link_redefinicao = url_for("redefinir_senha", token=token, _external=True)
            return render_template("esqueci_senha.html", sucesso="Link de recuperação gerado com sucesso!", link_gerado=link_redefinicao)
        return render_template("esqueci_senha.html", erro="Não encontramos nenhuma conta com esse e-mail.")

    return render_template("esqueci_senha.html")


@app.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    try:
        email = serializer.loads(token, salt="recuperar-senha", max_age=1800)
    except (SignatureExpired, BadTimeSignature):
        return render_template("esqueci_senha.html", erro="O link de recuperação expirou. Solicite um novo.")

    usuario = database.buscar_usuario_por_email(email)
    if not usuario:
        return render_template("esqueci_senha.html", erro="Usuário não encontrado.")

    if request.method == "POST":
        nova_senha = request.form.get("nova_senha", "")
        confirma_senha = request.form.get("confirma_senha", "")

        if len(nova_senha) < 8 or nova_senha != confirma_senha:
            return render_template("redefinir_senha.html", token=token, email=email, erro="Senhas inválidas ou não coincidentes.")

        database.atualizar_senha_usuario(usuario["tenant_id"], usuario["id"], generate_password_hash(nova_senha))
        return redirect(url_for("login", sucesso="Senha redefinida com sucesso!"))

    return render_template("redefinir_senha.html", token=token, email=email)


# ===== NAVEGAÇÃO E FILTRO DE ESFERA (EMPRESA / CASA / TODAS) =====

@app.route("/trocar-esfera/<nova_esfera>")
@login_required
def trocar_esfera(nova_esfera):
    if nova_esfera in ["Empresa", "Casa", "Todas"]:
        session["esfera_filtro"] = nova_esfera
    return redirect(request.referrer or url_for("pagina_inicial"))


# ===== PAINEL PRINCIPAL (DASHBOARD) =====

@app.route("/")
@login_required
def pagina_inicial():
    mes_ano = request.args.get("mes", date.today().strftime("%Y-%m"))
    esfera_filtro = session.get("esfera_filtro", "Todas")

    resumo = calculos.calcular_resumo_financeiro(tenant_atual(), esfera_filtro, mes_ano)
    despesas_categorias = calculos.calcular_despesas_por_categoria(tenant_atual(), esfera_filtro, mes_ano)
    dias_uteis = calculos.dias_uteis_restantes_no_mes()

    return render_template(
        "index.html",
        resumo=resumo,
        despesas_categorias=despesas_categorias,
        dias_uteis=dias_uteis,
        mes_ano=mes_ano,
        esfera_filtro=esfera_filtro
    )


# ===== CONTAS A PAGAR =====

@app.route("/pagar")
@login_required
def listar_pagar():
    mes_ano = request.args.get("mes", date.today().strftime("%Y-%m"))
    esfera_filtro = session.get("esfera_filtro", "Todas")

    lancamentos = database.listar_lancamentos(tenant_atual(), tipo="Pagar", esfera=esfera_filtro, mes_ano=mes_ano)
    categorias = database.listar_categorias(tenant_atual(), tipo="Pagar", esfera=esfera_filtro)

    return render_template("pagar.html", lancamentos=lancamentos, categorias=categorias, mes_ano=mes_ano)


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
        "observacoes": request.form.get("observacoes", "")
    }
    if dados["status"] == "Pago":
        dados["data_pagamento"] = request.form.get("data_pagamento") or date.today().isoformat()

    id_novo = database.inserir_lancamento(tenant_atual(), dados)
    calculos.projetar_recorrencias_do_mes(tenant_atual(), dados)
    return redirect(url_for("listar_pagar"))


@app.route("/pagar/<int:id_lancamento>/toggle-status", methods=["POST"])
@login_required
def toggle_status_pagar(id_lancamento):
    lancamento = database.buscar_lancamento(tenant_atual(), id_lancamento)
    if lancamento:
        novo_status = "Pendente" if lancamento["status"] == "Pago" else "Pago"
        data_pagto = date.today().isoformat() if novo_status == "Pago" else None
        database.alternar_status_lancamento(tenant_atual(), id_lancamento, novo_status, data_pagto)
    return redirect(url_for("listar_pagar"))


@app.route("/pagar/<int:id_lancamento>/editar", methods=["POST"])
@login_required
def editar_pagar(id_lancamento):
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
        "observacoes": request.form.get("observacoes", "")
    }
    if dados["status"] == "Pago":
        dados["data_pagamento"] = request.form.get("data_pagamento") or date.today().isoformat()
    else:
        dados["data_pagamento"] = None

    database.atualizar_lancamento(tenant_atual(), id_lancamento, dados)
    calculos.projetar_recorrencias_do_mes(tenant_atual(), dados)
    return redirect(url_for("listar_pagar"))


@app.route("/pagar/<int:id_lancamento>/excluir", methods=["POST"])
@login_required
def excluir_pagar(id_lancamento):
    database.excluir_lancamento(tenant_atual(), id_lancamento)
    return redirect(url_for("listar_pagar"))


# ===== CONTAS A RECEBER =====

@app.route("/receber")
@login_required
def listar_receber():
    mes_ano = request.args.get("mes", date.today().strftime("%Y-%m"))
    esfera_filtro = session.get("esfera_filtro", "Todas")
    hoje_str = date.today().isoformat()

    raw_lancamentos = database.listar_lancamentos(tenant_atual(), tipo="Receber", esfera=esfera_filtro, mes_ano=mes_ano)
    categorias = database.listar_categorias(tenant_atual(), tipo="Receber", esfera=esfera_filtro)

    total_recebido = 0.0
    total_no_prazo = 0.0
    total_atrasado = 0.0
    vencimento_mais_recente_atrasado = None

    lancamentos_normais = []
    atrasados_lista = []

    for item in raw_lancamentos:
        l = dict(item)
        valor = float(l["valor"] or 0)
        status = l["status"]
        vencimento = l["vencimento"]

        if status in ["Pago", "Recebido"]:
            l["status_calculado"] = "Recebido"
            total_recebido += valor
            lancamentos_normais.append(l)
        elif vencimento < hoje_str:
            l["status_calculado"] = "Atrasado"
            total_atrasado += valor
            atrasados_lista.append(l)
            if not vencimento_mais_recente_atrasado or vencimento > vencimento_mais_recente_atrasado:
                vencimento_mais_recente_atrasado = vencimento
        else:
            l["status_calculado"] = "No Prazo"
            total_no_prazo += valor
            lancamentos_normais.append(l)

    # Se existirem itens em atraso, cria UMA ÚNICA linha consolidada no topo da lista
    lancamentos = []
    if total_atrasado > 0:
        linha_consolidada_atraso = {
            "id": "atraso_consolidado",
            "descricao": "⚠️ Total de Receitas em Atraso (Consolidado)",
            "tipo": "Receber",
            "esfera": esfera_filtro if esfera_filtro != "Todas" else "Empresa",
            "categoria_nome": "Atrasados",
            "valor": total_atrasado,
            "vencimento": vencimento_mais_recente_atrasado or hoje_str,
            "status": "Pendente",
            "status_calculado": "Atrasado",
            "forma_pagamento": "Diversos",
            "recorrente": 0,
            "observacoes": f"Consolidado de {len(atrasados_lista)} conta(s) em atraso acumuladas."
        }
        lancamentos.append(linha_consolidada_atraso)

    lancamentos.extend(lancamentos_normais)

    resumo_receber = {
        "recebido": total_recebido,
        "no_prazo": total_no_prazo,
        "atrasado": total_atrasado,
        "total": total_recebido + total_no_prazo + total_atrasado
    }

    return render_template(
        "receber.html",
        lancamentos=lancamentos,
        categorias=categorias,
        mes_ano=mes_ano,
        resumo_receber=resumo_receber
    )


@app.route("/receber/novo", methods=["POST"])
@login_required
def novo_receber():
    freq = request.form.get("frequencia_recorrencia")
    if not freq:
        freq = "Mensal" if request.form.get("recorrente") else "Nenhuma"

    dados = {
        "descricao": request.form.get("descricao", "").strip(),
        "tipo": "Receber",
        "esfera": request.form.get("esfera", "Empresa"),
        "categoria_id": request.form.get("categoria_id") or None,
        "valor": float(request.form.get("valor", 0)),
        "vencimento": request.form.get("vencimento", date.today().isoformat()),
        "status": request.form.get("status", "Pendente"),
        "forma_pagamento": request.form.get("forma_pagamento", "Pix"),
        "recorrente": 1 if freq != "Nenhuma" else 0,
        "frequencia_recorrencia": freq,
        "observacoes": request.form.get("observacoes", "")
    }
    if dados["status"] == "Pago":
        dados["data_pagamento"] = request.form.get("data_pagamento") or date.today().isoformat()

    id_novo = database.inserir_lancamento(tenant_atual(), dados)
    calculos.projetar_recorrencias_do_mes(tenant_atual(), dados)
    return redirect(url_for("listar_receber"))


@app.route("/receber/<int:id_lancamento>/toggle-status", methods=["POST"])
@login_required
def toggle_status_receber(id_lancamento):
    lancamento = database.buscar_lancamento(tenant_atual(), id_lancamento)
    if lancamento:
        if lancamento["observacoes"] and "ID Ref:" in lancamento["observacoes"]:
            return redirect(url_for("listar_receber", erro="Lançamentos sincronizados da clínica são somente leitura."))
        novo_status = "Pendente" if lancamento["status"] == "Pago" else "Pago"
        data_pagto = date.today().isoformat() if novo_status == "Pago" else None
        database.alternar_status_lancamento(tenant_atual(), id_lancamento, novo_status, data_pagto)
    return redirect(url_for("listar_receber"))


@app.route("/receber/<int:id_lancamento>/editar", methods=["POST"])
@login_required
def editar_receber(id_lancamento):
    lancamento = database.buscar_lancamento(tenant_atual(), id_lancamento)
    if lancamento and lancamento["observacoes"] and "ID Ref:" in lancamento["observacoes"]:
        return redirect(url_for("listar_receber", erro="Lançamentos sincronizados da clínica são somente leitura e não podem ser editados."))

    freq = request.form.get("frequencia_recorrencia")
    if not freq:
        freq = "Mensal" if request.form.get("recorrente") else "Nenhuma"

    dados = {
        "descricao": request.form.get("descricao", "").strip(),
        "tipo": "Receber",
        "esfera": request.form.get("esfera", "Empresa"),
        "categoria_id": request.form.get("categoria_id") or None,
        "valor": float(request.form.get("valor", 0)),
        "vencimento": request.form.get("vencimento", date.today().isoformat()),
        "status": request.form.get("status", "Pendente"),
        "forma_pagamento": request.form.get("forma_pagamento", "Pix"),
        "recorrente": 1 if freq != "Nenhuma" else 0,
        "frequencia_recorrencia": freq,
        "observacoes": request.form.get("observacoes", "")
    }
    if dados["status"] in ["Pago", "Recebido"]:
        dados["data_pagamento"] = request.form.get("data_pagamento") or date.today().isoformat()
    else:
        dados["data_pagamento"] = None

    database.atualizar_lancamento(tenant_atual(), id_lancamento, dados)
    calculos.projetar_recorrencias_do_mes(tenant_atual(), dados)
    return redirect(url_for("listar_receber"))


@app.route("/receber/<int:id_lancamento>/excluir", methods=["POST"])
@login_required
def excluir_receber(id_lancamento):
    lancamento = database.buscar_lancamento(tenant_atual(), id_lancamento)
    if lancamento and lancamento["observacoes"] and "ID Ref:" in lancamento["observacoes"]:
        return redirect(url_for("listar_receber", erro="Lançamentos sincronizados da clínica são somente leitura e não podem ser excluídos."))

    database.excluir_lancamento(tenant_atual(), id_lancamento)
    return redirect(url_for("listar_receber"))


# ===== CATEGORIAS E RECORRÊNCIAS =====

@app.route("/categorias")
@login_required
def listar_categorias_view():
    categorias = database.listar_categorias(tenant_atual())
    return render_template("categorias.html", categorias=categorias)


@app.route("/categorias/nova", methods=["POST"])
@login_required
def nova_categoria():
    nome = request.form.get("nome", "").strip()
    tipo = request.form.get("tipo", "Pagar")
    esfera = request.form.get("esfera", "Ambos")

    if nome:
        database.inserir_categoria(tenant_atual(), nome, tipo, esfera)
    return redirect(url_for("listar_categorias_view"))


@app.route("/categorias/<int:id_categoria>/editar", methods=["POST"])
@login_required
def editar_categoria_view(id_categoria):
    nome = request.form.get("nome", "").strip()
    tipo = request.form.get("tipo", "Pagar")
    esfera = request.form.get("esfera", "Ambos")

    if nome:
        database.atualizar_categoria(tenant_atual(), id_categoria, nome, tipo, esfera)
    return redirect(url_for("listar_categorias_view"))


@app.route("/categorias/<int:id_categoria>/excluir", methods=["POST"])
@login_required
def excluir_categoria_view(id_categoria):
    database.excluir_categoria(tenant_atual(), id_categoria)
    return redirect(url_for("listar_categorias_view"))


@app.route("/gerar-recorrencias", methods=["POST"])
@login_required
def gerar_recorrencias():
    mes_destino = request.form.get("mes_destino", date.today().strftime("%Y-%m"))
    qtd = calculos.gerar_recorrencias_do_mes(tenant_atual(), mes_destino)
    return redirect(url_for("pagina_inicial", mes=mes_destino, aviso=f"{qtd} despesas recorrentes geradas!"))


# ===== ADMINISTRAÇÃO DE TENANTS (multi-tenancy) =====

def _normalizar_slug(texto):
    return re.sub(r"[^a-z0-9-]+", "-", texto.strip().lower()).strip("-")


def _renderizar_admin_tenants(**kwargs):
    tenants = database.listar_tenants()
    usuarios_por_tenant = {t["id"]: database.listar_usuarios_por_tenant(t["id"]) for t in tenants}
    return render_template("admin_tenants.html", tenants=tenants, usuarios_por_tenant=usuarios_por_tenant, **kwargs)


def _criar_usuario_com_convite(tenant_id, nome_organizacao, nome_usuario, email_usuario, is_admin=0):
    """Cria um usuário com senha temporária + troca obrigatória, e tenta
    enviar o convite por email. Retorna a mensagem de status pronta pra UI."""
    senha_temporaria = secrets.token_urlsafe(9)
    database.criar_usuario(
        tenant_id, nome_usuario, email_usuario,
        generate_password_hash(senha_temporaria), saudacao=None, is_admin=is_admin, deve_trocar_senha=1
    )

    url_login = url_for("login", _external=True)
    email_enviado, msg_email = emailer.enviar_email_convite(
        destinatario=email_usuario, nome_usuario=nome_usuario, nome_organizacao=nome_organizacao,
        email_login=email_usuario, senha_temporaria=senha_temporaria, url_login=url_login
    )

    if email_enviado:
        return f"Convite enviado por email para {email_usuario}."
    return (
        f"Usuário criado, mas o email não foi enviado ({msg_email}). "
        f"Senha temporária de {email_usuario}: {senha_temporaria} (repasse manualmente)."
    )


@app.route("/admin/tenants")
@admin_required
def admin_listar_tenants():
    return _renderizar_admin_tenants()


@app.route("/admin/tenants/novo", methods=["POST"])
@admin_required
def admin_novo_tenant():
    nome = request.form.get("nome", "").strip()
    slug = _normalizar_slug(request.form.get("slug", ""))

    admin_nome = request.form.get("admin_nome", "").strip()
    admin_email = request.form.get("admin_email", "").strip().lower()

    if not nome or not slug or not admin_nome or not admin_email:
        return _renderizar_admin_tenants(erro="Preencha nome e slug da organização, além de nome e email do primeiro usuário.")

    if database.buscar_tenant_por_slug(slug):
        return _renderizar_admin_tenants(erro="Já existe uma organização com esse slug.")

    api_token = secrets.token_hex(24)
    tenant_id = database.criar_tenant(nome, slug, api_token)
    # is_admin=0 aqui de propósito: "is_admin" hoje dá acesso à área
    # /admin/tenants, que enxerga TODAS as organizações da plataforma (é uma
    # permissão de dona da plataforma, não de dona da clínica). O usuário
    # inicial de cada organização nova entra como usuário comum; só você
    # deve ter is_admin=1.
    msg = _criar_usuario_com_convite(tenant_id, nome, admin_nome, admin_email, is_admin=0)
    return _renderizar_admin_tenants(sucesso=f"Organização '{nome}' criada. {msg}")


@app.route("/admin/tenants/<int:id_tenant>/editar", methods=["POST"])
@admin_required
def admin_editar_tenant(id_tenant):
    tenant = database.buscar_tenant_por_id(id_tenant)
    if not tenant:
        return _renderizar_admin_tenants(erro="Organização não encontrada.")

    nome = request.form.get("nome", "").strip()
    slug = _normalizar_slug(request.form.get("slug", ""))
    ativo = request.form.get("ativo") == "on"

    if not nome or not slug:
        return _renderizar_admin_tenants(erro="Nome e slug não podem ficar em branco.")

    slug_existente = database.buscar_tenant_por_slug(slug)
    if slug_existente and slug_existente["id"] != id_tenant:
        return _renderizar_admin_tenants(erro="Já existe outra organização com esse slug.")

    database.atualizar_tenant(id_tenant, nome, slug, ativo)
    return _renderizar_admin_tenants(sucesso=f"Organização '{nome}' atualizada.")


@app.route("/admin/tenants/<int:id_tenant>/excluir", methods=["POST"])
@admin_required
def admin_excluir_tenant(id_tenant):
    tenant = database.buscar_tenant_por_id(id_tenant)
    if not tenant:
        return _renderizar_admin_tenants(erro="Organização não encontrada.")

    confirmacao = request.form.get("confirmar_slug", "").strip().lower()
    if confirmacao != tenant["slug"]:
        return _renderizar_admin_tenants(erro=f"Confirmação incorreta. Digite exatamente \"{tenant['slug']}\" para excluir.")

    # Não deixa você se excluir sem querer e ficar trancada fora da própria
    # organização enquanto ainda está logada nela.
    if tenant_atual() == id_tenant:
        return _renderizar_admin_tenants(erro="Você não pode excluir a organização em que está logada agora.")

    database.excluir_tenant(id_tenant)
    return _renderizar_admin_tenants(sucesso=f"Organização '{tenant['nome']}' e todos os seus dados foram excluídos.")


@app.route("/admin/tenants/<int:id_tenant>/novo-usuario", methods=["POST"])
@admin_required
def admin_novo_usuario_tenant(id_tenant):
    tenant = database.buscar_tenant_por_id(id_tenant)
    if not tenant:
        return _renderizar_admin_tenants(erro="Organização não encontrada.")

    nome_usuario = request.form.get("nome", "").strip()
    email_usuario = request.form.get("email", "").strip().lower()

    if not nome_usuario or not email_usuario:
        return _renderizar_admin_tenants(erro="Preencha nome e email do novo usuário.")

    if database.buscar_usuario_por_email(email_usuario, tenant_id=id_tenant):
        return _renderizar_admin_tenants(erro=f"Já existe um usuário com esse email em '{tenant['nome']}'.")

    # is_admin sempre 0 aqui: usuários adicionados a uma organização existente
    # nunca recebem acesso de admin de plataforma por essa tela.
    msg = _criar_usuario_com_convite(id_tenant, tenant["nome"], nome_usuario, email_usuario, is_admin=0)
    return _renderizar_admin_tenants(sucesso=f"Usuário adicionado a '{tenant['nome']}'. {msg}")


@app.route("/admin/tenants/<int:id_tenant>/gerar-token", methods=["POST"])
@admin_required
def admin_gerar_token_tenant(id_tenant):
    novo_token = secrets.token_hex(24)
    database.atualizar_token_tenant(id_tenant, novo_token)
    return _renderizar_admin_tenants(sucesso="Token de API regenerado.")


# ===== FILTROS E FORMATADORES DE TEMPLATE =====

@app.template_filter("moeda_br")
def moeda_br(valor):
    if valor is None:
        return "R$ 0,00"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("data_br")
def data_br(valor):
    if not valor:
        return ""
    try:
        ano, mes, dia = str(valor).split("-")
        return f"{dia}/{mes}/{ano}"
    except Exception:
        return valor


# ===== API WEBHOOK (INTEGRAÇÃO FASE 2 COM A CLÍNICA) =====

@app.route("/api/v1/receber/webhook", methods=["POST"])
def api_webhook_receber():
    # Cada organização tem seu próprio token de API (tenants.api_token). O
    # chamador deve enviar esse token no header X-Api-Token para que o
    # webhook saiba em qual tenant gravar o lançamento.
    api_token = request.headers.get("X-Api-Token")
    tenant = database.buscar_tenant_por_token(api_token)
    if not tenant:
        return jsonify({"erro": "Token de API inválido ou ausente. Envie o header X-Api-Token."}), 401

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

    if existente:
        database.atualizar_lancamento(tenant["id"], existente["id"], dados_lancamento)
        return jsonify({"sucesso": True, "acao": "atualizado", "id_lancamento": existente["id"]}), 200
    else:
        id_gerado = database.inserir_lancamento(tenant["id"], dados_lancamento)
        return jsonify({"sucesso": True, "acao": "criado", "id_lancamento": id_gerado}), 201


if __name__ == "__main__":
    app.run(port=5002, debug=True)
