import os
import json # NOVO: Para carregar o JSON das credenciais
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
# import shutil # REMOVIDO: Não é mais necessário para backup
import base64

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash

# NOVO: Imports para gspread
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

# --- REMOÇÃO DE CAMINHOS LOCAIS ---
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# MODELO_EXCEL = os.path.join(BASE_DIR, "ControleOcorrencias.xlsx")
# DADOS_EXCEL = os.path.join(BASE_DIR, "dados_ocorrencias.xlsx")
# BACKUP_DIR = os.path.join(BASE_DIR, "backups")
# os.makedirs(BACKUP_DIR, exist_ok=True)

# -------------------- CONEXÃO GOOGLE SHEETS --------------------
# *** IMPORTANTE: SUBSTITUA ESTE VALOR PELO ID DA SUA PLANILHA ***
PLANILHA_ID = "1Jyle_LCRCKQfbDShoIj-9MPNIkVSkYxWaCwQrhmxSoE" 

SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

def conectar_sheets():
    """Tenta autenticar usando a Variável de Ambiente GSPREAD_CREDENTIALS."""
    creds_json = os.environ.get("GSPREAD_CREDENTIALS")
    if not creds_json:
        # Se a variável de ambiente não estiver configurada, levanta um erro claro
        raise Exception("Variável GSPREAD_CREDENTIALS não configurada no Render. Verifique suas credenciais JSON.")

    creds_dict = json.loads(creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
    client = gspread.authorize(creds)
    
    # Abre a planilha pelo ID
    return client.open_by_key(PLANILHA_ID)
# ---------------------------------------------------------------

app = Flask(__name__)
app.secret_key = "idrgestao"

# -------------------- Funções auxiliares --------------------
# --- FUNÇÕES DE ARQUIVO REMOVIDAS ---
# def backup_dados(): # REMOVIDA
# def restaurar_backup(): # REMOVIDA
# ------------------------------------

def carregar_dados(sheet_name="Dados"):
    """Carrega dados de uma aba do Google Sheets para um DataFrame do Pandas."""
    cols = [
        "ID", "DCO", "HCO",
        "Professor", "Sala", "Aluno", "Tutor",
        "Descrição da Ocorrência",
        "Atendimento Professor", "ATT",
        "ATC", "ATG", "FT", "FC", "FG", "DT", "DC", "DG",
        "Status"
    ]
    
    # restaurar_backup() REMOVIDA
    
    try:
        sh = conectar_sheets()
        ws = sh.worksheet(sheet_name)
        
        # Leitura eficiente de todos os dados (lista de listas)
        dados_raw = ws.get_all_values()
        
        if len(dados_raw) <= 1: # Checa se há apenas o cabeçalho ou está vazia
            return pd.DataFrame(columns=cols)
            
        # Cria o DataFrame usando a primeira linha como cabeçalho
        df = pd.DataFrame(dados_raw[1:], columns=dados_raw[0])

        # --- Lógica de compatibilidade e tratamento de tipos mantida ---
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        df["Aluno"] = df["Aluno"].astype(str).str.strip()
        df["Sala"] = df["Sala"].astype(str).str.strip()
        try:
            df["ID"] = pd.to_numeric(df["ID"], errors="coerce").astype("Int64")
        except Exception:
            pass
        # -----------------------------------------------------------
        
        # Retorna o DataFrame, garantindo as colunas principais
        return df[cols] if sheet_name == "Dados" else df
        
    except Exception as e:
        print(f"Erro ao ler dados do Google Sheets na aba '{sheet_name}':", e)
        # Se falhar, retorna um DF vazio, mantendo a lógica original
        return pd.DataFrame(columns=cols)

def salvar_dados(df):
    """Salva o DataFrame de volta na aba 'Dados' do Google Sheets."""
    try:
        sh = conectar_sheets()
        # Salva na aba "Dados"
        ws = sh.worksheet("Dados")
        
        # Converte o DataFrame para o formato de lista de listas (incluindo cabeçalho)
        dados_para_sheets = [df.columns.values.tolist()] + df.values.tolist()
        
        # Atualiza a planilha inteira
        ws.update(dados_para_sheets)
        
        # backup_dados() REMOVIDO
    except Exception as e:
        print("Erro ao salvar dados no Google Sheets:", e)
        # Em produção, você pode querer flashear uma mensagem de erro para o usuário
        
def calc_status(ft, fc, fg):
    return "Finalizada" if str(ft).strip().lower() == "não" and str(fc).strip().lower() == "não" and str(fg).strip().lower() == "não" else "Em Atendimento"

def proximo_numero():
    df = carregar_dados() # Chama a nova função de leitura
    if df.empty:
        return 1
    try:
        maxv = pd.to_numeric(df["ID"], errors="coerce").max()
        return 1 if pd.isna(maxv) else int(maxv) + 1
    except Exception:
        return len(df) + 1

# -------------------- Rotas principais --------------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/ocorrencias")
def index():
    tutor = request.args.get("tutor", "").strip()
    status = request.args.get("status", "").strip()
    sala = request.args.get("sala", "").strip()
    aluno = request.args.get("aluno", "").strip()

    df = carregar_dados() # LÊ DA ABA "Dados" do Sheets
    if not df.empty:
        df["Status"] = df.apply(lambda r: calc_status(r["FT"], r["FC"], r["FG"]), axis=1)
        if tutor:
            df = df[df["Tutor"].str.strip().str.lower() == tutor.strip().lower()]
        if status:
            df = df[df["Status"].str.strip().str.lower() == status.strip().lower()]
        if sala:
            df = df[df["Sala"].str.strip().str.lower() == sala.strip().lower()]
        if aluno:
            df = df[df["Aluno"].str.strip().str.lower() == aluno.strip().lower()]
        df = df.sort_values(by="ID", ascending=False)

    # --- NOVO: Lendo Tutores da Aba "Alunos" ---
    try:
        tutores_df = carregar_dados(sheet_name="Alunos")
        # Mantendo sua lógica para extrair tutores
        tutores_ref = tutores_df["Tutor"].dropna().unique().tolist()
    except Exception as e:
        print("Erro ao carregar lista de tutores:", e)
        tutores_ref = []
    # --------------------------------------------

    registros = [] if df.empty else df.to_dict(orient="records")
    return render_template(
        "index.html",
        registros=registros,
        tutores=tutores_ref,
        salas=sorted(df["Sala"].dropna().unique().tolist() if not df.empty else [])
    )

@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")

@app.route("/nova")
def nova():
    # --- NOVO: Lendo Professores da Aba "Professores" ---
    try:
        df_professores = carregar_dados(sheet_name="Professores")
        professores = df_professores["Professor"].dropna().tolist()
    except Exception as e:
        print("Erro ao carregar lista de professores:", e)
        professores = []
    
    # --- NOVO: Lendo Salas da Aba "Salas" ---
    try:
        df_salas = carregar_dados(sheet_name="Salas")
        salas = df_salas["Sala"].dropna().tolist()
    except Exception as e:
        print("Erro ao carregar lista de salas:", e)
        salas = []
    # ----------------------------------------
    
    return render_template("nova.html", professores=professores, salas=salas)

@app.route("/salvar", methods=["POST"])
def salvar():
    df = carregar_dados() # LÊ DO SHEETS
    numero = proximo_numero()
    agora = datetime.now(TZ_SAO)

    nova = {
        "ID": numero,
        "DCO": agora.strftime("%Y-%m-%d"),
        "HCO": agora.strftime("%H:%M:%S"),
        "Professor": request.form.get("professor", ""),
        "Sala": request.form.get("sala", ""),
        "Aluno": request.form.get("aluno", ""),
        "Tutor": request.form.get("tutor", ""),
        "Descrição da Ocorrência": request.form.get("descricao", ""),
        "Atendimento Professor": request.form.get("at_professor", ""),
        "ATT Tutor": "",
        "ATC": "",
        "ATG": "",
        "FT": "Sim" if request.form.get("FT") else "Não",
        "FC": "Sim" if request.form.get("FC") else "Não",
        "FG": "Sim" if request.form.get("FG") else "Não",
        "DT": agora.strftime("%Y-%m-%d"),
        "DC": agora.strftime("%Y-%m-%d"),
        "DG": agora.strftime("%Y-%m-%d"),
        "Status": "Em Atendimento",
    }

    df = pd.concat([df, pd.DataFrame([nova])], ignore_index=True)
    # backup_dados() REMOVIDO
    salvar_dados(df) # SALVA NO SHEETS
    return redirect(url_for("index"))

# -------------------- Editar / Visualizar --------------------
@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    campo = request.args.get("campo", "").strip()
    df = carregar_dados() # LÊ DO SHEETS
    linha = df[df["ID"] == oid]
    if linha.empty:
        return "Ocorrência não encontrada", 404

    registro = linha.iloc[0].to_dict()

    if request.method == "POST":
        if campo in ["lupa", "lapis", "edit", "tutor", "coord", "gestao"]:
            if campo in ["lapis", "edit", "tutor"]:
                df.loc[df["ID"] == oid, "ATT"] = request.form.get("at_tutor", "")
                if campo == "tutor":
                    df.loc[df["ID"] == oid, "FT"] = "Não"
            if campo in ["lapis", "edit", "coord"]:
                df.loc[df["ID"] == oid, "ATC"] = request.form.get("at_coord", "")
                if campo == "coord":
                    df.loc[df["ID"] == oid, "FC"] = "Não"
            if campo in ["lapis", "edit", "gestao"]:
                df.loc[df["ID"] == oid, "ATG"] = request.form.get("at_gestao", "")
                if campo == "gestao":
                    df.loc[df["ID"] == oid, "FG"] = "Não"
            if campo in ["lapis", "edit"]:
                df.loc[df["ID"] == oid, "Atendimento Professor"] = request.form.get("at_professor", "")

        linha_atual = df[df["ID"] == oid].iloc[0]
        df.loc[df["ID"] == oid, "Status"] = calc_status(
            linha_atual["FT"], linha_atual["FC"], linha_atual["FG"]
        )
        # backup_dados() REMOVIDO
        salvar_dados(df) # SALVA NO SHEETS
        return redirect(url_for("index"))

    permissoes = {
        "professor": campo in ["lapis", "edit"],
        "tutor": campo in ["lapis", "edit", "tutor"],
        "coord": campo in ["lapis", "edit", "coord"],
        "gestao": campo in ["lapis", "edit", "gestao"]
    }
    return render_template("editar.html", ocorrencia=registro, permissoes=permissoes, campo=campo)

@app.route("/visualizar/<int:oid>")
def visualizar(oid):
    df = carregar_dados() # LÊ DO SHEETS
    linha = df[df["ID"] == oid]
    if linha.empty:
        return "Ocorrência não encontrada", 404
    registro = linha.iloc[0].to_dict()
    permissoes = {"professor": False, "tutor": False, "coord": False, "gestao": False}
    return render_template("editar.html", ocorrencia=registro, permissoes=permissoes, campo="view")

# -------------------- API de alunos --------------------
@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    try:
        # --- NOVO: Lendo Alunos da Aba "Alunos" ---
        df_alunos = carregar_dados(sheet_name="Alunos")
        
        # Mantendo sua lógica de tratamento e filtro:
        if df_alunos.empty:
             return jsonify([])

        df_alunos["Sala"] = df_alunos["Sala"].astype(str).str.strip()
        df_alunos["Aluno"] = df_alunos["Aluno"].astype(str).str.strip()
        df_alunos["Tutor"] = df_alunos["Tutor"].astype(str).str.strip()
        df_alunos = df_alunos.dropna(subset=["Sala", "Aluno"])
        
        sala = str(sala).strip()
        pattern = re.escape(sala)
        dados = df_alunos[df_alunos["Sala"].str.contains(pattern, case=False, na=False)][["Aluno", "Tutor"]].to_dict(orient="records")
        return jsonify(dados)
    except Exception as e:
        print(f"Erro ao ler alunos/tutores para a sala '{sala}':", e)
        return jsonify([])

# -------------------- Relatórios --------------------
@app.route("/relatorio_inicial")
def relatorio_inicial():
    return render_template("relatorio_inicial.html")

@app.route("/relatorio_aluno")
def relatorio_aluno():
    sala = request.args.get("sala")
    aluno = request.args.get("aluno")
    df = carregar_dados() # LÊ DO SHEETS
    salas = df["Sala"].dropna().unique().tolist()
    alunos = []
    if sala:
        alunos = df[df["Sala"].str.strip().str.lower() == sala.strip().lower()]["Aluno"].dropna().unique().tolist()
    ocorrencias = []
    if sala and aluno:
        ocorrencias = df[
            (df["Sala"].str.strip().str.lower() == sala.strip().lower()) &
            (df["Aluno"].str.strip().str.lower() == aluno.strip().lower())
        ].to_dict(orient="records")
    return render_template("relatorio_aluno.html", salas=salas, alunos=alunos, sala_sel=sala, aluno_sel=aluno, ocorrencias=ocorrencias)

@app.route("/relatorio_geral")
def relatorio_geral():
    return render_template("relatorio_geral.html")

@app.route("/relatorio_tutor")
def relatorio_tutor():
    return render_template("relatorio_tutor.html")

@app.route("/relatorio_tutoraluno")
def relatorio_tutoraluno():
    return render_template("relatorio_tutoraluno.html")
    
@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    sala = request.form.get("sala")
    aluno = request.form.get("aluno")
    df = carregar_dados() # LÊ DO SHEETS
    if sala and aluno:
        ocorrencias = df[
            (df["Sala"].str.strip().str.lower() == sala.strip().lower()) &
            (df["Aluno"].str.strip().str.lower() == aluno.strip().lower())
        ]
    else:
        ocorrencias = pd.DataFrame()

    # Gerar PDF (exemplo simples com FPDF)
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Relatório de {aluno} - Sala {sala}", ln=True)
    for i, row in ocorrencias.iterrows():
        pdf.multi_cell(0, 10, txt=f"{row['ID']} - {row['Descrição da Ocorrência']}")
    output = BytesIO()
    pdf.output(output)
    output.seek(0)
    return send_file(output, download_name=f"relatorio_{aluno}.pdf", as_attachment=True)
    
@app.route("/abrir_pendencia/<oid>/<papel>")
def abrir_pendencia(oid, papel):
    df = carregar_dados() # LÊ DO SHEETS
    ocorrencia = df[df["ID"] == int(oid)].iloc[0]
    
    if papel == "tutor":
        return render_template("pendencia_tutor.html", ocorrencia=ocorrencia)
    elif papel == "aluno":
        return render_template("pendencia_aluno.html", ocorrencia=ocorrencia)
    else:
        return "Papel inválido", 400


# -------------------- Executar --------------------
if __name__ == "__main__":
    app.run(debug=True)