# criar_usuarios.py - Script para cadastrar as usuárias iniciais no Gestão Financeira
import getpass
from werkzeug.security import generate_password_hash
import database

database.criar_tabelas()

# Slug do tenant que vai receber os usuários abaixo. Se não existir, é criado.
TENANT_SLUG = "padrao"
TENANT_NOME = "Acupuntura Bem-estar"

USUARIOS_PARA_CRIAR = [
    {"nome": "Lois", "email": "loisneubauer@gmail.com", "saudacao": "Sr.", "is_admin": 1},
    {"nome": "Laila", "email": "lailaacupuntura@gmail.com", "saudacao": "Dra.", "is_admin": 0},
]

print("=== Criar Usuárias do Gestão Financeira ===")

tenant = database.buscar_tenant_por_slug(TENANT_SLUG)
if not tenant:
    # Se não existe nenhum tenant ainda (instalação nova do zero), usa o
    # primeiro tenant existente; senão, cria o tenant padrão.
    tenants_existentes = database.listar_tenants()
    if tenants_existentes:
        tenant_id = tenants_existentes[0]["id"]
        print(f"  Usando tenant existente: {tenants_existentes[0]['nome']} (id={tenant_id})")
    else:
        tenant_id = database.criar_tenant(TENANT_NOME, TENANT_SLUG)
        print(f"  Tenant '{TENANT_NOME}' criado (id={tenant_id}).")
else:
    tenant_id = tenant["id"]
    print(f"  Usando tenant existente: {tenant['nome']} (id={tenant_id})")

for u in USUARIOS_PARA_CRIAR:
    usuario_existente = database.buscar_usuario_por_email(u["email"], tenant_id=tenant_id)
    if usuario_existente:
        print(f"  Usuário {u['email']} já existe neste tenant, pulando.")
        continue

    senha = getpass.getpass(f"Digite a senha inicial para {u['nome']} ({u['email']}): ")
    if not senha:
        print("  Senha vazia. Usuário não criado.")
        continue

    senha_hash = generate_password_hash(senha)
    database.criar_usuario(tenant_id, u["nome"], u["email"], senha_hash, u.get("saudacao"), u.get("is_admin", 0))
    print(f"  Usuário criado com sucesso: {u['nome']} ({u['email']})")


print("Concluído!")
