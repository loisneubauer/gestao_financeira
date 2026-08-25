# database.py - Módulo de Banco de Dados do Gestão Financeira
import sqlite3
import os
import re
import unicodedata

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


# Escala padrão de classificação de gastos, definida com a Lois em 25/08/2026.
# Vai do que não se corta ao que não deveria ter acontecido; a "linha de corte"
# num mês apertado fica entre o nível 2 e o 3.
NIVEIS_IMPORTANCIA_PADRAO = [
    (1, "Indispensável", "Crítico",
     "Sobrevivência. Sem isso, o negócio para de funcionar ou sua vida básica é comprometida. Não pode ser cortado.",
     "Aluguel, energia elétrica, impostos, licenças.",
     "Moradia, alimentação básica, plano de saúde."),
    (2, "Importante", "Estratégico",
     "Traz retorno ou grande melhoria, mas o valor pode ser ajustado ou negociado se houver crise.",
     "Marketing, atualização de equipamentos, softwares bons.",
     "Educação, academia, manutenções preventivas."),
    (3, "Desejável", "Conforto",
     "Melhora a experiência, mas não é vital. É a primeira linha de corte em meses de aperto financeiro.",
     "Café premium para pacientes, decoração extra.",
     "Streaming, restaurantes, viagens de lazer."),
    (4, "Evitável", "Impulso",
     "Gasto sem planejamento, emocional ou que não trouxe retorno nem utilidade real. Desperdício.",
     "Compras de materiais em excesso, multas por atraso.",
     "Compras por impulso, assinaturas não utilizadas."),
]


def _migrar_niveis_importancia_para_multi_tenant(conexao):
    """Converte a tabela de níveis global (primeira versão) em uma por
    organização, replicando o que estava lá para cada tenant.

    A versão global durou pouco, mas se alguém já tinha editado um nível, essa
    edição vira o ponto de partida de todas as organizações — melhor do que
    descartar e voltar ao padrão sem avisar."""
    if not _tabela_existe(conexao, "niveis_importancia"):
        return
    if _coluna_existe(conexao, "niveis_importancia", "tenant_id"):
        return

    antigos = conexao.execute(
        "SELECT nivel, nome, apelido, significado, exemplo_empresa, exemplo_casa "
        "FROM niveis_importancia ORDER BY nivel"
    ).fetchall()

    conexao.execute("DROP TABLE niveis_importancia")
    conexao.execute("""
        CREATE TABLE niveis_importancia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants (id),
            nivel INTEGER NOT NULL,
            nome TEXT NOT NULL,
            apelido TEXT,
            significado TEXT,
            exemplo_empresa TEXT,
            exemplo_casa TEXT,
            UNIQUE (tenant_id, nivel)
        )
    """)

    tenants = [linha["id"] for linha in conexao.execute("SELECT id FROM tenants").fetchall()]
    for tid in tenants:
        for linha in antigos:
            conexao.execute("""
                INSERT OR IGNORE INTO niveis_importancia
                    (tenant_id, nivel, nome, apelido, significado, exemplo_empresa, exemplo_casa)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (tid, linha["nivel"], linha["nome"], linha["apelido"],
                  linha["significado"], linha["exemplo_empresa"], linha["exemplo_casa"]))


def _semear_niveis_importancia(conexao, tenant_id=None):
    """Dá a cada organização os 4 níveis padrão, se ela ainda não os tiver.

    Cada organização tem a sua própria tabela: a escala que a Lois desenhou é o
    padrão de fábrica, e ajustar para um cliente não mexe nos outros.

    Nunca sobrescreve o que já está lá — uma edição feita na tela não pode ser
    desfeita no próximo reinício do app. Sem tenant_id, semeia todas."""
    if tenant_id is None:
        alvos = [linha["id"] for linha in conexao.execute("SELECT id FROM tenants").fetchall()]
    else:
        alvos = [tenant_id]

    for tid in alvos:
        for nivel, nome, apelido, significado, ex_empresa, ex_casa in NIVEIS_IMPORTANCIA_PADRAO:
            conexao.execute("""
                INSERT OR IGNORE INTO niveis_importancia
                    (tenant_id, nivel, nome, apelido, significado, exemplo_empresa, exemplo_casa)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (tid, nivel, nome, apelido, significado, ex_empresa, ex_casa))


# ===== SALDO DE CAIXA =====

ESFERAS_DE_CAIXA = ["Empresa", "Casa"]

# Qual data define em que mês o dinheiro conta. Fica num lugar só, de propósito:
# a visão por COMPETÊNCIA (a que mês o gasto pertence, independente de quando o
# dinheiro se moveu) pode ser útil no futuro, e quando for pedida o trabalho
# deve ser a tela, não reescrever consulta espalhada pelo código.
#
#   caixa       -> quando o dinheiro se moveu de verdade (o que o extrato mostra)
#   competencia -> a que mês o lançamento pertence, pago ou não
#
# Hoje só "caixa" é usado. O outro ramo existe como seam, não como recurso.
_BASES_DE_DATA = {
    "caixa": {
        "expressao": "COALESCE(lancamentos.data_pagamento, lancamentos.vencimento)",
        "so_efetivados": True,
    },
    "competencia": {
        "expressao": "lancamentos.vencimento",
        "so_efetivados": False,
    },
}


def obter_saldos_iniciais(tenant_id):
    """Devolve {esfera: {'valor': float, 'data_referencia': str}}. Esfera sem
    saldo definido simplesmente não aparece no dicionário."""
    conexao = conectar()
    linhas = conexao.execute(
        "SELECT esfera, valor, data_referencia FROM saldos_iniciais WHERE tenant_id = ?",
        (tenant_id,)
    ).fetchall()
    conexao.close()
    return {
        l["esfera"]: {"valor": float(l["valor"] or 0), "data_referencia": l["data_referencia"]}
        for l in linhas
    }


def definir_saldo_inicial(tenant_id, esfera, valor, data_referencia):
    """Grava (ou substitui) o ponto de partida do caixa de uma esfera."""
    conexao = conectar()
    conexao.execute("""
        INSERT INTO saldos_iniciais (tenant_id, esfera, valor, data_referencia)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (tenant_id, esfera)
        DO UPDATE SET valor = excluded.valor, data_referencia = excluded.data_referencia
    """, (tenant_id, esfera, float(valor or 0), data_referencia))
    conexao.commit()
    conexao.close()


def somar_movimentacoes(tenant_id, esfera=None, data_inicio=None, data_fim=None, base="caixa"):
    """Soma quanto entrou e quanto saiu num intervalo, devolvendo (entrou, saiu).

    Com base="caixa" (padrão), conta só o que foi efetivado — status Pago ou
    Recebido — e usa a data em que o dinheiro se moveu. O COALESCE existe porque
    lançamentos vindos do webhook da clínica chegam com status Pago mas sem
    data_pagamento; sem o fallback eles sumiriam do saldo.

    data_inicio=None significa "desde sempre". esfera None ou "Todas" não filtra.
    """
    config = _BASES_DE_DATA.get(base) or _BASES_DE_DATA["caixa"]
    data_expr = config["expressao"]

    condicoes = ["lancamentos.tenant_id = ?"]
    params = [tenant_id]

    if config["so_efetivados"]:
        condicoes.append("lancamentos.status IN ('Pago', 'Recebido')")
    if esfera and esfera != "Todas":
        condicoes.append("lancamentos.esfera = ?")
        params.append(esfera)
    if data_inicio:
        condicoes.append(f"{data_expr} >= ?")
        params.append(data_inicio)
    if data_fim:
        condicoes.append(f"{data_expr} <= ?")
        params.append(data_fim)

    conexao = conectar()
    linha = conexao.execute(f"""
        SELECT
            COALESCE(SUM(CASE WHEN lancamentos.tipo = 'Receber' THEN lancamentos.valor END), 0) AS entrou,
            COALESCE(SUM(CASE WHEN lancamentos.tipo = 'Pagar'   THEN lancamentos.valor END), 0) AS saiu
        FROM lancamentos
        WHERE {' AND '.join(condicoes)}
    """, params).fetchone()
    conexao.close()
    return float(linha["entrou"] or 0), float(linha["saiu"] or 0)


def data_do_primeiro_lancamento(tenant_id, esfera=None):
    """Data de caixa mais antiga da organização — usada quando não há saldo
    inicial informado, para saber de onde começar a somar."""
    condicoes = ["tenant_id = ?"]
    params = [tenant_id]
    if esfera and esfera != "Todas":
        condicoes.append("esfera = ?")
        params.append(esfera)
    conexao = conectar()
    linha = conexao.execute(
        f"SELECT MIN(COALESCE(data_pagamento, vencimento)) AS inicio "
        f"FROM lancamentos WHERE {' AND '.join(condicoes)}",
        params
    ).fetchone()
    conexao.close()
    return linha["inicio"] if linha else None


# ===== NÍVEIS DE IMPORTÂNCIA =====

def listar_niveis_importancia(tenant_id):
    """Devolve os níveis da organização, do mais essencial (1) ao mais
    evitável (4)."""
    conexao = conectar()
    niveis = conexao.execute(
        "SELECT * FROM niveis_importancia WHERE tenant_id = ? ORDER BY nivel ASC",
        (tenant_id,)
    ).fetchall()
    conexao.close()
    return niveis


def atualizar_nivel_importancia(tenant_id, nivel, nome, apelido, significado, exemplo_empresa, exemplo_casa):
    """Edita o rótulo e os textos de um nível DESTA organização. O número do
    nível nunca muda — é ele que os lançamentos guardam."""
    conexao = conectar()
    conexao.execute("""
        UPDATE niveis_importancia
           SET nome = ?, apelido = ?, significado = ?, exemplo_empresa = ?, exemplo_casa = ?
         WHERE nivel = ? AND tenant_id = ?
    """, (nome, apelido, significado, exemplo_empresa, exemplo_casa, nivel, tenant_id))
    conexao.commit()
    conexao.close()


def restaurar_niveis_importancia_padrao(tenant_id):
    """Volta os 4 níveis DESTA organização ao padrão de fábrica."""
    conexao = conectar()
    for nivel, nome, apelido, significado, ex_empresa, ex_casa in NIVEIS_IMPORTANCIA_PADRAO:
        conexao.execute("""
            UPDATE niveis_importancia
               SET nome = ?, apelido = ?, significado = ?, exemplo_empresa = ?, exemplo_casa = ?
             WHERE nivel = ? AND tenant_id = ?
        """, (nome, apelido, significado, ex_empresa, ex_casa, nivel, tenant_id))
    conexao.commit()
    conexao.close()


def contar_lancamentos_por_nivel(tenant_id):
    """Quantos lançamentos DESTA organização usam cada nível — mostrado na tela
    de edição, para saber o que está sendo mexido antes de renomear."""
    conexao = conectar()
    linhas = conexao.execute("""
        SELECT importancia_nivel AS nivel, COUNT(*) AS total
          FROM lancamentos
         WHERE tenant_id = ? AND importancia_nivel IS NOT NULL
      GROUP BY importancia_nivel
    """, (tenant_id,)).fetchall()
    conexao.close()
    return {linha["nivel"]: linha["total"] for linha in linhas}


def _slug_a_partir_do_nome(nome):
    """Converte "Acupuntura Bem-estar" em "acupuntura-bem-estar".
    Tira acentos antes, para "São" virar "sao" e não "s-o"."""
    sem_acento = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9-]+", "-", sem_acento.strip().lower()).strip("-")


def _renomear_slug_padrao(conexao):
    """Troca o slug automático "padrao" por um derivado do nome da organização.

    O slug virou dado visível: é o que se digita no campo "Organização" da tela
    de login. "padrao" foi gerado pela migração de multi-tenancy, não escolhido
    por ninguém, e pedir para alguém digitá-lo seria confuso. Organizações com
    slug próprio nunca são tocadas."""
    alvos = conexao.execute("SELECT id, nome FROM tenants WHERE slug = 'padrao'").fetchall()
    if not alvos:
        return

    ocupados = {linha["slug"] for linha in conexao.execute("SELECT slug FROM tenants").fetchall()}

    for tenant in alvos:
        base = _slug_a_partir_do_nome(tenant["nome"])
        if not base:
            continue  # nome só de símbolos: melhor manter "padrao" do que gerar lixo

        candidato = base
        sufixo = 2
        while candidato in ocupados and candidato != "padrao":
            candidato = f"{base}-{sufixo}"
            sufixo += 1

        conexao.execute("UPDATE tenants SET slug = ? WHERE id = ?", (candidato, tenant["id"]))
        ocupados.discard("padrao")
        ocupados.add(candidato)


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
            importancia TEXT, -- LEGADO: guardava o nome do nível como texto.
                              -- Substituída por importancia_nivel; mantida só
                              -- como rede de segurança da migração.
            importancia_nivel INTEGER, -- 1..4 (ver tabela niveis_importancia) ou NULL
            FOREIGN KEY (categoria_id) REFERENCES categorias (id)
        )
    """)

    # Tabela de Níveis de Importância (classificação de gastos).
    # O número do nível (1..4) é a chave estável — é ele que fica gravado em
    # lancamentos.importancia_nivel. O nome é só rótulo e pode ser editado sem
    # que nenhum lançamento já classificado se perca.
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS niveis_importancia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants (id),
            nivel INTEGER NOT NULL,         -- 1 = mais essencial ... 4 = desperdício
            nome TEXT NOT NULL,             -- "Indispensável"
            apelido TEXT,                   -- "Crítico"
            significado TEXT,               -- o que significa na prática
            exemplo_empresa TEXT,
            exemplo_casa TEXT,
            UNIQUE (tenant_id, nivel)
        )
    """)

    # Saldo inicial de caixa de cada esfera. O sistema não conhece a vida da
    # organização antes dos primeiros lançamentos: sem um ponto de partida
    # informado, todo saldo acumulado nasce errado.
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS saldos_iniciais (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER NOT NULL REFERENCES tenants (id),
            esfera TEXT NOT NULL,            -- 'Empresa' ou 'Casa'
            valor REAL NOT NULL DEFAULT 0,
            data_referencia TEXT NOT NULL,   -- AAAA-MM-DD: a partir de quando vale
            UNIQUE (tenant_id, esfera)
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

    # Migração: lancamentos.importancia guardava o NOME do nível como texto
    # ("Impulso"). Isso travava a escala: renomear um nível deixaria órfão todo
    # lançamento gravado com o nome antigo. Agora grava o número do nível.
    if _tabela_existe(conexao, "lancamentos") and not _coluna_existe(conexao, "lancamentos", "importancia_nivel"):
        conexao.execute("ALTER TABLE lancamentos ADD COLUMN importancia_nivel INTEGER")
        # A escala antiga tinha os mesmos 4 degraus, na mesma ordem.
        for nome_antigo, nivel in [("Imprescindível", 1), ("Necessário", 2),
                                   ("Supérfluo", 3), ("Impulso", 4)]:
            conexao.execute(
                "UPDATE lancamentos SET importancia_nivel = ? WHERE importancia = ?",
                (nivel, nome_antigo)
            )
        # A coluna `importancia` (texto) fica no banco de propósito, como rede
        # de segurança para conferir a conversão. Nada mais escreve nela.

    _migrar_niveis_importancia_para_multi_tenant(conexao)
    _semear_niveis_importancia(conexao)

    # Migração: o slug automático "padrao" vira o nome real da organização,
    # já que agora ele é digitado na tela de login.
    _renomear_slug_padrao(conexao)

    # Migração: coluna importancia (classificação do gasto). Fica NULL nos
    # lançamentos antigos — "Não classificado" no relatório, sem chute.
    if _tabela_existe(conexao, "lancamentos") and not _coluna_existe(conexao, "lancamentos", "importancia"):
        conexao.execute("ALTER TABLE lancamentos ADD COLUMN importancia TEXT")

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
    id_novo = cursor.lastrowid
    # A organização já nasce com a escala padrão de importância, para a tela
    # nunca aparecer vazia — e ela pode ajustar a sua sem afetar as outras.
    _semear_niveis_importancia(conexao, id_novo)
    conexao.commit()
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
            data_pagamento, status, forma_pagamento, recorrente, frequencia_recorrencia,
            observacoes, importancia_nivel
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tenant_id, dados["descricao"], dados["tipo"], dados["esfera"], dados.get("categoria_id"),
        dados["valor"], dados["vencimento"], dados.get("data_pagamento"),
        dados.get("status", "Pendente"), dados.get("forma_pagamento"),
        recorrente_val, freq, dados.get("observacoes"), dados.get("importancia_nivel")
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
            recorrente = ?, frequencia_recorrencia = ?, observacoes = ?, importancia_nivel = ?
        WHERE id = ? AND tenant_id = ?
    """, (
        dados["descricao"], dados["tipo"], dados["esfera"], dados.get("categoria_id"),
        dados["valor"], dados["vencimento"], dados.get("data_pagamento"),
        dados.get("status", "Pendente"), dados.get("forma_pagamento"),
        recorrente_val, freq, dados.get("observacoes"), dados.get("importancia_nivel"),
        id_lancamento, tenant_id
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


def excluir_lancamentos_da_clinica(tenant_id):
    """Apaga TODOS os lançamentos gerados pela integração com a clínica
    (qualquer 'ID Ref: clinic_...'), devolvendo quantos foram removidos.

    A clínica é a fonte da verdade dessas receitas, então cada sincronização
    reescreve o conjunto inteiro em vez de só atualizar linha a linha: assim
    uma linha que deixou de existir lá (um mês que saiu do atraso, uma projeção
    que virou histórico) não fica órfã aqui somando valor errado para sempre.

    Só toca no que veio do webhook — lançamentos criados à mão na tela não têm
    a marca 'ID Ref:' e são preservados."""
    conexao = conectar()
    cursor = conexao.execute(
        "DELETE FROM lancamentos WHERE tenant_id = ? AND observacoes LIKE '%ID Ref: clinic_%'",
        (tenant_id,)
    )
    conexao.commit()
    removidos = cursor.rowcount
    conexao.close()
    return removidos


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


def buscar_usuario_por_id_global(id_usuario):
    """Busca um usuário só pelo id, sem filtrar por tenant. Uso restrito ao
    fluxo de redefinição de senha, onde o token carrega o id do usuário e o
    tenant é lido do próprio registro encontrado. Em qualquer outro lugar use
    buscar_usuario_por_id, que exige o tenant."""
    conexao = conectar()
    usuario = conexao.execute("SELECT * FROM usuarios WHERE id = ?", (id_usuario,)).fetchone()
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


def alternar_admin_usuario(tenant_id, id_usuario, valor):
    """Liga (valor=1) ou desliga (valor=0) o acesso de admin de plataforma
    (área /admin/tenants, que enxerga TODAS as organizações) para um usuário."""
    conexao = conectar()
    conexao.execute(
        "UPDATE usuarios SET is_admin = ? WHERE id = ? AND tenant_id = ?",
        (1 if valor else 0, id_usuario, tenant_id)
    )
    conexao.commit()
    conexao.close()


def contar_admins():
    """Conta quantos usuários (em qualquer organização) têm is_admin=1.
    Usado para nunca deixar a plataforma sem nenhum admin."""
    conexao = conectar()
    total = conexao.execute("SELECT COUNT(*) AS total FROM usuarios WHERE is_admin = 1").fetchone()["total"]
    conexao.close()
    return total


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


def atualizar_usuario_admin(tenant_id, id_usuario, nome, email):
    """Atualiza nome/email de um usuário a partir da tela de administração
    (diferente de atualizar_perfil_usuario, que é o próprio usuário editando
    seu perfil/foto). Não mexe em senha."""
    conexao = conectar()
    conexao.execute(
        "UPDATE usuarios SET nome = ?, email = ? WHERE id = ? AND tenant_id = ?",
        (nome, email, id_usuario, tenant_id)
    )
    conexao.commit()
    conexao.close()


def excluir_usuario(tenant_id, id_usuario):
    conexao = conectar()
    conexao.execute("DELETE FROM usuarios WHERE id = ? AND tenant_id = ?", (id_usuario, tenant_id))
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
