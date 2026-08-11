# Roteiro — Subir o Multi-Tenancy pro PythonAnywhere (via Git)

Já criei o `.gitignore` do projeto (exclui `venv/`, `financeiro.db`, backups, `.secret_key` e uploads — nada disso deve ir pro GitHub).

## Parte 1 — Criar o repositório no GitHub

1. Acesse [github.com/new](https://github.com/new).
2. Nome do repositório: `sistema-financeiro` (ou o que preferir).
3. Marque **Private**.
4. **Não** marque "Add a README", "Add .gitignore" nem "Choose a license" — o projeto já existe localmente, vamos subir ele como está.
5. Clique em "Create repository" e deixe a página aberta — ela mostra a URL do repositório (algo como `https://github.com/SEU_USUARIO/sistema-financeiro.git`).

## Parte 2 — Subir o código do seu Mac pro GitHub

No terminal (Mac ou VS Code), na pasta do projeto:

```bash
cd ~/Documents/Sistema_Financeiro
git init
git add .
git commit -m "Versão inicial com multi-tenancy"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/sistema-financeiro.git
git push -u origin main
```

Troque `SEU_USUARIO` pela URL real que o GitHub te mostrou. Na primeira vez, o GitHub vai pedir autenticação — se pedir senha e não aceitar sua senha normal da conta, é porque o GitHub exige um "Personal Access Token" no lugar da senha (eles descontinuaram login por senha simples no `git push`). Nesse caso:

1. Vá em GitHub → foto do perfil → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token.
2. Marque o escopo `repo`, gere, copie o token.
3. Quando o terminal pedir senha, cole esse token (ele não aparece na tela enquanto você cola, é normal).

Depois do primeiro `git push` bem-sucedido, confirme atualizando a página do repositório no GitHub — os arquivos `.py` e `templates/` devem aparecer lá.

## Parte 3 — Conectar o PythonAnywhere ao repositório

No **console Bash do PythonAnywhere**:

```bash
cd ~/caminho/da/pasta/do/projeto   # ajuste para onde o app.py já está publicado
git init
git remote add origin https://github.com/SEU_USUARIO/sistema-financeiro.git
git fetch origin
git reset --hard origin/main
```

Por que `git reset --hard` e não `git clone` numa pasta nova: assim o `financeiro.db`, o `.secret_key` e a pasta `venv` que já existem lá em produção **não são apagados** (eles não fazem parte do repositório, graças ao `.gitignore`) — só os arquivos de código (`.py`, `templates/`) são atualizados para bater com o que está no GitHub.

Depois disso:

```bash
# faça um backup do banco de produção antes de reiniciar o app
cp financeiro.db financeiro_backup_pre_multitenancy_$(date +%Y%m%d_%H%M).db

# se o requirements.txt mudou, reinstale as dependências no ambiente virtual usado pelo PythonAnywhere
pip install -r requirements.txt
```

## Parte 4 — Reiniciar o app e migrar

1. Vá na aba **Web** do PythonAnywhere e clique em **Reload**.
2. A migração roda sozinha no primeiro carregamento (mesma lógica que testamos localmente).
3. No console Bash, confirme:
```bash
sqlite3 financeiro.db "SELECT id, nome, slug FROM tenants;"
sqlite3 financeiro.db "SELECT id, tenant_id, nome, email, is_admin FROM usuarios;"
```
4. Torne você admin lá também:
```bash
sqlite3 financeiro.db "UPDATE usuarios SET is_admin = 1 WHERE email = 'loisneubauer@gmail.com';"
```
5. Acesse o site publicado, faça login e confirme que "Organizações" aparece só para você.

## Parte 5 — Próximas atualizações de código

A partir de agora, todo ajuste no projeto segue esse fluxo:

```bash
# no Mac, depois de editar o código
git add .
git commit -m "descrição da mudança"
git push

# no PythonAnywhere
git pull
# Reload na aba Web
```
