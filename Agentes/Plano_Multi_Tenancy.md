# Avaliação e Plano de Multi-Tenancy — Sistema Financeiro

Decisões já validadas com você: banco compartilhado com `tenant_id`, escala pequena (2 a 10 clientes), provisionamento manual (admin cadastra cada tenant).

---

## 1. Avaliação do estado atual

### 1.1 Divergência entre a documentação e o código

Os arquivos `Documentacao_Financeiro.md` e `Agentes/Documentacao_Financeiro.md` descrevem um sistema de clínica de acupuntura, com tabelas `pagamentos`, `atendimentos`, `fechamentos_mensais`, regras de convênio/Climed etc. Nenhuma dessas tabelas existe em `database.py`. O código real (`app.py`, `database.py`, `calculos.py`) implementa um sistema mais simples de contas a pagar/receber pessoais ou de pequena empresa, com três tabelas: `categorias`, `lancamentos` e `usuarios`.

Como a instrução do projeto é usar o `.md` como referência, isso é um problema: a documentação está desatualizada ou pertence a outra versão/projeto. Antes de qualquer trabalho maior, vale confirmar qual documento é o correto e atualizar o `.md` para refletir o código real — caso contrário, futuras decisões (inclusive este plano) corem o risco de se basear em uma estrutura que não existe.

Este plano foi construído a partir do **código real**, não da documentação, já que é o que efetivamente roda.

### 1.2 Arquitetura atual

- Flask monolítico (`app.py`, 625 linhas), sessão de login única (tabela `usuarios`, sem papel/role).
- Banco único SQLite (`financeiro.db`), sem qualquer coluna de tenant/empresa.
- Separação de dados hoje é só por `esfera` ("Empresa" / "Casa"), um campo de filtro dentro da mesma conta — não é isolamento entre organizações diferentes.
- Um único endpoint de webhook público (`/api/v1/receber/webhook`) sem autenticação, que insere lançamentos diretamente — hoje não distingue de qual "empresa" é a origem.
- Upload de avatar salvo em `static/uploads/avatars`, compartilhado por todos os usuários.
- Chave secreta e sessão são globais ao processo (arquivo `.secret_key`).

Ou seja: hoje a aplicação é **single-tenant por completo** — qualquer usuário que faça login vê os mesmos dados. Não há nenhum mecanismo de isolamento.

### 1.3 Riscos específicos para multi-tenancy

- **Vazamento de dados entre tenants**: toda função em `database.py` monta a query sem filtro de organização. Basta esquecer um filtro em uma única função para vazar dados de um cliente para outro.
- **Webhook sem tenant**: precisa de um identificador de tenant (ex.: token de API por clínica) — hoje aceita qualquer POST e grava direto.
- **Uploads**: nome de arquivo do avatar usa `user_{id}`, sem tenant — colisão improvável mas vale isolar por pasta.
- **CSRF/sessão**: já existe proteção CSRF por sessão, isso não muda com multi-tenancy, mas o "esfera_filtro" salvo em sessão vai precisar conviver com um novo `tenant_id` de sessão.
- **SQLite com múltiplos tenants concorrentes**: SQLite lida bem com poucos usuários e baixa concorrência de escrita (seu caso, 2-10 tenants), mas se o volume crescer bastante vale migrar para Postgres — não é urgente agora.

---

## 2. Modelo de dados proposto

Estratégia: **banco único + coluna `tenant_id`** em todas as tabelas de dados, com filtro obrigatório em toda query.

```sql
CREATE TABLE tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,              -- Nome da clínica/empresa
    slug TEXT NOT NULL UNIQUE,       -- identificador amigável (ex: acupuntura-bemestar)
    ativo INTEGER DEFAULT 1,
    criado_em TEXT DEFAULT (datetime('now')),
    api_token TEXT UNIQUE            -- token para o webhook dessa organização
);
```

Alterações nas tabelas existentes:

- `usuarios`: adicionar `tenant_id INTEGER NOT NULL REFERENCES tenants(id)`. O `email` deixa de ser `UNIQUE` global e passa a ser `UNIQUE(tenant_id, email)` (dois tenants podem ter usuário com mesmo email).
- `categorias`: adicionar `tenant_id INTEGER NOT NULL REFERENCES tenants(id)`.
- `lancamentos`: adicionar `tenant_id INTEGER NOT NULL REFERENCES tenants(id)`.

Índices novos: `CREATE INDEX idx_lancamentos_tenant ON lancamentos(tenant_id, vencimento)` e equivalentes em `categorias` e `usuarios`, para manter as queries rápidas mesmo com filtro extra.

Migração de dados existentes: criar um tenant "padrão" (ex.: o cliente atual) e popular `tenant_id` em todas as linhas já existentes com esse id.

---

## 3. Plano de implementação em fases

### Fase 0 — Preparação (curta, baixo risco)
- Backup completo de `financeiro.db`.
- Atualizar a documentação `.md` para refletir o schema real (resolve a divergência do item 1.1).
- Criar branch de trabalho / ambiente de teste separado do banco de produção.

### Fase 1 — Schema e migração
- Criar tabela `tenants`.
- Adicionar coluna `tenant_id` (nullable inicialmente) em `usuarios`, `categorias`, `lancamentos`.
- Script de migração único: cria o tenant "padrão", preenche `tenant_id` em todas as linhas existentes, depois torna a coluna `NOT NULL`.
- Ajustar `UNIQUE` de `usuarios.email` para `UNIQUE(tenant_id, email)`.

### Fase 2 — Camada de dados (`database.py`)
- Toda função que lê/grava em `lancamentos`, `categorias` ou `usuarios` passa a receber `tenant_id` como parâmetro obrigatório e incluir `WHERE tenant_id = ?` (ou `AND tenant_id = ?`) em 100% das queries.
- Funções de busca por id (`buscar_lancamento`, `buscar_usuario_por_id` etc.) passam a exigir o `tenant_id` também, para impedir que alguém acesse um recurso de outro tenant só adivinhando o ID na URL (IDOR).
- Adicionar funções novas: `criar_tenant`, `buscar_tenant_por_slug`, `buscar_tenant_por_token`.

### Fase 3 — Autenticação e sessão (`app.py`)
- Login passa a resolver o `tenant_id` do usuário e gravá-lo em `session["tenant_id"]`.
- `login_required` (ou um novo decorator `tenant_required`) passa a validar que existe `tenant_id` na sessão e a repassá-lo a todas as chamadas de `database.*`.
- Revisar cada uma das ~25 rotas em `app.py` para garantir que passam `tenant_id` explicitamente — é o ponto de maior risco de esquecimento e vazamento de dados, então vale uma checklist rota a rota.
- Decidir a URL de acesso: subpasta por tenant (`/acupuntura-bemestar/...`) ou apenas isolamento por sessão (usuário loga e o tenant já vem implícito). Para 2-10 tenants com provisionamento manual, isolamento por sessão é suficiente e mais simples — sem necessidade de subdomínio.

### Fase 4 — Webhook e integrações externas
- `/api/v1/receber/webhook` passa a exigir um `api_token` (header ou query param) vinculado a um tenant específico (`tenants.api_token`), em vez de aceitar qualquer POST sem validação.
- `buscar_lancamento_por_referencia` passa a filtrar também por `tenant_id`.

### Fase 5 — Administração de tenants
- Tela simples (`/admin/tenants`) restrita a um usuário com papel de administrador (novo campo `usuarios.is_admin` ou tabela separada de admins da plataforma) para cadastrar novo tenant + primeiro usuário.
- Como o provisionamento é manual e a escala é pequena, isso pode ser uma tela HTML simples — não precisa de fluxo de signup público.

### Fase 6 — Uploads e isolamento de arquivos
- Avatares passam a ser salvos em `static/uploads/avatars/{tenant_id}/` para evitar qualquer colisão e facilitar limpeza por tenant.

### Fase 7 — Testes e validação
- Criar 2 tenants de teste com dados distintos e validar manualmente (ou script) que nenhuma rota retorna dado do tenant errado — login como usuário do tenant A, tentar acessar IDs conhecidos do tenant B via URL direta.
- Testar geração de recorrências (`gerar_recorrencias_do_mes`), que hoje varre lançamentos sem filtro de tenant — é uma função de alto risco de vazamento se não for ajustada.
- Revisar `excluir_lancamentos_detalhados_clinica` (delete em massa por padrão de texto) — também precisa de filtro de tenant.

### Fase 8 — Deploy
- Rodar migração no banco de produção (fora do horário de uso).
- Validar login e dados do tenant existente antes de liberar novos tenants.

---

## 4. Estimativa de esforço

Dado o tamanho do código (≈900 linhas em 3 arquivos Python, ~25 rotas), este é um trabalho de porte médio, não trivial — o risco não está na complexidade técnica (o padrão `tenant_id` é bem conhecido), mas em não deixar nenhuma query sem filtro. Estimativa: 3 a 5 dias de trabalho focado, incluindo revisão rota a rota e testes de isolamento (Fase 7 é a que mais protege contra vazamento e não deve ser cortada).

## 5. Pontos que precisam de decisão sua antes de começar a implementar

- Confirmar que o `.md` de documentação da clínica está desatualizado/obsoleto, ou se na verdade existe outro banco/projeto que eu ainda não vi.
- Definir quem faz o papel de "admin da plataforma" (cadastra novos tenants).
- Confirmar se o tenant atual (dados já em produção) deve virar o tenant "padrão" da migração.
