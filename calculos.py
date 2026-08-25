# calculos.py - Regras de negócio e estatísticas financeiras
from datetime import datetime, date, timedelta
import database


# Classificação do gasto, do mais essencial ao mais evitável. Os NOMES vivem no
# banco (tabela niveis_importancia) e são editáveis; aqui ficam só o número do
# nível — que é o que os lançamentos guardam — e a aparência de cada um.
NIVEIS_VALIDOS = [1, 2, 3, 4]

# A "linha de corte" da escala: num mês apertado, é daqui para baixo que se
# corta. Por isso o dashboard soma 3 + 4 e chama de gasto que dava para cortar
# — e não de "evitável", que é o nome do nível 4 e significaria só ele.
NIVEIS_CORTAVEIS = [3, 4]

NAO_CLASSIFICADO = "Não classificado"

ESTILO_NIVEL = {
    1: {"cor": "#2E7D32", "icone": "bi-shield-check"},
    2: {"cor": "#0277BD", "icone": "bi-check-circle"},
    3: {"cor": "#EF6C00", "icone": "bi-emoji-smile"},
    4: {"cor": "#C62828", "icone": "bi-lightning"},
    None: {"cor": "#9E9E9E", "icone": "bi-question-circle"},
}


def nomes_dos_niveis():
    """Mapa {nivel: nome} lido do banco, para rotular badges e relatórios.
    Cai no número puro se algum nível sumir da tabela, em vez de estourar."""
    nomes = {n["nivel"]: n["nome"] for n in database.listar_niveis_importancia()}
    return {n: nomes.get(n, f"Nível {n}") for n in NIVEIS_VALIDOS}


def _campo(registro, nome, padrao=None):
    """Lê um campo de um sqlite3.Row com fallback.

    sqlite3.Row NÃO tem .get() — chamar row.get("x") levanta AttributeError.
    Como as funções aqui recebem ora Row (vindo do banco) ora dict (vindo do
    formulário), este helper atende os dois e ainda tolera coluna ausente,
    que é o caso de uma base antes da migração."""
    if isinstance(registro, dict):
        return registro.get(nome, padrao)
    if nome in registro.keys():
        valor = registro[nome]
        return padrao if valor is None else valor
    return padrao


def dias_uteis_restantes_no_mes(data_ref=None):
    """
    Calcula quantos dias úteis (segunda a sexta-feira) faltam a partir de hoje
    (ou da data de referência) até o último dia do mês corrente.
    """
    if not data_ref:
        data_ref = date.today()
    
    # Descobre o último dia do mês
    proximo_mes = data_ref.replace(day=28) + timedelta(days=4)
    ultimo_dia_mes = proximo_mes - timedelta(days=proximo_mes.day)

    dias_uteis = 0
    dia_atual = data_ref
    while dia_atual <= ultimo_dia_mes:
        if dia_atual.weekday() < 5:  # 0 a 4 = Segunda a Sexta
            dias_uteis += 1
        dia_atual += timedelta(days=1)
        
    return dias_uteis


def calcular_resumo_financeiro(tenant_id, esfera_filtro="Todas", mes_ano=None):
    """
    Calcula os totais de Contas a Pagar e Contas a Receber divididos por status:
    - Pago/Recebido
    - Em Atraso
    - A Vencer (Pendente)
    - Total Geral e Saldo Projetado (Receber Total - Pagar Total)
    """
    if not mes_ano:
        mes_ano = date.today().strftime("%Y-%m")

    hoje_str = date.today().isoformat()

    lancamentos = database.listar_lancamentos(tenant_id, esfera=esfera_filtro, mes_ano=mes_ano)

    resumo = {
        "pagar_pago": 0.0,
        "pagar_atrasado": 0.0,
        "pagar_a_vencer": 0.0,
        "pagar_total": 0.0,
        
        "receber_pago": 0.0,
        "receber_atrasado": 0.0,
        "receber_a_vencer": 0.0,
        "receber_total": 0.0,
        
        "saldo_atual": 0.0,      # Recebido Pago - Pago Realizado
        "saldo_projetado": 0.0   # Receber Total - Pagar Total
    }

    for l in lancamentos:
        valor = float(l["valor"] or 0)
        status = l["status"]
        vencimento = l["vencimento"]

        if l["tipo"] == "Pagar":
            resumo["pagar_total"] += valor
            if status == "Pago":
                resumo["pagar_pago"] += valor
            elif vencimento < hoje_str and status != "Pago":
                resumo["pagar_atrasado"] += valor
            else:
                resumo["pagar_a_vencer"] += valor

        elif l["tipo"] == "Receber":
            resumo["receber_total"] += valor
            if status == "Pago" or status == "Recebido":
                resumo["receber_pago"] += valor
            elif vencimento < hoje_str and status != "Pago" and status != "Recebido":
                resumo["receber_atrasado"] += valor
            else:
                resumo["receber_a_vencer"] += valor

    resumo["receber_pendente"] = resumo["receber_a_vencer"] + resumo["receber_atrasado"]
    resumo["saldo_atual"] = resumo["receber_pago"] - resumo["pagar_pago"]
    resumo["saldo_projetado"] = resumo["receber_total"] - resumo["pagar_total"]

    return resumo



def calcular_despesas_por_categoria(tenant_id, esfera_filtro="Todas", mes_ano=None):
    """
    Agrupa o valor total de despesas (Contas a Pagar) por categoria.
    Retorna uma lista de dicionários [{'categoria': 'Aluguel', 'valor': 1500.0}, ...]
    ideal para alimentarmos os gráficos do Chart.js.
    """
    if not mes_ano:
        mes_ano = date.today().strftime("%Y-%m")

    lancamentos = database.listar_lancamentos(tenant_id, tipo="Pagar", esfera=esfera_filtro, mes_ano=mes_ano)

    agrupado = {}
    for l in lancamentos:
        cat_nome = l["categoria_nome"] or "Outros / Sem Categoria"
        agrupado[cat_nome] = agrupado.get(cat_nome, 0.0) + float(l["valor"] or 0)

    resultado = [{"categoria": k, "valor": v} for k, v in agrupado.items()]
    resultado.sort(key=lambda x: x["valor"], reverse=True)
    return resultado


def calcular_gastos_por_importancia(tenant_id, esfera_filtro="Todas", mes_ano=None):
    """
    Agrupa as despesas (Contas a Pagar) do mês pelo nível de importância.

    Retorna um dicionário com:
      - 'niveis': lista do nível 1 ao 4 com {nivel, nome, valor, quantidade,
        percentual, cor, icone}, mais os não classificados no fim (se houver);
      - 'total': soma de todas as despesas do mês;
      - 'cortavel' / 'percentual_cortavel': soma e fatia dos níveis 3 e 4 — o
        que dava para cortar num mês apertado. Não se chama "evitável" porque
        esse é o nome do nível 4, e significaria só ele;
      - 'classificado' / 'nao_classificado': quanto já foi classificado e quanto falta.

    O percentual é calculado sobre o total JÁ CLASSIFICADO — dizer que "10%
    dava para cortar" quando metade dos gastos não tem classificação seria
    enganoso.
    """
    if not mes_ano:
        mes_ano = date.today().strftime("%Y-%m")

    lancamentos = database.listar_lancamentos(tenant_id, tipo="Pagar", esfera=esfera_filtro, mes_ano=mes_ano)
    nomes = nomes_dos_niveis()

    somas = {nivel: 0.0 for nivel in NIVEIS_VALIDOS}
    somas[None] = 0.0
    quantidades = {nivel: 0 for nivel in somas}

    for l in lancamentos:
        valor = float(l["valor"] or 0)
        nivel = _campo(l, "importancia_nivel")
        if nivel not in NIVEIS_VALIDOS:
            nivel = None
        somas[nivel] += valor
        quantidades[nivel] += 1

    total = sum(somas.values())
    nao_classificado = somas[None]
    classificado = total - nao_classificado
    cortavel = sum(somas[n] for n in NIVEIS_CORTAVEIS)

    ordem = list(NIVEIS_VALIDOS) + ([None] if nao_classificado > 0 else [])
    niveis = [
        {
            "nivel": nivel,
            "nome": nomes[nivel] if nivel else NAO_CLASSIFICADO,
            "valor": somas[nivel],
            "quantidade": quantidades[nivel],
            "percentual": (somas[nivel] / total * 100) if total else 0.0,
            "cor": ESTILO_NIVEL[nivel]["cor"],
            "icone": ESTILO_NIVEL[nivel]["icone"],
        }
        for nivel in ordem
    ]

    return {
        "niveis": niveis,
        "total": total,
        "cortavel": cortavel,
        "percentual_cortavel": (cortavel / classificado * 100) if classificado else 0.0,
        "classificado": classificado,
        "nao_classificado": nao_classificado,
        "tem_classificacao": classificado > 0,
    }


def gerar_recorrencias_do_mes(tenant_id, mes_destino_str):
    """
    Gera automaticamente os lançamentos recorrentes para o mês especificado (AAAA-MM),
    suportando recorrências Mensais, Quinzenais e Semanais.
    """
    from datetime import date, timedelta

    ano_dest, mes_dest = map(int, mes_destino_str.split("-"))

    # Calcula o mês anterior
    if mes_dest == 1:
        ano_origem = ano_dest - 1
        mes_origem = 12
    else:
        ano_origem = ano_dest
        mes_origem = mes_dest - 1

    mes_origem_str = f"{ano_origem:04d}-{mes_origem:02d}"

    # Busca lançamentos recorrentes do mês anterior
    lancamentos_origem = database.listar_lancamentos(tenant_id, mes_ano=mes_origem_str)
    recorrentes = [
        l for l in lancamentos_origem
        if l["recorrente"] == 1 or _campo(l, "frequencia_recorrencia", "Nenhuma") != "Nenhuma"
    ]

    # Lançamentos que já existem no mês destino para evitar duplicidades
    existentes_destino = database.listar_lancamentos(tenant_id, mes_ano=mes_destino_str)
    chaves_existentes = set((e["descricao"], e["tipo"], e["esfera"], float(e["valor"]), e["vencimento"]) for e in existentes_destino)

    novos_gerados = 0
    for orig in recorrentes:
        freq = _campo(orig, "frequencia_recorrencia") or "Mensal"
        
        if freq in ["Semanal", "Quinzenal"]:
            delta_dias = 7 if freq == "Semanal" else 14
            try:
                dt_base = date.fromisoformat(orig["vencimento"])
                dt_calc = dt_base + timedelta(days=delta_dias)
                # Avança até chegar no mês de destino
                while dt_calc.strftime("%Y-%m") < mes_destino_str:
                    dt_calc += timedelta(days=delta_dias)
                
                # Gera para todas as semanas/quinzenas que caírem dentro do mês de destino
                while dt_calc.strftime("%Y-%m") == mes_destino_str:
                    novo_vencimento = dt_calc.isoformat()
                    chave = (orig["descricao"], orig["tipo"], orig["esfera"], float(orig["valor"]), novo_vencimento)
                    if chave not in chaves_existentes:
                        dados_novo = {
                            "descricao": orig["descricao"],
                            "tipo": orig["tipo"],
                            "esfera": orig["esfera"],
                            "categoria_id": orig["categoria_id"],
                            "valor": orig["valor"],
                            "vencimento": novo_vencimento,
                            "status": "Pendente",
                            "forma_pagamento": orig["forma_pagamento"],
                            "recorrente": 1,
                            "frequencia_recorrencia": freq,
                            "observacoes": orig["observacoes"],
                            "importancia_nivel": _campo(orig, "importancia_nivel")
                        }
                        database.inserir_lancamento(tenant_id, dados_novo)
                        chaves_existentes.add(chave)
                        novos_gerados += 1
                    dt_calc += timedelta(days=delta_dias)
            except Exception as e:
                print(f"Erro ao gerar {freq} para {orig['descricao']}: {e}")
        else: # Mensal
            try:
                dia_orig = int(orig["vencimento"].split("-")[2])
                novo_vencimento = f"{ano_dest:04d}-{mes_dest:02d}-{dia_orig:02d}"
            except (IndexError, ValueError):
                novo_vencimento = f"{mes_destino_str}-10"

            chave = (orig["descricao"], orig["tipo"], orig["esfera"], float(orig["valor"]), novo_vencimento)
            if chave in chaves_existentes:
                continue

            dados_novo = {
                "descricao": orig["descricao"],
                "tipo": orig["tipo"],
                "esfera": orig["esfera"],
                "categoria_id": orig["categoria_id"],
                "valor": orig["valor"],
                "vencimento": novo_vencimento,
                "status": "Pendente",
                "forma_pagamento": orig["forma_pagamento"],
                "recorrente": 1,
                "frequencia_recorrencia": "Mensal",
                "observacoes": orig["observacoes"],
                "importancia_nivel": _campo(orig, "importancia_nivel")
            }
            database.inserir_lancamento(tenant_id, dados_novo)
            chaves_existentes.add(chave)
            novos_gerados += 1

    return novos_gerados


def projetar_recorrencias_do_mes(tenant_id, dados):
    """
    Quando um lançamento é criado ou editado com recorrência Semanal ou Quinzenal,
    gera automaticamente os lançamentos das semanas/quinzenas restantes dentro do próprio mês.
    """
    freq = dados.get("frequencia_recorrencia")
    if freq not in ["Semanal", "Quinzenal"] or not dados.get("vencimento"):
        return 0

    from datetime import date, timedelta
    try:
        dt_venc = date.fromisoformat(dados["vencimento"])
        mes_atual = dt_venc.strftime("%Y-%m")
        delta_dias = 7 if freq == "Semanal" else 14
        proxima_dt = dt_venc + timedelta(days=delta_dias)

        existentes = database.listar_lancamentos(tenant_id, mes_ano=mes_atual)
        chaves_existentes = set((e["descricao"], e["tipo"], e["esfera"], float(e["valor"]), e["vencimento"]) for e in existentes)

        novos_gerados = 0
        while proxima_dt.strftime("%Y-%m") == mes_atual:
            venc_str = proxima_dt.isoformat()
            chave = (dados["descricao"], dados["tipo"], dados["esfera"], float(dados["valor"]), venc_str)
            if chave not in chaves_existentes:
                dados_prox = dict(dados)
                dados_prox["vencimento"] = venc_str
                dados_prox["status"] = "Pendente"
                dados_prox["data_pagamento"] = None
                database.inserir_lancamento(tenant_id, dados_prox)
                chaves_existentes.add(chave)
                novos_gerados += 1
            proxima_dt += timedelta(days=delta_dias)
        return novos_gerados
    except Exception as e:
        print(f"Erro ao projetar recorrências do mês: {e}")
        return 0


