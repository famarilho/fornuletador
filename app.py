import os
import json
from flask import Flask, request, jsonify
import pulp
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# Configuração do Acesso ao Google Sheets via gspread
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def para_float(valor, padrao=0.0):
    """Converte valores com vírgula ou ponto para float com segurança."""
    if valor is None or valor == "":
        return float(padrao)
    return float(str(valor).replace(',', '.'))

def conectar_sheets(nome_planilha="Formulacao_Suplemento"):
    try:
        json_env = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if json_env:
            info = json.loads(json_env)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
            
        client = gspread.authorize(creds)
        return client.open(nome_planilha)
    except Exception as e:
        print(f"Erro ao conectar com Google Sheets: {e}")
        return None

def resolver_otimizacao(dados):
    id_formulacao = dados.get("ID_Formulacao", "d1")
    pv = para_float(dados.get("PV_Inicial", 400))
    cms_supl_mn = para_float(dados.get("Consumo_supl_MN_kg", 1.0))
    fdn_pasto = para_float(dados.get("Pasto_FDN_perc", 60.0))
    
    # Nutrientes do Pasto (%)
    pb_pasto_pct = para_float(dados.get("Pasto_PB_perc", 0)) / 100.0
    pdr_pasto_pct = para_float(dados.get("Pasto_PDR_perc", 0)) / 100.0
    ndt_pasto_pct = para_float(dados.get("Pasto_NDT_perc", 0)) / 100.0
    ca_pasto_pct = para_float(dados.get("Pasto_Ca_perc", 0)) / 100.0
    p_pasto_pct = para_float(dados.get("Pasto_P_perc", 0)) / 100.0
    s_pasto_pct = para_float(dados.get("Pasto_S_perc", 0)) / 100.0
    ee_pasto_pct = para_float(dados.get("Pasto_EE_perc", 0)) / 100.0

    # Diagnóstico da Pastagem via FDN
    cms_pasto_kg = (120.0 / fdn_pasto) * (pv / 100.0) if fdn_pasto > 0 else 0
    pb_pasto_kg = cms_pasto_kg * pb_pasto_pct
    pdr_pasto_kg = cms_pasto_kg * pdr_pasto_pct
    ndt_pasto_kg = cms_pasto_kg * ndt_pasto_pct
    ca_pasto_kg = cms_pasto_kg * ca_pasto_pct
    p_pasto_kg = cms_pasto_kg * p_pasto_pct
    s_pasto_kg = cms_pasto_kg * s_pasto_pct
    ee_pasto_kg = cms_pasto_kg * ee_pasto_pct

    ingredientes = dados.get("Ingredientes", [])
    n_ing = len(ingredientes)

    if n_ing == 0:
        return {"status": "Erro", "mensagem": "Nenhum ingrediente fornecido"}

    def rodar_lp(modo_fallback=False):
        prob = pulp.LpProblem("Formulacao_Suplemento", pulp.LpMinimize)
        x = [pulp.LpVariable(f"x_{i}", lowBound=0, upBound=1) for i in range(n_ing)]
        
        # 1. Soma das proporções = 100%
        prob += pulp.lpSum([x[i] for i in range(n_ing)]) == 1.0, "Soma_Proporcoes"

        # 2. Travas Tecnológicas
        travas = dados.get("Travas", {})
        for i, ing in enumerate(ingredientes):
            if ing.get("Eh_NaCl") and travas.get("NaCl_perc") is not None and travas.get("NaCl_perc") != "":
                prob += x[i] == (para_float(travas["NaCl_perc"]) / 100.0)
            if ing.get("Eh_Cal") and travas.get("Cal_perc") is not None and travas.get("Cal_perc") != "":
                prob += x[i] == (para_float(travas["Cal_perc"]) / 100.0)
            if ing.get("Eh_Farelo_Soja") and travas.get("FS_min_perc") is not None and travas.get("FS_min_perc") != "":
                prob += x[i] >= (para_float(travas["FS_min_perc"]) / 100.0)
            if ing.get("Eh_Milho") and travas.get("M_min_perc") is not None and travas.get("M_min_perc") != "":
                prob += x[i] >= (para_float(travas["M_min_perc"]) / 100.0)
            if ing.get("Eh_Melaco"):
                if travas.get("Melaco_min_perc") is not None and travas.get("Melaco_min_perc") != "":
                    prob += x[i] >= (para_float(travas["Melaco_min_perc"]) / 100.0)
                if travas.get("Melaco_max_perc") is not None and travas.get("Melaco_max_perc") != "":
                    prob += x[i] <= (para_float(travas["Melaco_max_perc"]) / 100.0)
            if ing.get("Eh_Oleo"):
                if travas.get("Oleo_min_perc") is not None and travas.get("Oleo_min_perc") != "":
                    prob += x[i] >= (para_float(travas["Oleo_min_perc"]) / 100.0)
                if travas.get("Oleo_max_perc") is not None and travas.get("Oleo_max_perc") != "":
                    prob += x[i] <= (para_float(travas["Oleo_max_perc"]) / 100.0)
            if ing.get("Eh_Ureia") and travas.get("Ureia_max_perc") is not None and travas.get("Ureia_max_perc") != "":
                prob += x[i] * cms_supl_mn <= (para_float(travas["Ureia_max_perc"]) / 100.0)

        # 3. Saúde Animal
        limite_ureia_kg = (35.0 * (pv / 100.0)) / 1000.0
        ureia_indices = [i for i, ing in enumerate(ingredientes) if ing.get("Eh_Ureia")]
        if ureia_indices:
            prob += pulp.lpSum([x[i] * cms_supl_mn for i in ureia_indices]) <= limite_ureia_kg, "Limite_Ureia_Saude"

        ee_supl_expr = pulp.lpSum([x[i] * cms_supl_mn * (para_float(ing["MS_perc"])/100.0) * (para_float(ing["EE_perc"])/100.0) for i, ing in enumerate(ingredientes)])
        ms_supl_expr = pulp.lpSum([x[i] * cms_supl_mn * (para_float(ing["MS_perc"])/100.0) for i, ing in enumerate(ingredientes)])
        cms_total_expr = cms_pasto_kg + ms_supl_expr
        prob += (ee_pasto_kg + ee_supl_expr) <= 0.07 * cms_total_expr, "Limite_EE_Saude"

        # 4. Relações Ca:P e N:S
        exig = dados.get("Exigencias", {})
        ca_supl_expr = pulp.lpSum([x[i] * cms_supl_mn * (para_float(ing["MS_perc"])/100.0) * (para_float(ing["Ca_perc"])/100.0) for i, ing in enumerate(ingredientes)])
        p_supl_expr = pulp.lpSum([x[i] * cms_supl_mn * (para_float(ing["MS_perc"])/100.0) * (para_float(ing["P_perc"])/100.0) for i, ing in enumerate(ingredientes)])
        s_supl_expr = pulp.lpSum([x[i] * cms_supl_mn * (para_float(ing["MS_perc"])/100.0) * (para_float(ing.get("S_perc", 0))/100.0) for i, ing in enumerate(ingredientes)])
        pb_supl_expr = pulp.lpSum([x[i] * cms_supl_mn * (para_float(ing["MS_perc"])/100.0) * (para_float(ing["PB_perc"])/100.0) for i, ing in enumerate(ingredientes)])

        if exig.get("CaP_min") and para_float(exig["CaP_min"]) > 0:
            prob += (ca_pasto_kg + ca_supl_expr) >= para_float(exig["CaP_min"]) * (p_pasto_kg + p_supl_expr)
        if exig.get("CaP_max") and para_float(exig["CaP_max"]) > 0:
            prob += (ca_pasto_kg + ca_supl_expr) <= para_float(exig["CaP_max"]) * (p_pasto_kg + p_supl_expr)
        
        n_total_expr = (pb_pasto_kg + pb_supl_expr) / 6.25
        s_total_expr = (s_pasto_kg + s_supl_expr)
        if exig.get("NS_min") and para_float(exig["NS_min"]) > 0:
            prob += n_total_expr >= para_float(exig["NS_min"]) * s_total_expr
        if exig.get("NS_max") and para_float(exig["NS_max"]) > 0:
            prob += n_total_expr <= para_float(exig["NS_max"]) * s_total_expr

        # 5. Condições Nutricionais
        if not modo_fallback:
            condicoes = dados.get("Condicoes_Nutrientes", {})
            nutr_map = {
                "PB": (pb_pasto_kg, pb_supl_expr, para_float(exig.get("PB_kg", 0))),
                "PDR": (pdr_pasto_kg, pulp.lpSum([x[i] * cms_supl_mn * (para_float(ing["MS_perc"])/100.0) * (para_float(ing["PDR_perc"])/100.0) for i, ing in enumerate(ingredientes)]), para_float(exig.get("PDR_kg", 0))),
                "NDT": (ndt_pasto_kg, pulp.lpSum([x[i] * cms_supl_mn * (para_float(ing["MS_perc"])/100.0) * (para_float(ing["NDT_perc"])/100.0) for i, ing in enumerate(ingredientes)]), para_float(exig.get("NDT_kg", 0))),
                "Ca": (ca_pasto_kg, ca_supl_expr, para_float(exig.get("Ca_kg", 0))),
                "P": (p_pasto_kg, p_supl_expr, para_float(exig.get("P_kg", 0)))
            }

            for nutr, (pasto_val, supl_expr, exig_val) in nutr_map.items():
                cond = condicoes.get(nutr, {})
                acao = cond.get("Acao", "MAXIMIZAR")
                if acao == "OBRIGATORIO":
                    prob += (pasto_val + supl_expr) >= exig_val
                elif acao == "INTERVALO":
                    if cond.get("Minimo") is not None and cond.get("Minimo") != "" and para_float(cond["Minimo"]) > 0:
                        prob += (pasto_val + supl_expr) >= para_float(cond["Minimo"])
                    if cond.get("Maximo") is not None and cond.get("Maximo") != "" and para_float(cond["Maximo"]) > 0:
                        prob += (pasto_val + supl_expr) <= para_float(cond["Maximo"])

        # 6. Função Objetivo
        custo_expr = pulp.lpSum([x[i] * para_float(ing["Custo_kg_MN"]) for i, ing in enumerate(ingredientes)])
        penalizacao = 0
        condicoes = dados.get("Condicoes_Nutrientes", {})
        for nutr in ["PB", "PDR", "NDT", "Ca", "P"]:
            if condicoes.get(nutr, {}).get("Acao") == "MAXIMIZAR":
                if nutr == "PB":
                    penalizacao -= 0.01 * pb_supl_expr
                elif nutr == "NDT":
                    penalizacao -= 0.01 * pulp.lpSum([x[i] * (para_float(ing["NDT_perc"])/100.0) for i, ing in enumerate(ingredientes)])

        prob += custo_expr + penalizacao, "Objetivo"
        prob.solve(pulp.PULP_CBC_CMD(msg=0))
        return prob, x

    # Execução e Fallback
    prob, x = rodar_lp(modo_fallback=False)
    aviso = ""
    if pulp.LpStatus[prob.status] != 'Optimal':
        prob, x = rodar_lp(modo_fallback=True)
        aviso = "As exigências nutricionais obrigatórias/intervalo não puderam ser atendidas integralmente devido aos limites de consumo ou travas tecnológicas."

    if pulp.LpStatus[prob.status] != 'Optimal':
        return {"status": "Erro", "mensagem": "Modelo Inviável"}

    # Montagem dos Resultados
    resultado_ingredientes = []
    custo_supl_kg_mn = 0
    for i, ing in enumerate(ingredientes):
        prop = pulp.value(x[i])
        if prop and prop > 0.0001:
            custo_ing = para_float(ing["Custo_kg_MN"])
            resultado_ingredientes.append({
                "ID_Ingrediente": ing["ID_Ingrediente"],
                "Nome": ing["Nome"],
                "Proporcao_MN_perc": round(prop * 100, 2),
                "Consumo_kg_MN": round(prop * cms_supl_mn, 4),
                "Custo_kg_MN": custo_ing
            })
            custo_supl_kg_mn += prop * custo_ing

    custo_diario = round(custo_supl_kg_mn * cms_supl_mn, 3)
    custo_supl_kg_mn = round(custo_supl_kg_mn, 3)

    # Gravação no Google Sheets via gspread
    wb = conectar_sheets("Formulacao_Suplemento")
    if wb:
        # 1. Limpa resultados anteriores da formulacao na aba Resultados_Mistura
        aba_mistura = wb.worksheet("Resultados_Mistura")
        registros = aba_mistura.get_all_records()
        novas_linhas = []
        for reg in registros:
            if str(reg.get("ID_Formulacao")) != str(id_formulacao):
                novas_linhas.append(list(reg.values()))

        # Adiciona os novos resultados
        cont = len(novas_linhas) + 1
        for item in resultado_ingredientes:
            novas_linhas.append([
                f"r_{cont}",
                id_formulacao,
                item["ID_Ingrediente"],
                item["Nome"],
                item["Proporcao_MN_perc"],
                item["Consumo_kg_MN"],
                item["Custo_kg_MN"]
            ])
            cont += 1
        
        # Atualiza a aba Resultados_Mistura
        aba_mistura.clear()
        aba_mistura.append_row(["ID_Resultado", "ID_Formulacao", "ID_Ingrediente", "Nome_Ingrediente", "Proporcao_MN_perc", "Consumo_kg_MN", "Custo_kg_MN"])
        if novas_linhas:
            aba_mistura.append_rows(novas_linhas)

        # 2. Atualiza os custos e o aviso na aba Formulacoes
        aba_form = wb.worksheet("Formulacoes")
        cell = aba_form.find(str(id_formulacao))
        if cell:
            row = cell.row
            aba_form.update_cell(row, 15, custo_supl_kg_mn)  # Custo_Supl_kg_MN
            aba_form.update_cell(row, 16, custo_diario)       # Custo_Diario_Animal
            aba_form.update_cell(row, 17, aviso)              # Avisos_Relatorio

    return {
        "status": "Sucesso",
        "Avisos_Relatorio": aviso,
        "Custo_Supl_kg_MN": custo_supl_kg_mn,
        "Custo_Diario_Animal": custo_diario,
        "Ingredientes_Mistura": resultado_ingredientes
    }

@app.route('/formular', methods=['POST'])
def webhook_formular():
    dados = request.get_json()
    resposta = resolver_otimizacao(dados)
    return jsonify(resposta)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)