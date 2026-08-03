# database.py - Módulo de Banco de Dados do Gestão Financeira
import sqlite3
import os

NOME_DO_BANCO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "financeiro.db")


def conectar():
    """Abre uma conexão com o banco de dados financeiro.db."""
    conexao = sqlite3.connect(NOME_DO_BANCO)
    conexao.row_factory = sqlite3.Row
    return conexao


def criar_tabelas():
    """Cria as tabelas do sistema financeiro caso ainda não existam."""
    conexao = conectar()
    
    # Tabela de Categorias (ex: Aluguel, Luz, Insumos, Consultas)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS categorias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL,      -- 'Pagar' ou 'Receber'
            esfera TEXT NOT NULL     -- 'Empresa', 'Casa' ou 'Ambos'
        )
    """)
    
    # Tabela de Lançamentos (Despesas e Receitas)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS lancamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            observacoes TEXT,
            FOREIGN KEY (categoria_id) REFERENCES categorias (id)
        )
    """)

    # Tabela de Usuários para Login e Perfil
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL,
            foto_perfil TEXT,
            saudacao TEXT
        )
    """)

    colunas = [row[1] for row in conexao.execute("PRAGMA table_info(lancamentos)").fetchall()]
    if "frequencia_recorrencia" not in colunas:
        conexao.execute("ALTER TABLE lancamentos ADD COLUMN frequencia_recorrencia TEXT DEFAULT 'Nenhuma'")
        conexao.execute("UPDATE lancamentos SET frequencia_recorrencia = 'Mensal' WHERE recorrente = 1")

    conexao.commit()
    conexao.close()



# ===== CATEGORIAS =====

def listar_categorias(tipo=None, esfera=None):
    conexao = conectar()
    query = "SELECT * FROM categorias WHERE 1=1"
    params = []
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



def inserir_categoria(nome, tipo, esfera):
    conexao = conectar()
    conexao.execute(
        "INSERT INTO categorias (nome, tipo, esfera) VALUES (?, ?, ?)",
        (nome, tipo, esfera)
    )
    conexao.commit()
    conexao.close()


def atualizar_categoria(id_categoria, nome, tipo, esfera):
    conexao = conectar()
    conexao.execute(
        "UPDATE categorias SET nome = ?, tipo = ?, esfera = ? WHERE id = ?",
        (nome, tipo, esfera, id_categoria)
    )
    conexao.commit()
    conexao.close()


def excluir_categoria(id_categoria):
    conexao = conectar()
    conexao.execute("DELETE FROM categorias WHERE id = ?", (id_categoria,))
    conexao.commit()
    conexao.close()



# ===== LANÇAMENTOS =====

def listar_lancamentos(tipo=None, esfera=None, mes_ano=None, incluir_atrasados_anteriores=True):
    """
    Busca lançamentos filtrados por tipo ('Pagar'/'Receber'), esfera ('Empresa'/'Casa')
    e mês de referência ('AAAA-MM'). Inclui por padrão contas em atraso de meses anteriores.
    """
    conexao = conectar()
    query = """
        SELECT lancamentos.*, categorias.nome AS categoria_nome
        FROM lancamentos
        LEFT JOIN categorias ON lancamentos.categoria_id = categorias.id
        WHERE 1=1
    """
    params = []
    
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



def buscar_lancamento(id_lancamento):
    conexao = conectar()
    lancamento = conexao.execute("SELECT * FROM lancamentos WHERE id = ?", (id_lancamento,)).fetchone()
    conexao.close()
    return lancamento


def inserir_lancamento(dados):
    conexao = conectar()
    freq = dados.get("frequencia_recorrencia") or ("Mensal" if dados.get("recorrente") == 1 else "Nenhuma")
    recorrente_val = 1 if freq != "Nenhuma" else 0

    cursor = conexao.execute("""
        INSERT INTO lancamentos (
            descricao, tipo, esfera, categoria_id, valor, vencimento,
            data_pagamento, status, forma_pagamento, recorrente, frequencia_recorrencia, observacoes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        dados["descricao"], dados["tipo"], dados["esfera"], dados.get("categoria_id"),
        dados["valor"], dados["vencimento"], dados.get("data_pagamento"),
        dados.get("status", "Pendente"), dados.get("forma_pagamento"),
        recorrente_val, freq, dados.get("observacoes")
    ))
    conexao.commit()
    id_novo = cursor.lastrowid
    conexao.close()
    return id_novo


def atualizar_lancamento(id_lancamento, dados):
    conexao = conectar()
    freq = dados.get("frequencia_recorrencia") or ("Mensal" if dados.get("recorrente") == 1 else "Nenhuma")
    recorrente_val = 1 if freq != "Nenhuma" else 0

    conexao.execute("""
        UPDATE lancamentos SET
            descricao = ?, tipo = ?, esfera = ?, categoria_id = ?, valor = ?,
            vencimento = ?, data_pagamento = ?, status = ?, forma_pagamento = ?,
            recorrente = ?, frequencia_recorrencia = ?, observacoes = ?
        WHERE id = ?
    """, (
        dados["descricao"], dados["tipo"], dados["esfera"], dados.get("categoria_id"),
        dados["valor"], dados["vencimento"], dados.get("data_pagamento"),
        dados.get("status", "Pendente"), dados.get("forma_pagamento"),
        recorrente_val, freq, dados.get("observacoes"), id_lancamento
    ))
    conexao.commit()
    conexao.close()



def alternar_status_lancamento(id_lancamento, novo_status, data_pagto=None):
    conexao = conectar()
    if novo_status == "Pago":
        conexao.execute(
            "UPDATE lancamentos SET status = ?, data_pagamento = ? WHERE id = ?",
            (novo_status, data_pagto, id_lancamento)
        )
    else:
        conexao.execute(
            "UPDATE lancamentos SET status = ?, data_pagamento = NULL WHERE id = ?",
            (novo_status, id_lancamento)
        )
    conexao.commit()
    conexao.close()


def excluir_lancamento(id_lancamento):
    conexao = conectar()
    conexao.execute("DELETE FROM lancamentos WHERE id = ?", (id_lancamento,))
    conexao.commit()
    conexao.close()


def buscar_lancamento_por_referencia(referencia_id):
    if not referencia_id:
        return None
    conexao = conectar()
    ref_tag = f"%ID Ref: {referencia_id}%"
    lancamento = conexao.execute(
        "SELECT * FROM lancamentos WHERE observacoes LIKE ?", (ref_tag,)
    ).fetchone()
    conexao.close()
    return lancamento


def excluir_lancamentos_detalhados_clinica():
    conexao = conectar()
    conexao.execute("DELETE FROM lancamentos WHERE observacoes LIKE '%ID Ref: clinic_pg_%'")
    conexao.commit()
    conexao.close()


# ===== USUÁRIOS =====



def buscar_usuario_por_email(email):
    conexao = conectar()
    usuario = conexao.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    conexao.close()
    return usuario


def buscar_usuario_por_id(id_usuario):
    conexao = conectar()
    usuario = conexao.execute("SELECT * FROM usuarios WHERE id = ?", (id_usuario,)).fetchone()
    conexao.close()
    return usuario


def criar_usuario(nome, email, senha_hash, saudacao=None):
    conexao = conectar()
    conexao.execute(
        "INSERT INTO usuarios (nome, email, senha_hash, saudacao) VALUES (?, ?, ?, ?)",
        (nome, email, senha_hash, saudacao)
    )
    conexao.commit()
    conexao.close()


def atualizar_senha_usuario(id_usuario, nova_senha_hash):
    conexao = conectar()
    conexao.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (nova_senha_hash, id_usuario))
    conexao.commit()
    conexao.close()


def atualizar_perfil_usuario(id_usuario, nome, saudacao, foto_perfil=None):
    conexao = conectar()
    if foto_perfil is not None:
        conexao.execute(
            "UPDATE usuarios SET nome = ?, saudacao = ?, foto_perfil = ? WHERE id = ?",
            (nome, saudacao, foto_perfil, id_usuario)
        )
    else:
        conexao.execute(
            "UPDATE usuarios SET nome = ?, saudacao = ? WHERE id = ?",
            (nome, saudacao, id_usuario)
        )
    conexao.commit()
    conexao.close()
