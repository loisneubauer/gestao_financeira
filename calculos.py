# calculos.py - Regras de negócio e estatísticas financeiras
from datetime import datetime, date, timedelta
import database


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


def calcular_resumo_financeiro(esfera_filtro="Todas", mes_ano=None):
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

    lancamentos = database.listar_lancamentos(esfera=esfera_filtro, mes_ano=mes_ano)

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



def calcular_despesas_por_categoria(esfera_filtro="Todas", mes_ano=None):
    """
    Agrupa o valor total de despesas (Contas a Pagar) por categoria.
    Retorna uma lista de dicionários [{'categoria': 'Aluguel', 'valor': 1500.0}, ...]
    ideal para alimentarmos os gráficos do Chart.js.
    """
    if not mes_ano:
        mes_ano = date.today().strftime("%Y-%m")

    lancamentos = database.listar_lancamentos(tipo="Pagar", esfera=esfera_filtro, mes_ano=mes_ano)

    agrupado = {}
    for l in lancamentos:
        cat_nome = l["categoria_nome"] or "Outros / Sem Categoria"
        agrupado[cat_nome] = agrupado.get(cat_nome, 0.0) + float(l["valor"] or 0)

    resultado = [{"categoria": k, "valor": v} for k, v in agrupado.items()]
    resultado.sort(key=lambda x: x["valor"], reverse=True)
    return resultado


def gerar_recorrencias_do_mes(mes_destino_str):
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
    lancamentos_origem = database.listar_lancamentos(mes_ano=mes_origem_str)
    recorrentes = [l for l in lancamentos_origem if l["recorrente"] == 1 or (l.get("frequencia_recorrencia") and l.get("frequencia_recorrencia") != "Nenhuma")]

    # Lançamentos que já existem no mês destino para evitar duplicidades
    existentes_destino = database.listar_lancamentos(mes_ano=mes_destino_str)
    chaves_existentes = set((e["descricao"], e["tipo"], e["esfera"], float(e["valor"]), e["vencimento"]) for e in existentes_destino)

    novos_gerados = 0
    for orig in recorrentes:
        freq = orig.get("frequencia_recorrencia") or "Mensal"
        
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
                            "observacoes": orig["observacoes"]
                        }
                        database.inserir_lancamento(dados_novo)
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
                "observacoes": orig["observacoes"]
            }
            database.inserir_lancamento(dados_novo)
            chaves_existentes.add(chave)
            novos_gerados += 1

    return novos_gerados

