import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
import shutil
import base64

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELO_EXCEL = os.path.join(BASE_DIR, "ControleOcorrencias.xlsx")
DADOS_EXCEL = os.path.join(BASE_DIR, "dados_ocorrencias.xlsx")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "sua_chave_secreta"

# -------------------- Funções auxiliares --------------------
def backup_dados():
    if os.path.exists(DADOS_EXCEL):
        agora = datetime.now(TZ_SAO)
        nome_backup = f"dados_ocorrencias_{agora.strftime('%Y%m%d_%H%M%S')}.xlsx"
        caminho = os.path.join(BACKUP_DIR, nome_backup)
        try:
            shutil.copy2(DADOS_EXCEL, caminho)
            print(f"[BACKUP] Criado: {caminho}")
        except Exception as e:
            print("Erro ao criar backup:", e)

def restaurar_backup():
    if not os.path.exists(DADOS_EXCEL) and os.path.exists(BACKUP_DIR):
        backups = sorted(
            [f for f in os.listdir(BACKUP_DIR) if f.startswith("dados_ocorrencias_")],
            reverse=True
        )
        if backups:
            ultimo_backup = os.path.join(BACKUP_DIR, backups[0])
            shutil.copy2(ultimo_backup, DADOS_EXCEL)
            print(f"[RESTORE] Base recuperada do backup: {ultimo_backup}")

def carregar_dados():
    cols = [
        "Nº Ocorrência", "Data Criação", "Hora Criação",
        "Professor", "Sala", "Aluno", "Tutor",
        "Descrição da Ocorrência",
        "Atendimento Professor", "Atendimento Tutor",
        "Atendimento Coordenação", "Atendimento Gestão",
        "FlagTutor", "FlagCoord", "FlagGestao",
        "Data Atendimento Tutor", "Data Atendimento Coord", "Data Atendimento Gestao",
        "Status"
    ]

    restaurar_backup()

    if not os.path.exists(DADOS_EXCEL):
        return pd.DataFrame(columns=cols)

    try:
        df = pd.read_excel(DADOS_EXCEL)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        df["Aluno"] = df["Aluno"].astype(str).str.strip()
        df["Sala"] = df["Sala"].astype(str).str.strip()
        try:
            df["Nº Ocorrência"] = pd.to_numeric(df["Nº Ocorrência"], errors="coerce").astype("Int64")
        except Exception:
            pass
        return df[cols]
    except Exception as e:
        print("Erro ao ler dados:", e)
        return pd.DataFrame(columns=cols)

def salvar_dados(df):
    df.to_excel(DADOS_EXCEL, index=False)

def calc_status(ft, fc, fg):
    return "Finalizada" if str(ft).strip().lower() == "não" and str(fc).strip().lower() == "não" and str(fg).strip().lower() == "não" else "Em Atendimento"

def proximo_numero():
    df = carregar_dados()
    if df.empty:
        return 1
    try:
        maxv = pd.to_numeric(df["Nº Ocorrência"], errors="coerce").max()
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

    df = carregar_dados()
    if not df.empty:
        df["Status"] = df.apply(lambda r: calc_status(r["FlagTutor"], r["FlagCoord"], r["FlagGestao"]), axis=1)
        if tutor:
            df = df[df["Tutor"].str.strip().str.lower() == tutor.strip().lower()]
        if status:
            df = df[df["Status"].str.strip().str.lower() == status.strip().lower()]
        if sala:
            df = df[df["Sala"].str.strip().str.lower() == sala.strip().lower()]
        if aluno:
            df = df[df["Aluno"].str.strip().str.lower() == aluno.strip().lower()]
        df = df.sort_values(by="Nº Ocorrência", ascending=False)

    try:
        tutores_ref = pd.read_excel(MODELO_EXCEL, sheet_name="Alunos", header=None)
        tutores_ref.columns = ["Sala", "Aluno", "Tutor"]
        tutores_ref = tutores_ref["Tutor"].dropna().unique().tolist()
    except Exception:
        tutores_ref = []

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
    try:
        professores = pd.read_excel(MODELO_EXCEL, sheet_name="Professores")["Professor"].dropna().tolist()
    except Exception:
        professores = []
    try:
        salas = pd.read_excel(MODELO_EXCEL, sheet_name="Salas")["Sala"].dropna().tolist()
    except Exception:
        salas = []
    return render_template("nova.html", professores=professores, salas=salas)

@app.route("/salvar", methods=["POST"])
def salvar():
    df = carregar_dados()
    numero = proximo_numero()
    agora = datetime.now(TZ_SAO)

    nova = {
        "Nº Ocorrência": numero,
        "Data Criação": agora.strftime("%Y-%m-%d"),
        "Hora Criação": agora.strftime("%H:%M:%S"),
        "Professor": request.form.get("professor", ""),
        "Sala": request.form.get("sala", ""),
        "Aluno": request.form.get("aluno", ""),
        "Tutor": request.form.get("tutor", ""),
        "Descrição da Ocorrência": request.form.get("descricao", ""),
        "Atendimento Professor": request.form.get("at_professor", ""),
        "Atendimento Tutor": "",
        "Atendimento Coordenação": "",
        "Atendimento Gestão": "",
        "FlagTutor": "Sim" if request.form.get("flag_tutor") else "Não",
        "FlagCoord": "Sim" if request.form.get("flag_coord") else "Não",
        "FlagGestao": "Sim" if request.form.get("flag_gestao") else "Não",
        "Data Atendimento Tutor": "",
        "Data Atendimento Coord": "",
        "Data Atendimento Gestao": "",
        "Status": "Em Atendimento",
    }

    df = pd.concat([df, pd.DataFrame([nova])], ignore_index=True)
    backup_dados()
    salvar_dados(df)
    return redirect(url_for("index"))

# -------------------- Editar / Visualizar --------------------
@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    campo = request.args.get("campo", "").strip()
    df = carregar_dados()
    linha = df[df["Nº Ocorrência"] == oid]
    if linha.empty:
        return "Ocorrência não encontrada", 404

    registro = linha.iloc[0].to_dict()

    if request.method == "POST":
        if campo in ["lupa", "lapis", "edit", "tutor", "coord", "gestao"]:
            if campo in ["lapis", "edit", "tutor"]:
                df.loc[df["Nº Ocorrência"] == oid, "Atendimento Tutor"] = request.form.get("at_tutor", "")
                if campo == "tutor":
                    df.loc[df["Nº Ocorrência"] == oid, "FlagTutor"] = "Não"
            if campo in ["lapis", "edit", "coord"]:
                df.loc[df["Nº Ocorrência"] == oid, "Atendimento Coordenação"] = request.form.get("at_coord", "")
                if campo == "coord":
                    df.loc[df["Nº Ocorrência"] == oid, "FlagCoord"] = "Não"
            if campo in ["lapis", "edit", "gestao"]:
                df.loc[df["Nº Ocorrência"] == oid, "Atendimento Gestão"] = request.form.get("at_gestao", "")
                if campo == "gestao":
                    df.loc[df["Nº Ocorrência"] == oid, "FlagGestao"] = "Não"
            if campo in ["lapis", "edit"]:
                df.loc[df["Nº Ocorrência"] == oid, "Atendimento Professor"] = request.form.get("at_professor", "")

        linha_atual = df[df["Nº Ocorrência"] == oid].iloc[0]
        df.loc[df["Nº Ocorrência"] == oid, "Status"] = calc_status(
            linha_atual["FlagTutor"], linha_atual["FlagCoord"], linha_atual["FlagGestao"]
        )
        backup_dados()
        salvar_dados(df)
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
    df = carregar_dados()
    linha = df[df["Nº Ocorrência"] == oid]
    if linha.empty:
        return "Ocorrência não encontrada", 404
    registro = linha.iloc[0].to_dict()
    permissoes = {"professor": False, "tutor": False, "coord": False, "gestao": False}
    return render_template("editar.html", ocorrencia=registro, permissoes=permissoes, campo="view")

# -------------------- API de alunos --------------------
@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    try:
        df_alunos = pd.read_excel(MODELO_EXCEL, sheet_name="Alunos", header=None)
        df_alunos.columns = ["Sala", "Aluno", "Tutor"]
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
    df = carregar_dados()
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
    df = carregar_dados()
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
        pdf.multi_cell(0, 10, txt=f"{row['Nº Ocorrência']} - {row['Descrição da Ocorrência']}")
    output = BytesIO()
    pdf.output(output)
    output.seek(0)
    return send_file(output, download_name=f"relatorio_{aluno}.pdf", as_attachment=True)
@app.route("/abrir_pendencia/<oid>/<papel>")
def abrir_pendencia(oid, papel):
    df = carregar_dados()
    ocorrencia = df[df["Nº Ocorrência"] == int(oid)].iloc[0]
    
    if papel == "tutor":
        return render_template("pendencia_tutor.html", ocorrencia=ocorrencia)
    elif papel == "aluno":
        return render_template("pendencia_aluno.html", ocorrencia=ocorrencia)
    else:
        return "Papel inválido", 400


# -------------------- Executar --------------------
if __name__ == "__main__":
    app.run(debug=True)
