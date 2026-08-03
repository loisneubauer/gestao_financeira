# criar_usuarios.py - Script para cadastrar as usuárias iniciais no Gestão Financeira
import getpass
from werkzeug.security import generate_password_hash
import database

database.criar_tabelas()

USUARIOS_PARA_CRIAR = [
    {"nome": "Lois", "email": "loisneubauer@gmail.com", "saudacao": "Sr."},
    {"nome": "Laila", "email": "lailaacupuntura@gmail.com", "saudacao": "Dra."},
]

print("=== Criar Usuárias do Gestão Financeira ===")
for u in USUARIOS_PARA_CRIAR:
    usuario_existente = database.buscar_usuario_por_email(u["email"])
    if usuario_existente:
        print(f"  Usuário {u['email']} já existe, pulando.")
        continue

    senha = getpass.getpass(f"Digite a senha inicial para {u['nome']} ({u['email']}): ")
    if not senha:
        print("  Senha vazia. Usuário não criado.")
        continue

    senha_hash = generate_password_hash(senha)
    database.criar_usuario(u["nome"], u["email"], senha_hash, u.get("saudacao"))
    print(f"  Usuário criado com sucesso: {u['nome']} ({u['email']})")


print("Concluído!")
