# Roteiro de Testes — Ativação do Multi-Tenancy

Rode isso no seu Mac, com o `venv` do projeto ativado (`source venv/bin/activate`), na pasta `Sistema_Financeiro`. Os testes automatizados de isolamento entre tenants (banco de dados) já foram executados e passaram; este roteiro cobre o que só dá pra validar rodando o app de verdade.

---

## 1. Ativar a migração

```bash
python app.py
```

Ao subir, o `database.criar_tabelas()` roda automaticamente e migra o `financeiro.db` (já existe um backup em `financeiro_backup_pre_multitenancy_20260810.db`, criado antes de qualquer alteração).

**Verifique no terminal**: não deve aparecer nenhum erro. Se aparecer, pare e me avise antes de continuar — não tente rodar de novo sem entender o erro.

## 2. Confirmar a migração no banco

Em outro terminal, ainda na pasta do projeto:

```bash
sqlite3 financeiro.db "SELECT id, nome, slug, ativo FROM tenants;"
sqlite3 financeiro.db "SELECT id, tenant_id, nome, email, is_admin FROM usuarios;"
```

Esperado: um tenant chamado "Acupuntura Bem-estar" (slug `padrao`), e as usuárias Lois e Laila com `tenant_id` igual ao id desse tenant.

## 3. Virar administradora

Os usuários migrados vêm com `is_admin = 0` por padrão. Para acessar a tela `/admin/tenants`, rode uma vez:

```bash
sqlite3 financeiro.db "UPDATE usuarios SET is_admin = 1 WHERE email = 'loisneubauer@gmail.com';"
```

## 4. Login e navegação normal (tenant existente)

- Acesse `http://localhost:5002/login` e entre com o email/senha da Lois.
- Confira que o Dashboard, Contas a Pagar, Contas a Receber e Categorias mostram os mesmos dados de sempre (nada deve ter sumido ou duplicado).
- Confira que o nome da organização aparece ao lado da saudação, no topo.
- No menu (⚙️), confira que dá pra editar perfil e trocar senha normalmente.
- Se você marcou o passo 3, deve aparecer um item "Organizações" na barra de navegação.

## 5. Criar uma organização de teste (valida o isolamento de verdade)

Em "Organizações" → formulário "Nova organização":

- Nome: `Clínica Teste`
- Slug: `clinica-teste`
- Usuário inicial: um nome/email/senha quaisquer (ex: `teste@clinicateste.com`)

Depois de criar:

1. Faça logout e faça login com o usuário da Clínica Teste.
2. Confirme que o Dashboard, Pagar, Receber e Categorias aparecem **vazios** — nenhum dado da Acupuntura Bem-estar deve aparecer aqui.
3. Cadastre uma categoria e um lançamento na Clínica Teste.
4. Faça logout, entre de novo como Lois (Acupuntura Bem-estar), e confirme que **não aparece** nada da Clínica Teste — nem a categoria, nem o lançamento.

Esse é o teste mais importante: se algum dado vazar entre as duas organizações aqui, é sinal de que alguma rota ficou sem filtro de tenant.

## 6. Testar o webhook com token por tenant

Pegue o token gerado para a Clínica Teste na tela "Organizações" (coluna "Token de API") e teste:

```bash
curl -X POST http://localhost:5002/api/v1/receber/webhook \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: COLE_O_TOKEN_AQUI" \
  -d '{"descricao": "Teste Webhook", "valor": 150.0, "referencia_id": "teste_001"}'
```

- Deve responder `201` e o lançamento deve aparecer só na Clínica Teste (não na Acupuntura Bem-estar).
- Repita sem o header `X-Api-Token` ou com um token errado — deve responder `401`.

## 7. Testar recorrências

- Cadastre um lançamento recorrente (Semanal ou Mensal) em uma das organizações.
- Use o botão "Gerar Recorrências" e confirme que as novas ocorrências aparecem só na organização onde foram criadas.

---

Se tudo isso passar, o multi-tenancy está funcionando de ponta a ponta. Qualquer coisa estranha (dado aparecendo trocado, erro 500, etc.), me manda a mensagem de erro que eu ajusto.
