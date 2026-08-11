# database.py - Módulo de Banco de Dados do Gestão Financeira
import sqlite3
import os

NOME_DO_BANCO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "financeiro.db")


def conectar():
    """Abre uma conexão com o banco de dados financeiro.db."""
    conexao = sqlite3.connect(NOME_DO_BANCO)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


# ===== HELPERS DE MIGRAÇÃO =====

def _tabela_existe(conexao, tabela):
    row = conexao.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (tabela,)
    ).fetchone()
    return row is not None


def _coluna_existe(conexao, tabela, coluna):
    colunas = [row[1] for row in conexao.execute(f"PRAGMA table_info({tabela})").fetchall()]
    return coluna in colunas


def _obter_ou_criar_tenant_padrao(conexao):
    """Retorna o id do primeiro tenant existente ou cria um tenant padrão
    para receber os dados de instalações anteriores ao multi-tenancy."""
    tenant = conexao.execute("SELECT id FROM tenants ORDER BY id ASC LIMIT 1").fetchone()
    if tenant:
        return tenant["id"]
    cursor = conexao.execute(
        "INSERT INTO tenants (nome, slug, ativo) VALUES (?, ?, 1)",
        ("Acupuntura Bem-estar", "padrao")
    )
    return cursor.lastrowid


def _migrar_usuarios_para_multi_tenant(conexao, tenant_id_padrao):
    """Reconstrói a tabela usuarios para trocar UNIQUE(email) por
    UNIQUE(tenant_id, email) e adicionar tenant_id/is_admin."""
    if not _tabela_existe(conexao, "usuarios") or _coluna_existe(conexao, "usuarios", "tenant_id"):
        return

    conexao.execute("ALTER TABLE usuarios RENAME TO usuarios_legado")
    conexao.execute("""
        CREATE TABLE usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants (id),
            nome TEXT NOT NULL,
            email TEXT NOT NULL,
            senha_hash TEXT NOT NULL,
            foto_perfil TEXT,
            saudacao TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            deve_trocar_senha INTEGER NOT NULL DEFAULT 0,
            UNIQUE (tenant_id, email)
        )
    """)
    conexao.execute("""
        INSERT INTO usuarios (id, tenant_id, nome, email, senha_hash, foto_perfil, saudacao, is_admin, deve_trocar_senha)
        SELECT id, ?, nome, email, senha_hash, foto_perfil, saudacao, 0, 0 FROM usuarios_legado
    """, (tenant_id_padrao,))
    conexao.execute("DROP TABLE usuarios_legado")


def _migrar_coluna_tenant_simples(conexao, tabela, tenant_id_padrao):
    """Adiciona tenant_id (NOT NULL, com default = tenant padrão) em tabelas
    que não têm restrição UNIQUE afetada pelo tenant (categorias, lancamentos)."""
    if not _tabela_existe(conexao, tabela) or _coluna_existe(conexao, tabela, "tenant_id"):
        return
    conexao.execute(
        f"ALTER TABLE {tabela} ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT {int(tenant_id_padrao)}"
    )


def criar_tabelas():
    """Cria as tabelas do sistema financeiro caso ainda não existam e migra
    instalações antigas (sem tenant_id) para o modelo multi-tenant."""
    conexao = conectar()

    # Tenants (organizações/clínicas) - precisa existir antes das FKs de tenant_id
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            ativo INTEGER NOT NULL DEFAULT 1,
            criado_em TEXT NOT NULL DEFAULT (datetime('now')),
            api_token TEXT UNIQUE
        )
    """)

    # Tabela de Categorias (ex: Aluguel, Luz, Insumos, Consultas)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants (id),
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL,      -- 'Pagar' ou 'Receber'
            esfera TEXT NOT NULL     -- 'Empresa', 'Casa' ou 'Ambos'
        )
    """)

    # Tabela de Lançamentos (Despesas e Receitas)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS lancamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants (id),
            descricao TEXT NOT NULL,
            tipo TEXT NOT NULL,          -- 'Pagar' ou 'Receber'
            esfera TEXT NOT NULL,        -- 'Empresa' ou 'Casa'
            categoria_id INTEGER,
            valor REAL NOT NULL DEFAULT 0,
            vencimento TEXT NOT NULL,    -- AAAA-MM-DD
            data_pagamento TEXT,         -- AAAA-MM-DD (preenchido quando pago/recebido)
            status TEXT DEFAULT 'Pendente', -- 'Pendente', 'Pago', 'Atrasado'
            forma_pagamento TEXT,        -- Pix, Boleto, Cartão, Dinheiro, Transferência
            recorrente INTEGER DEFAULT 0, -- 1 = Sim, 0 = Não
            frequencia_recorrencia TEXT DEFAULT 'Nenhuma',
            observacoes TEXT,
            FOREIGN KEY (categoria_id) REFERENCES categorias (id)
        )
    """)

    # Tabela de Usuários para Login e Perfil
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants (id),
            nome TEXT NOT NULL,
            email TEXT NOT NULL,
            senha_hash TEXT NOT NULL,
            foto_perfil TEXT,
            saudacao TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            deve_trocar_senha INTEGER NOT NULL DEFAULT 0,
            UNIQUE (tenant_id, email)
        )
    """)

    conexao.commit()

    # ===== Migração de instalações anteriores ao multi-tenancy =====
    precisa_migrar = (
        (_tabela_existe(conexao, "usuarios") and not _coluna_existe(conexao, "usuarios", "tenant_id"))
        or (_tabela_existe(conexao, "categorias") and not _coluna_existe(conexao, "categorias", "tenant_id"))
        or (_tabela_existe(conexao, "lancamentos") and not _coluna_existe(conexao, "lancamentos", "tenant_id"))
    )
    if precisa_migrar:
        tenant_id_padrao = _obter_ou_criar_tenant_padrao(conexao)
        _migrar_usuarios_para_multi_tenant(conexao, tenant_id_padrao)
        _migrar_coluna_tenant_simples(conexao, "categorias", tenant_id_padrao)
        _migrar_coluna_tenant_simples(conexao, "lancamentos", tenant_id_padrao)
        conexao.commit()

    # Migração legada: coluna frequencia_recorrencia (mantida por compatibilidade)
    if _tabela_existe(conexao, "lancamentos") and not _coluna_existe(conexao, "lancamentos", "frequencia_recorrencia"):
        conexao.execute("ALTER TABLE lancamentos ADD COLUMN frequencia_recorrencia TEXT DEFAULT 'Nenhuma'")
        conexao.execute("UPDATE lancamentos SET frequencia_recorrencia = 'Mensal' WHERE recorrente = 1")

    # Migração legada: coluna is_admin (caso a tabela usuarios já tivesse tenant_id mas não is_admin)
    if _tabela_existe(conexao, "usuarios") and not _coluna_existe(conexao, "usuarios", "is_admin"):
        conexao.execute("ALTER TABLE usuarios ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")

    # Migração legada: coluna deve_trocar_senha (força troca de senha no próximo login)
    if _tabela_existe(conexao, "usuarios") and not _coluna_existe(conexao, "usuarios", "deve_trocar_senha"):
        conexao.execute("ALTER TABLE usuarios ADD COLUMN deve_trocar_senha INTEGER NOT NULL DEFAULT 0")

    conexao.commit()
    conexao.close()


# ===== TENANTS =====

def listar_tenants():
    conexao = conectar()
    tenants = conexao.execute("SELECT * FROM tenants ORDER BY nome ASC").fetchall()
    conexao.close()
    return tenants


def buscar_tenant_por_id(tenant_id):
    conexao = conectar()
    tenant = conexao.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    conexao.close()
    return tenant


def buscar_tenant_por_slug(slug):
    conexao = conectar()
    tenant = conexao.execute("SELECT * FROM tenants WHERE slug = ?", (slug,)).fetchone()
    conexao.close()
    return tenant


def buscar_tenant_por_token(api_token):
    if not api_token:
        return None
    conexao = conectar()
    tenant = conexao.execute(
        "SELECT * FROM tenants WHERE api_token = ? AND ativo = 1", (api_token,)
    ).fetchone()
    conexao.close()
    return tenant


def criar_tenant(nome, slug, api_token=None):
    conexao = conectar()
    cursor = conexao.execute(
        "INSERT INTO tenants (nome, slug, ativo, api_token) VALUES (?, ?, 1, ?)",
        (nome, slug, api_token)
    )
    conexao.commit()
    id_novo = cursor.lastrowid
    conexao.close()
    return id_novo


def atualizar_token_tenant(tenant_id, novo_token):
    conexao = conectar()
    conexao.execute("UPDATE tenants SET api_token = ? WHERE id = ?", (novo_token, tenant_id))
    conexao.commit()
    conexao.close()


def atualizar_tenant(tenant_id, nome, slug, ativo):
    conexao = conectar()
    conexao.execute(
        "UPDATE tenants SET nome = ?, slug = ?, ativo = ? WHERE id = ?",
        (nome, slug, 1 if ativo else 0, tenant_id)
    )
    conexao.commit()
    conexao.close()


def excluir_tenant(tenant_id):
    """Apaga a organização e TODOS os dados vinculados a ela (usuários,
    categorias, lançamentos). Operação irreversível — a confirmação (digitar
    o slug) deve acontecer na camada de rota, antes de chamar esta função."""
    conexao = conectar()
    conexao.execute("DELETE FROM lancamentos WHERE tenant_id = ?", (tenant_id,))
    conexao.execute("DELETE FROM categorias WHERE tenant_id = ?", (tenant_id,))
    conexao.execute("DELETE FROM usuarios WHERE tenant_id = ?", (tenant_id,))
    conexao.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
    conexao.commit()
    conexao.close()


def listar_usuarios_por_tenant(tenant_id):
    conexao = conectar()
    usuarios = conexao.execute(
        "SELECT id, nome, email, is_admin, deve_trocar_senha FROM usuarios WHERE tenant_id = ? ORDER BY nome ASC",
        (tenant_id,)
    ).fetchall()
    conexao.close()
    return usuarios


# ===== CATEGORIAS =====

def listar_categorias(tenant_id, tipo=None, esfera=None):
    conexao = conectar()
    query = "SELECT * FROM categorias WHERE tenant_id = ?"
    params = [tenant_id]
    if tipo and tipo != "Ambos":
        query += " AND (tipo = ? OR tipo = 'Ambos')"
        params.append(tipo)
    if esfera and esfera != "Todas" and esfera != "Ambos":
        query += " AND (esfera = ? OR esfera = 'Ambos')"
        params.append(esfera)
    query += " ORDER BY nome ASC"
    categorias = conexao.execute(query, params).fetchall()
    conexao.close()
    return categorias


def buscar_categoria(tenant_id, id_categoria):
    conexao = conectar()
    categoria = conexao.execute(
        "SELECT * FROM categorias WHERE id = ? AND tenant_id = ?", (id_categoria, tenant_id)
    ).fetchone()
    conexao.close()
    return categoria


def inserir_categoria(tenant_id, nome, tipo, esfera):
    conexao = conectar()
    conexao.execute(
        "INSERT INTO categorias (tenant_id, nome, tipo, esfera) VALUES (?, ?, ?, ?)",
        (tenant_id, nome, tipo, esfera)
    )
    conexao.commit()
    conexao.close()


def atualizar_categoria(tenant_id, id_categoria, nome, tipo, esfera):
    conexao = conectar()
    conexao.execute(
        "UPDATE categorias SET nome = ?, tipo = ?, esfera = ? WHERE id = ? AND tenant_id = ?",
        (nome, tipo, esfera, id_categoria, tenant_id)
    )
    conexao.commit()
    conexao.close()


def excluir_categoria(tenant_id, id_categoria):
    conexao = conectar()
    conexao.execute("DELETE FROM categorias WHERE id = ? AND tenant_id = ?", (id_categoria, tenant_id))
    conexao.commit()
    conexao.close()


# ===== LANÇAMENTOS =====

def listar_lancamentos(tenant_id, tipo=None, esfera=None, mes_ano=None, incluir_atrasados_anteriores=True):
    """
    Busca lançamentos de um tenant, filtrados por tipo ('Pagar'/'Receber'), esfera
    ('Empresa'/'Casa') e mês de referência ('AAAA-MM'). Inclui por padrão contas em
    atraso de meses anteriores.
    """
    conexao = conectar()
    query = """
        SELECT lancamentos.*, categorias.nome AS categoria_nome
        FROM lancamentos
        LEFT JOIN categorias ON lancamentos.categoria_id = categorias.id
        WHERE lancamentos.tenant_id = ?
    """
    params = [tenant_id]

    if tipo:
        query += " AND lancamentos.tipo = ?"
        params.append(tipo)
    if esfera and esfera != "Todas":
        query += " AND lancamentos.esfera = ?"
        params.append(esfera)
    if mes_ano:
        primeiro_dia_mes = f"{mes_ano}-01"
        if incluir_atrasados_anteriores:
            query += " AND (strftime('%Y-%m', lancamentos.vencimento) = ? OR (lancamentos.vencimento < ? AND lancamentos.status NOT IN ('Pago', 'Recebido')))"
            params.extend([mes_ano, primeiro_dia_mes])
        else:
            query += " AND strftime('%Y-%m', lancamentos.vencimento) = ?"
            params.append(mes_ano)

    query += " ORDER BY lancamentos.vencimento DESC, lancamentos.id DESC"
    lancamentos = conexao.execute(query, params).fetchall()
    conexao.close()
    return lancamentos


def buscar_lancamento(tenant_id, id_lancamento):
    conexao = conectar()
    lancamento = conexao.execute(
        "SELECT * FROM lancamentos WHERE id = ? AND tenant_id = ?", (id_lancamento, tenant_id)
    ).fetchone()
    conexao.close()
    return lancamento


def inserir_lancamento(tenant_id, dados):
    conexao = conectar()
    freq = dados.get("frequencia_recorrencia") or ("Mensal" if dados.get("recorrente") == 1 else "Nenhuma")
    recorrente_val = 1 if freq != "Nenhuma" else 0

    cursor = conexao.execute("""
        INSERT INTO lancamentos (
            tenant_id, descricao, tipo, esfera, categoria_id, valor, vencimento,
            data_pagamento, status, forma_pagamento, recorrente, frequencia_recorrencia, observacoes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tenant_id, dados["descricao"], dados["tipo"], dados["esfera"], dados.get("categoria_id"),
        dados["valor"], dados["vencimento"], dados.get("data_pagamento"),
        dados.get("status", "Pendente"), dados.get("forma_pagamento"),
        recorrente_val, freq, dados.get("observacoes")
    ))
    conexao.commit()
    id_novo = cursor.lastrowid
    conexao.close()
    return id_novo


def atualizar_lancamento(tenant_id, id_lancamento, dados):
    conexao = conectar()
    freq = dados.get("frequencia_recorrencia") or ("Mensal" if dados.get("recorrente") == 1 else "Nenhuma")
    recorrente_val = 1 if freq != "Nenhuma" else 0

    conexao.execute("""
        UPDATE lancamentos SET
            descricao = ?, tipo = ?, esfera = ?, categoria_id = ?, valor = ?,
            vencimento = ?, data_pagamento = ?, status = ?, forma_pagamento = ?,
            recorrente = ?, frequencia_recorrencia = ?, observacoes = ?
        WHERE id = ? AND tenant_id = ?
    """, (
        dados["descricao"], dados["tipo"], dados["esfera"], dados.get("categoria_id"),
        dados["valor"], dados["vencimento"], dados.get("data_pagamento"),
        dados.get("status", "Pendente"), dados.get("forma_pagamento"),
        recorrente_val, freq, dados.get("observacoes"), id_lancamento, tenant_id
    ))
    conexao.commit()
    conexao.close()


def alternar_status_lancamento(tenant_id, id_lancamento, novo_status, data_pagto=None):
    conexao = conectar()
    if novo_status == "Pago":
        conexao.execute(
            "UPDATE lancamentos SET status = ?, data_pagamento = ? WHERE id = ? AND tenant_id = ?",
            (novo_status, data_pagto, id_lancamento, tenant_id)
        )
    else:
        conexao.execute(
            "UPDATE lancamentos SET status = ?, data_pagamento = NULL WHERE id = ? AND tenant_id = ?",
            (novo_status, id_lancamento, tenant_id)
        )
    conexao.commit()
    conexao.close()


def excluir_lancamento(tenant_id, id_lancamento):
    conexao = conectar()
    conexao.execute("DELETE FROM lancamentos WHERE id = ? AND tenant_id = ?", (id_lancamento, tenant_id))
    conexao.commit()
    conexao.close()


def buscar_lancamento_por_referencia(tenant_id, referencia_id):
    if not referencia_id:
        return None
    conexao = conectar()
    ref_tag = f"%ID Ref: {referencia_id}%"
    lancamento = conexao.execute(
        "SELECT * FROM lancamentos WHERE tenant_id = ? AND observacoes LIKE ?", (tenant_id, ref_tag)
    ).fetchone()
    conexao.close()
    return lancamento


def excluir_lancamentos_detalhados_clinica(tenant_id):
    conexao = conectar()
    conexao.execute(
        "DELETE FROM lancamentos WHERE tenant_id = ? AND observacoes LIKE '%ID Ref: clinic_pg_%'",
        (tenant_id,)
    )
    conexao.commit()
    conexao.close()


# ===== USUÁRIOS =====

def buscar_usuario_por_email(email, tenant_id=None):
    """Busca um usuário pelo email. Se tenant_id for informado, restringe a busca
    a esse tenant (uso normal dentro do app). Sem tenant_id, é usado apenas no
    momento do login, quando ainda não sabemos a qual tenant o usuário pertence."""
    conexao = conectar()
    if tenant_id is not None:
        usuario = conexao.execute(
            "SELECT * FROM usuarios WHERE email = ? AND tenant_id = ?", (email, tenant_id)
        ).fetchone()
    else:
        usuario = conexao.execute(
            "SELECT * FROM usuarios WHERE email = ? ORDER BY id ASC LIMIT 1", (email,)
        ).fetchone()
    conexao.close()
    return usuario


def buscar_usuario_por_id(tenant_id, id_usuario):
    conexao = conectar()
    usuario = conexao.execute(
        "SELECT * FROM usuarios WHERE id = ? AND tenant_id = ?", (id_usuario, tenant_id)
    ).fetchone()
    conexao.close()
    return usuario


def criar_usuario(tenant_id, nome, email, senha_hash, saudacao=None, is_admin=0, deve_trocar_senha=0):
    conexao = conectar()
    conexao.execute(
        "INSERT INTO usuarios (tenant_id, nome, email, senha_hash, saudacao, is_admin, deve_trocar_senha) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tenant_id, nome, email, senha_hash, saudacao, is_admin, deve_trocar_senha)
    )
    conexao.commit()
    conexao.close()


def atualizar_senha_usuario(tenant_id, id_usuario, nova_senha_hash):
    """Atualiza a senha e sempre desliga a exigência de troca obrigatória,
    já que o usuário acabou de definir uma senha nova por conta própria."""
    conexao = conectar()
    conexao.execute(
        "UPDATE usuarios SET senha_hash = ?, deve_trocar_senha = 0 WHERE id = ? AND tenant_id = ?",
        (nova_senha_hash, id_usuario, tenant_id)
    )
    conexao.commit()
    conexao.close()


def marcar_deve_trocar_senha(tenant_id, id_usuario, valor=1):
    """Liga (valor=1) ou desliga (valor=0) a exigência de troca de senha no
    próximo login para um usuário específico."""
    conexao = conectar()
    conexao.execute(
        "UPDATE usuarios SET deve_trocar_senha = ? WHERE id = ? AND tenant_id = ?",
        (1 if valor else 0, id_usuario, tenant_id)
    )
    conexao.commit()
    conexao.close()


def atualizar_perfil_usuario(tenant_id, id_usuario, nome, saudacao, foto_perfil=None):
    conexao = conectar()
    if foto_perfil is not None:
        conexao.execute(
            "UPDATE usuarios SET nome = ?, saudacao = ?, foto_perfil = ? WHERE id = ? AND tenant_id = ?",
            (nome, saudacao, foto_perfil, id_usuario, tenant_id)
        )
    else:
        conexao.execute(
            "UPDATE usuarios SET nome = ?, saudacao = ? WHERE id = ? AND tenant_id = ?",
            (nome, saudacao, id_usuario, tenant_id)
        )
    conexao.commit()
    conexao.close()
