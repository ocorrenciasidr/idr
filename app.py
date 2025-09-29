import os
import json 
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
import base64

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash

# Imports para gspread
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ReportLab (gera o PDF)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from fpdf import FPDF
import io

# -------------------- Fuso horário --------------------
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ_SAO = timezone(timedelta(hours=-3))


app = Flask(__name__)
app.secret_key = "sua_chave_secreta"

# -------------------- Configuração do Google Sheets --------------------
# Use variáveis de ambiente para a chave do serviço (recomendado no Render)
def conectar_sheets():
    # Carrega credenciais do JSON armazenado na variável de ambiente
    creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
    if not creds_json:
        # Se a variável de ambiente não estiver configurada, levanta um erro claro
        raise Exception("GOOGLE_SHEETS_CREDENTIALS environment variable not set.")
        
    creds_dict = json.loads(creds_json)
    
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # Substitua pelo URL ou nome da sua planilha
    sheet_url = os.environ.get('SPREADSHEET_URL')
    if not sheet_url:
        raise Exception("SPREADSHEET_URL environment variable not set.")
        
    sh = client.open_by_url(sheet_url)
    return sh

def salvar_dados(df, sheet_name="Dados"):
    try:
        sh = conectar_sheets()
        ws = sh.worksheet(sheet_name)
        
        # Limpa o conteúdo existente (mantendo a primeira linha de cabeçalho)
        ws.clear()
        
        # Converte o DataFrame para uma lista de listas (cabeçalho + dados)
        dados_para_salvar = [df.columns.tolist()] + df.values.tolist()
        
        # Escreve de volta no Sheets
        ws.update(dados_para_salvar)
        return True
    except Exception as e:
        print(f"Erro ao salvar dados no Google Sheets na aba '{sheet_name}':", e)
        return False
        
# -------------------- Funções auxiliares --------------------
def calc_status(tutor_flag, coord_flag, gestao_flag):
    if tutor_flag == "Sim" or coord_flag == "Sim" or gestao_flag == "Sim":
        return "Em Aberto"
    return "Concluído"

def carregar_dados(sheet_name="Dados"):
    """Carrega dados de uma aba do Google Sheets para um DataFrame do Pandas."""
    # NOVO: Lista de colunas abreviadas
    cols = [
        "ID", "DCO", "HCO", 
        "Professor", "Sala", "Aluno", "Tutor",
        "Descrição da Ocorrência",
        "Atendimento Professor", "ATT",
        "ATC", "ATG",
        "FT", "FC", "FG",
        "DT", "DC", "DG",
        "Status"
    ]
    
    try:
        sh = conectar_sheets()
        ws = sh.worksheet(sheet_name)
        
        dados_raw = ws.get_all_values()
        
        if len(dados_raw) <= 1:
            return pd.DataFrame(columns=cols)
            
        df = pd.DataFrame(dados_raw[1:], columns=dados_raw[0])

        # Garante que as colunas existam
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        
        # Conversão de tipos:
        df["Aluno"] = df["Aluno"].astype(str).str.strip()
        df["Sala"] = df["Sala"].astype(str).str.strip()
        
        try:
            df["ID"] = pd.to_numeric(df["ID"], errors="coerce").astype("Int64")
        except Exception:
            pass
        
        return df[cols] if sheet_name == "Dados" else df
        
    except Exception as e:
        print(f"Erro ao ler dados do Google Sheets na aba '{sheet_name}':", e)
        return pd.DataFrame(columns=cols)

def proximo_numero():
    df = carregar_dados()
    if df.empty:
        return 1
    try:
        maxv = pd.to_numeric(df["ID"], errors="coerce").max()
        return 1 if pd.isna(maxv) else int(maxv) + 1
    except Exception:
        return len(df) + 1

# -------------------- Rotas --------------------

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/ocorrencias", methods=["GET"])
def index():
    df = carregar_dados()
    
    # Filtros
    tutor_filtro = request.args.get("tutor")
    status_filtro = request.args.get("status")
    
    if tutor_filtro:
        df = df[df["Tutor"] == tutor_filtro]
    
    if status_filtro:
        df = df[df["Status"] == status_filtro]
        
    # Adicionar coluna "Prazo" - CORRIGIDO: usa DCO
    df["Prazo"] = df.apply(lambda row: (datetime.now(TZ_SAO) - datetime.strptime(row["DCO"], "%Y-%m-%d").replace(tzinfo=TZ_SAO)).days, axis=1)
    
    registros = df.to_dict("records")
    
    # Listas para filtros
    tutores = sorted(df["Tutor"].dropna().unique().tolist() if not df.empty else [])
    status_list = sorted(df["Status"].dropna().unique().tolist() if not df.empty else [])
    salas = sorted(df["Sala"].dropna().unique().tolist() if not df.empty else [])

    return render_template(
        "Index.html",
        registros=registros,
        tutores=tutores,
        status_list=status_list,
        salas=salas
    )

@app.route("/nova", methods=["GET"])
def nova():
    df_salas = carregar_dados(sheet_name="Salas")
    
    # Garante que só há salas únicas e não vazias
    salas = sorted(df_salas["Sala"].dropna().unique().tolist() if not df_salas.empty else [])
    
    # Simula a lista de professores logados (para teste)
    professores = ["Prof. Maria", "Prof. João", "Prof. Ana"] 
    
    return render_template("nova.html", salas=salas, professores=professores)

@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    df_salas = carregar_dados(sheet_name="Salas")
    
    # Filtra os alunos pela sala
    alunos_sala = df_salas[df_salas["Sala"].str.lower() == sala.lower()]
    
    # Retorna o nome do aluno e seu tutor (se houver)
    lista = alunos_sala[["Aluno", "Tutor"]].to_dict("records")
    return jsonify(lista)

@app.route("/salvar", methods=["POST"])
def salvar():
    df = carregar_dados()
    numero = proximo_numero()
    agora = datetime.now(TZ_SAO)
    
    # Verifica se os flags de atendimento foram setados
    flag_tutor = "Sim" if request.form.get("tutor") else "Não"
    flag_coord = request.form.get("flag_coord") or "Não"
    flag_gestao = request.form.get("flag_gestao") or "Não"

    # Calcula o status
    status = calc_status(flag_tutor, flag_coord, flag_gestao)

    # CORRIGIDO: Uso das novas chaves abreviadas
    nova = {
        "ID": numero,
        "DCO": agora.strftime("%Y-%m-%d"),
        "HCO": agora.strftime("%H:%M:%S"),
        "Professor": request.form.get("professor", ""),
        "Sala": request.form.get("sala", ""),
        "Aluno": request.form.get("aluno", ""),
        "Tutor": request.form.get("tutor", ""),
        "Descrição da Ocorrência": request.form.get("descricao", ""),
        "Atendimento Professor": "",
        "ATT": "",
        "ATC": "",
        "ATG": "",
        "FT": flag_tutor,
        "FC": flag_coord,
        "FG": flag_gestao,
        "DT": "",
        "DC": "",
        "DG": "",
        "Status": status
    }
    
    # Adiciona a nova ocorrência e salva
    df_nova_linha = pd.DataFrame([nova])
    df_final = pd.concat([df_nova_linha, df], ignore_index=True)
    
    if salvar_dados(df_final):
        flash("Ocorrência registrada com sucesso!", "success")
    else:
        flash("Erro ao registrar ocorrência.", "danger")
        
    return redirect(url_for("index"))

@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    campo = request.args.get("campo", "").strip()
    df = carregar_dados()
    linha = df[df["ID"] == oid]

    if linha.empty:
        return "Ocorrência não encontrada", 404

    registro = linha.iloc[0].to_dict()

    permissoes = {
        "professor": campo == "lapis" or campo == "edit",
        "tutor": campo == "lapis" or campo == "edit" or campo == "tutor",
        "coord": campo == "lapis" or campo == "edit" or campo == "coord",
        "gestao": campo == "lapis" or campo == "edit" or campo == "gestao",
    }
    
    if request.method == "POST":
        agora = datetime.now(TZ_SAO).strftime("%Y-%m-%d")
        
        # Atualização dos campos de atendimento e Flags - CORRIGIDO: usa as novas chaves
        if campo in ["lapis", "edit", "tutor", "coord", "gestao"]:
            if campo in ["lapis", "edit", "tutor"]:
                df.loc[df["ID"] == oid, "ATT"] = request.form.get("at_tutor", "")
                if campo == "tutor":
                    df.loc[df["ID"] == oid, "FT"] = "Não"
                    df.loc[df["ID"] == oid, "DT"] = agora
            if campo in ["lapis", "edit", "coord"]:
                df.loc[df["ID"] == oid, "ATC"] = request.form.get("at_coord", "")
                if campo == "coord":
                    df.loc[df["ID"] == oid, "FC"] = "Não"
                    df.loc[df["ID"] == oid, "DC"] = agora
            if campo in ["lapis", "edit", "gestao"]:
                df.loc[df["ID"] == oid, "ATG"] = request.form.get("at_gestao", "")
                if campo == "gestao":
                    df.loc[df["ID"] == oid, "FG"] = "Não"
                    df.loc[df["ID"] == oid, "DG"] = agora
            if campo in ["lapis", "edit"]:
                df.loc[df["ID"] == oid, "Atendimento Professor"] = request.form.get("at_professor", "")

        # Recalcula o Status - CORRIGIDO: usa as novas chaves
        linha_atual = df[df["ID"] == oid].iloc[0]
        df.loc[df["ID"] == oid, "Status"] = calc_status(
            linha_atual["FT"], linha_atual["FC"], linha_atual["FG"]
        )
        
        if salvar_dados(df):
            flash("Ocorrência atualizada com sucesso!", "success")
        else:
            flash("Erro ao atualizar ocorrência.", "danger")
            
        return redirect(url_for("index"))

    return render_template("editar.html", ocorrencia=registro, permissoes=permissoes, campo=campo)

@app.route("/visualizar/<int:oid>")
def visualizar(oid):
    df = carregar_dados()
    linha = df[df["ID"] == oid]
    
    if linha.empty:
        return "Ocorrência não encontrada", 404
    
    registro = linha.iloc[0].to_dict()
    permissoes = {"professor": False, "tutor": False, "coord": False, "gestao": False}
    return render_template("editar.html", ocorrencia=registro, permissoes=permissoes, campo="view")


@app.route("/relatorio_inicial")
def relatorio_inicial():
    return render_template("relatorio_inicial.html")

@app.route("/relatorio_aluno")
def relatorio_aluno():
    df_dados = carregar_dados()
    df_salas = carregar_dados(sheet_name="Salas")
    
    salas = sorted(df_salas["Sala"].dropna().unique().tolist() if not df_salas.empty else [])
    
    sala_sel = request.args.get("sala")
    aluno_sel = request.args.get("aluno")
    
    alunos = []
    ocorrencias = []
    
    if sala_sel:
        alunos = sorted(df_salas[df_salas["Sala"] == sala_sel]["Aluno"].dropna().unique().tolist())
        
    if sala_sel and aluno_sel:
        # Filtra as ocorrências pelo aluno
        ocorrencias_df = df_dados[
            (df_dados["Sala"] == sala_sel) &
            (df_dados["Aluno"] == aluno_sel)
        ]
        ocorrencias = ocorrencias_df.to_dict("records")

    return render_template(
        "relatorio_aluno.html", 
        salas=salas, 
        alunos=alunos, 
        sala_sel=sala_sel, 
        aluno_sel=aluno_sel,
        ocorrencias=ocorrencias
    )
    
@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    sala = request.form.get("sala")
    aluno = request.form.get("aluno")
    ocorrencias_ids = request.form.getlist("ocorrencias")
    
    df = carregar_dados() 
    
    if ocorrencias_ids:
        # Filtra apenas as ocorrências selecionadas
        ocorrencias = df[df["ID"].isin([int(oid) for oid in ocorrencias_ids])].sort_values(by="ID")
    elif sala and aluno:
        # Se não houver seleção, gera o relatório de todas as ocorrências filtradas (lógica de fallback)
        ocorrencias = df[
            (df["Sala"].str.strip().str.lower() == sala.strip().lower()) &
            (df["Aluno"].str.strip().str.lower() == aluno.strip().lower())
        ].sort_values(by="ID")
    else:
        ocorrencias = pd.DataFrame()

    # Gerar PDF (exemplo simples com FPDF)
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    # Título do PDF
    pdf.cell(200, 10, txt=f"Relatório de {aluno} - Sala {sala}", ln=True)
    pdf.cell(200, 10, txt="Ocorrências Selecionadas:", ln=True)

    for i, row in ocorrencias.iterrows():
        # CORRIGIDO: usa DCO para a data
        pdf.multi_cell(0, 10, txt=f"ID: {row['ID']} - Data: {row['DCO']}")
        pdf.multi_cell(0, 5, txt=f"Descrição: {row['Descrição da Ocorrência']}")
        pdf.multi_cell(0, 5, txt=f"Status: {row['Status']}", ln=True)
        # Adicionar mais detalhes se necessário (Atendimentos, etc.)
    
    output = BytesIO()
    pdf.output(output)
    output.seek(0)
    return send_file(output, download_name=f"relatorio_{aluno}.pdf", as_attachment=True)
    
# Rotas restantes (relatórios)

@app.route("/abrir_pendencia/<oid>/<papel>")
def abrir_pendencia(oid, papel):
    df = carregar_dados()
    ocorrencia = df[df["ID"] == int(oid)].iloc[0]
    
    permissoes = {"professor": False, "tutor": False, "coord": False, "gestao": False}
    if papel == 'tutor':
        permissoes["tutor"] = True
    elif papel == 'coord':
        permissoes["coord"] = True
    elif papel == 'gestao':
        permissoes["gestao"] = True
        
    return render_template("editar.html", ocorrencia=ocorrencia.to_dict(), permissoes=permissoes, campo=papel)
    
@app.route("/relatorio_geral")
def relatorio_geral():
    # Rotina para gerar o relatório geral
    return "Página de Relatório Geral (A implementar)"

@app.route("/relatorio_tutor")
def relatorio_tutor():
    # Rotina para gerar o relatório por tutor
    return "Página de Relatório por Tutor (A implementar)"

@app.route("/relatorio_tutoraluno")
def relatorio_tutoraluno():
    # Rotina para listar tutorados por tutor
    return "Página de Lista Tutor/Tutorados (A implementar)"

@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")

if __name__ == "__main__":
    app.run(debug=True)