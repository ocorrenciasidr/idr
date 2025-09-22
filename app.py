import os
from datetime import datetime
from io import BytesIO

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file

# ReportLab (gera o PDF)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELO_EXCEL = os.path.join(BASE_DIR, "ControleOcorrencias.xlsx")
DADOS_EXCEL = os.path.join(BASE_DIR, "dados_ocorrencias.xlsx")

app = Flask(__name__)


# -------------------- Funções auxiliares --------------------
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
    return "Finalizada" if ft == "Não" and fc == "Não" and fg == "Não" else "Em Atendimento"


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
            df = df[df["Tutor"] == tutor]
        if status:
            df = df[df["Status"] == status]
        if sala:
            df = df[df["Sala"] == sala]
        if aluno:
            df = df[df["Aluno"] == aluno]

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
    agora = datetime.now()

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
        "FlagTutor": "Não",
        "FlagCoord": "Não",
        "FlagGestao": "Não",
        "Data Atendimento Tutor": "",
        "Data Atendimento Coord": "",
        "Data Atendimento Gestao": "",
        "Status": "Em Atendimento",
    }
    df = pd.concat([df, pd.DataFrame([nova])], ignore_index=True)
    salvar_dados(df)
    return redirect(url_for("index"))


@app.route("/abrir_pendencia/<int:oid>/<papel>")
def abrir_pendencia(oid, papel):
    df = carregar_dados()
    if oid not in df["Nº Ocorrência"].astype("Int64").values:
        return "Ocorrência não encontrada", 404
    if papel == "tutor":
        df.loc[df["Nº Ocorrência"] == oid, "FlagTutor"] = "Sim"
    elif papel == "coord":
        df.loc[df["Nº Ocorrência"] == oid, "FlagCoord"] = "Sim"
    elif papel == "gestao":
        df.loc[df["Nº Ocorrência"] == oid, "FlagGestao"] = "Sim"

    linha = df[df["Nº Ocorrência"] == oid].iloc[0]
    df.loc[df["Nº Ocorrência"] == oid, "Status"] = calc_status(
        linha["FlagTutor"], linha["FlagCoord"], linha["FlagGestao"]
    )
    salvar_dados(df)
    return redirect(url_for("editar", oid=oid, campo=papel))


@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    campo = request.args.get("campo", "").strip()
    df = carregar_dados()
    linha = df[df["Nº Ocorrência"] == oid]
    if linha.empty:
        return "Ocorrência não encontrada", 404

    registro = linha.iloc[0].to_dict()

    if request.method == "POST":
        if campo in ["edit", "tutor", "coord", "gestao"]:
            if campo in ["edit", "tutor"]:
                df.loc[df["Nº Ocorrência"] == oid, "Atendimento Tutor"] = request.form.get("at_tutor", "")
                if campo == "tutor":
                    df.loc[df["Nº Ocorrência"] == oid, "FlagTutor"] = "Não"
            if campo in ["edit", "coord"]:
                df.loc[df["Nº Ocorrência"] == oid, "Atendimento Coordenação"] = request.form.get("at_coord", "")
                if campo == "coord":
                    df.loc[df["Nº Ocorrência"] == oid, "FlagCoord"] = "Não"
            if campo in ["edit", "gestao"]:
                df.loc[df["Nº Ocorrência"] == oid, "Atendimento Gestão"] = request.form.get("at_gestao", "")
                if campo == "gestao":
                    df.loc[df["Nº Ocorrência"] == oid, "FlagGestao"] = "Não"
            if campo == "edit":
                df.loc[df["Nº Ocorrência"] == oid, "Atendimento Professor"] = request.form.get("at_professor", "")

        linha_atual = df[df["Nº Ocorrência"] == oid].iloc[0]
        df.loc[df["Nº Ocorrência"] == oid, "Status"] = calc_status(
            linha_atual["FlagTutor"], linha_atual["FlagCoord"], linha_atual["FlagGestao"]
        )
        salvar_dados(df)
        return redirect(url_for("index"))

    permissoes = {
        "professor": campo == "edit",
        "tutor": campo in ["edit", "tutor"],
        "coord": campo in ["edit", "coord"],
        "gestao": campo in ["edit", "gestao"]
    }
    return render_template("editar.html", ocorrencia=registro, permissoes=permissoes)


# -------------------- APIs --------------------
@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    """
    Retorna todos os alunos e seus tutores da sala informada.
    """
    try:
        df_alunos = pd.read_excel(MODELO_EXCEL, sheet_name="Alunos", header=None)
        df_alunos.columns = ["Sala", "Aluno", "Tutor"]

        sala = sala.strip()
        dados = (
            df_alunos[df_alunos["Sala"].astype(str).str.strip() == sala]
            [["Aluno", "Tutor"]]
            .dropna(subset=["Aluno"])
            .to_dict(orient="records")
        )
        return jsonify(dados)
    except Exception as e:
        print("Erro ao ler alunos/tutores:", e)
        return jsonify([])


# -------------------- Relatórios --------------------
@app.route("/relatorio_inicial")
def relatorio_inicial():
    return render_template("relatorio_inicial.html")


@app.route("/relatorio_aluno", methods=["GET"])
def relatorio_aluno():
    df = carregar_dados()
    salas = sorted(df["Sala"].dropna().unique().tolist()) if not df.empty else []

    sala_sel = request.args.get("sala", "").strip()
    aluno_sel = request.args.get("aluno", "").strip()

    alunos = []
    ocorrencias = []
    if sala_sel and not df.empty:
        alunos = sorted(df.loc[df["Sala"] == sala_sel, "Aluno"].dropna().unique().tolist())
    if sala_sel and aluno_sel and not df.empty:
        ocorrencias = df[
            (df["Sala"] == sala_sel) & (df["Aluno"] == aluno_sel)
        ].sort_values("Nº Ocorrência").to_dict(orient="records")

    return render_template(
        "relatorio_aluno.html",
        salas=salas,
        alunos=alunos,
        sala_sel=sala_sel,
        aluno_sel=aluno_sel,
        ocorrencias=ocorrencias
    )


@app.route("/relatorio_geral")
def relatorio_geral():
    return render_template("relatorio_geral.html")


@app.route("/relatorio_tutor")
def relatorio_tutor():
    return render_template("relatorio_tutor.html")


@app.route("/relatorio_tutoraluno")
def relatorio_tutoraluno():
    return render_template("relatorio_tutoraluno.html")


@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")


# -------------------- Gerar PDF --------------------
@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    selecionadas = request.form.getlist("ocorrencias") or request.form.getlist("selecionadas") or []
    aluno = request.form.get("aluno", "").strip()

    if not selecionadas:
        return "<h3 style='text-align:center;margin-top:30px'>Nenhuma ocorrência selecionada.</h3>", 400

    df = carregar_dados()
    selecionadas_str = set([str(x) for x in selecionadas])
    df["__NUM__"] = df["Nº Ocorrência"].astype(str)
    df_sel = df[df["__NUM__"].isin(selecionadas_str)].sort_values("Nº Ocorrência")
    if df_sel.empty:
        return "<h3 style='text-align:center;margin-top:30px'>Ocorrências não encontradas.</h3>", 404

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("<b>RELATÓRIO DE REGISTRO DE OCORRÊNCIAS - IDR</b>", styles["Title"]))
    story.append(Spacer(1, 12))

    for _, r in df_sel.iterrows():
        story.append(Paragraph(f"<b>Nº Ocorrência:</b> {r['Nº Ocorrência']}", styles["Heading3"]))
        story.append(Paragraph(f"<b>Data/Hora:</b> {r['Data Criação']} {r.get('Hora Criação','')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Professor:</b> {r.get('Professor','')}  |  <b>Sala:</b> {r.get('Sala','')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Aluno:</b> {r.get('Aluno','')}  |  <b>Tutor:</b> {r.get('Tutor','')}", styles["Normal"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"<b>Descrição:</b> {r.get('Descrição da Ocorrência','')}", styles["Normal"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"<b>Atendimento Professor:</b> {r.get('Atendimento Professor','')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Atendimento Tutor:</b> {r.get('Atendimento Tutor','')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Atendimento Coordenação:</b> {r.get('Atendimento Coord','') or r.get('Atendimento Coordenação','')}", styles["Normal"]))
        story.append(Paragraph(f"<b>Atendimento Gestão:</b> {r.get('Atendimento Gestão','')}", styles["Normal"]))
        story.append(Spacer(1, 12))

    story.append(Spacer(1, 20))
    story.append(Paragraph("Data: ________________________________", styles["Normal"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Assinatura: ___________________________", styles["Normal"]))

    doc.build(story)
    buffer.seek(0)
    nome_pdf = f"Relatorio_{(aluno.replace(' ', '_') if aluno else 'ocorrencias')}.pdf"
    return send_file(buffer, as_attachment=True, download_name=nome_pdf, mimetype="application/pdf")


# -------------------- Main --------------------
if __name__ == "__main__":
    app.run(debug=True)
