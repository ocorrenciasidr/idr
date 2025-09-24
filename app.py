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

# Matplotlib import removed from relatorio_geral view (we're not using it now)
# import matplotlib.pyplot as plt

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
    """Cria backup do arquivo principal com timestamp"""
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
    """Se o banco principal sumir, restaura o último backup"""
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
    salvar_dados(df)
    backup_dados()
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

# ------------------ NOVAS ROTAS: Visualizar e Senha ------------------
@app.route("/visualizar/<int:oid>")
def visualizar(oid):
    """Abre a ocorrência em modo somente leitura (todos os campos bloqueados)."""
    df = carregar_dados()
    linha = df[df["Nº Ocorrência"] == oid]
    if linha.empty:
        return "Ocorrência não encontrada", 404
    registro = linha.iloc[0].to_dict()
    permissoes = {"professor": False, "tutor": False, "coord": False, "gestao": False}
    return render_template("editar.html", ocorrencia=registro, permissoes=permissoes)

@app.route("/senha/<int:oid>", methods=["GET", "POST"])
def senha(oid):
    """
    Tela de senha. Se senha correta redireciona para /editar/<oid>?campo=edit
    (o seu fluxo original de edição via ?campo=edit é preservado).
    """
    if request.method == "POST":
        senha_fornecida = request.form.get("senha", "")
        if senha_fornecida == "IDR@2025":
            # redireciona para abrir a ocorrência em modo edição completa (campo=edit)
            return redirect(url_for("editar", oid=oid, campo="edit"))
        else:
            flash("Senha incorreta!", "danger")
            return render_template("senha.html", oid=oid)
    return render_template("senha.html", oid=oid)

# -------------------- Editar --------------------
@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    campo = request.args.get("campo", "").strip()
    df = carregar_dados()
    linha = df[df["Nº Ocorrência"] == oid]
    if linha.empty:
        return "Ocorrência não encontrada", 404

    registro = linha.iloc[0].to_dict()

    if request.method == "POST":
        # Mantive sua lógica original: se campo == edit/tutor/coord/gestao atualiza os campos correspondentes
        if campo in ["edit", "tutor", "coord", "gestao"]:
            if campo in ["edit", "tutor"]:
                df.loc[df["Nº Ocorrência"] == oid, "Atendimento Tutor"] = request.form.get("at_tutor", "")
                # se foi uma edição específica do tutor (papel == tutor), marca FlagTutor como Não (resolvido)
                if campo == "tutor":
                    df.loc[df["Nº Ocorrência"] == oid, "FlagTutor"] = "Não"
                # se for edit completo (campo == edit) não altero a flag automaticamente

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

            # Atualiza Status com base nas flags atuais
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

@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    sala = request.form.get("sala")
    aluno = request.form.get("aluno")
    df = carregar_dados()
    df_filtro = df[
        (df["Sala"].str.strip().str.lower() == sala.strip().lower()) &
        (df["Aluno"].str.strip().str.lower() == aluno.strip().lower())
    ]

    if df_filtro.empty:
        return "Nenhuma ocorrência encontrada para este aluno.", 404

    # Atualiza status para "Assinada"
    df.loc[df.index.isin(df_filtro.index), "Status"] = "Assinada"
    salvar_dados(df)
    backup_dados()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(f"Relatório de Ocorrências do Aluno: {aluno}", styles["Title"]))
    story.append(Spacer(1, 12))

    for _, row in df_filtro.iterrows():
        story.append(Paragraph(f"Nº Ocorrência: {row['Nº Ocorrência']}", styles["Normal"]))
        story.append(Paragraph(f"Data: {row['Data Criação']}", styles["Normal"]))
        story.append(Paragraph(f"Sala: {row['Sala']}", styles["Normal"]))
        story.append(Paragraph(f"Descrição: {row['Descrição da Ocorrência']}", styles["Normal"]))
        story.append(Paragraph(f"Atendimento Professor: {row['Atendimento Professor']}", styles["Normal"]))
        story.append(Paragraph(f"Atendimento Tutor: {row['Atendimento Tutor']}", styles["Normal"]))
        story.append(Paragraph(f"Atendimento Coordenação: {row['Atendimento Coordenação']}", styles["Normal"]))
        story.append(Paragraph(f"Atendimento Gestão: {row['Atendimento Gestão']}", styles["Normal"]))
        story.append(Paragraph(f"Status: {row['Status']}", styles["Normal"]))
        story.append(Spacer(1, 12))

    # Espaço para assinatura
    story.append(Spacer(1, 24))
    story.append(Paragraph("Assinatura do Responsável: _______________________", styles["Normal"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Data: {datetime.now(TZ_SAO).strftime('%d/%m/%Y')}", styles["Normal"]))

    doc.build(story)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"relatorio_{aluno}.pdf",
        mimetype="application/pdf"
    )

# -------------------- Relatório Geral (sem gráfico, só tabelas) --------------------
def _parse_date_safe(val):
    """Tenta parsear datas; retorna pd.NaT em erro."""
    try:
        return pd.to_datetime(val, errors="coerce")
    except Exception:
        return pd.NaT

@app.route("/relatorio_geral")
def relatorio_geral():
    df = carregar_dados()
    if df.empty:
        return render_template("relatorio_geral.html", salas_stats=[], resumo_geral={}, resumo_setor=[])

    # Garante Status calculado conforme flags
    df["Status"] = df.apply(lambda r: calc_status(r["FlagTutor"], r["FlagCoord"], r["FlagGestao"]), axis=1)

    # Preparar colunas de data para calcular prazos
    df["Data Criação_dt"] = df["Data Criação"].apply(_parse_date_safe)
    df["Data Atendimento Tutor_dt"] = df["Data Atendimento Tutor"].apply(_parse_date_safe)
    df["Data Atendimento Coord_dt"] = df["Data Atendimento Coord"].apply(_parse_date_safe)
    df["Data Atendimento Gestao_dt"] = df["Data Atendimento Gestao"].apply(_parse_date_safe)

    # --- Ocorrências por Sala (total + % + respondidas <7/ >7 / não respondidas)
    total_ocorrencias = len(df)
    salas = df["Sala"].fillna("Sem Sala").astype(str).unique().tolist()
    salas_stats = []
    for s in salas:
        sub = df[df["Sala"].fillna("Sem Sala").astype(str) == s]
        total = len(sub)
        pct = (total / total_ocorrencias * 100) if total_ocorrencias else 0

        # Para os contadores abaixo consideramos "solicitação" quando ANY Flag == "Sim" nesta linha
        # E consideramos "respondido por qualquer setor" verificando se existe data de atendimento preenchida
        # para os setores que foram solicitados.
        def _row_response_category(row):
            # retorna: "resp_lt7", "resp_gt7", "nao"
            # verifica cada setor solicitado
            criacao = row["Data Criação_dt"]
            responses_days = []
            # Tutor
            if str(row.get("FlagTutor", "")).strip().lower() == "sim":
                if pd.notna(row["Data Atendimento Tutor_dt"]) and pd.notna(criacao):
                    responses_days.append((row["Data Atendimento Tutor_dt"] - criacao).days)
            # Coord
            if str(row.get("FlagCoord", "")).strip().lower() == "sim":
                if pd.notna(row["Data Atendimento Coord_dt"]) and pd.notna(criacao):
                    responses_days.append((row["Data Atendimento Coord_dt"] - criacao).days)
            # Gestao
            if str(row.get("FlagGestao", "")).strip().lower() == "sim":
                if pd.notna(row["Data Atendimento Gestao_dt"]) and pd.notna(criacao):
                    responses_days.append((row["Data Atendimento Gestao_dt"] - criacao).days)

            if responses_days:
                min_days = min(responses_days)
                if min_days <= 7:
                    return "resp_lt7"
                else:
                    return "resp_gt7"
            else:
                # Se não existirem respostas (para os setores solicitados), é "nao"
                return "nao"

        resp_lt7 = 0
        resp_gt7 = 0
        nao_resp = 0
        # Consideramos apenas linhas onde ao menos um Flag == Sim como "solicitação"
        solicitado = sub[
            (sub["FlagTutor"].astype(str).str.strip().str.lower() == "sim") |
            (sub["FlagCoord"].astype(str).str.strip().str.lower() == "sim") |
            (sub["FlagGestao"].astype(str).str.strip().str.lower() == "sim")
        ]
        for _, row in solicitado.iterrows():
            cat = _row_response_category(row)
            if cat == "resp_lt7":
                resp_lt7 += 1
            elif cat == "resp_gt7":
                resp_gt7 += 1
            else:
                nao_resp += 1

        salas_stats.append({
            "sala": s,
            "total": total,
            "porcentagem": f"{pct:.1f}%",
            "resp_lt7": resp_lt7,
            "resp_gt7": resp_gt7,
            "nao_resp": nao_resp
        })

    # --- Resumo Geral (sobre ocorrências que tiveram solicitação de resposta)
    solicitado_total = df[
        (df["FlagTutor"].astype(str).str.strip().str.lower() == "sim") |
        (df["FlagCoord"].astype(str).str.strip().str.lower() == "sim") |
        (df["FlagGestao"].astype(str).str.strip().str.lower() == "sim")
    ]
    total_solicitado = len(solicitado_total)
    geral_resp_lt7 = 0
    geral_resp_gt7 = 0
    geral_nao = 0
    for _, row in solicitado_total.iterrows():
        # reutiliza a mesma função de categorização
        criacao = row["Data Criação_dt"]
        responses_days = []
        if str(row.get("FlagTutor", "")).strip().lower() == "sim":
            if pd.notna(row["Data Atendimento Tutor_dt"]) and pd.notna(criacao):
                responses_days.append((row["Data Atendimento Tutor_dt"] - criacao).days)
        if str(row.get("FlagCoord", "")).strip().lower() == "sim":
            if pd.notna(row["Data Atendimento Coord_dt"]) and pd.notna(criacao):
                responses_days.append((row["Data Atendimento Coord_dt"] - criacao).days)
        if str(row.get("FlagGestao", "")).strip().lower() == "sim":
            if pd.notna(row["Data Atendimento Gestao_dt"]) and pd.notna(criacao):
                responses_days.append((row["Data Atendimento Gestao_dt"] - criacao).days)

        if responses_days:
            if min(responses_days) <= 7:
                geral_resp_lt7 += 1
            else:
                geral_resp_gt7 += 1
        else:
            geral_nao += 1

    resumo_geral = {
        "total_solicitado": total_solicitado,
        "resp_lt7_num": geral_resp_lt7,
        "resp_lt7_pct": f"{(geral_resp_lt7 / total_solicitado * 100):.1f}%" if total_solicitado else "0.0%",
        "resp_gt7_num": geral_resp_gt7,
        "resp_gt7_pct": f"{(geral_resp_gt7 / total_solicitado * 100):.1f}%" if total_solicitado else "0.0%",
        "nao_num": geral_nao,
        "nao_pct": f"{(geral_nao / total_solicitado * 100):.1f}%" if total_solicitado else "0.0%"
    }

    # --- Resumo por Setor (Tutor, Coordenação, Gestão) - contamos apenas linhas onde o setor foi solicitado
    def setor_stats(flag_col, data_col):
        subset = df[df[flag_col].astype(str).str.strip().str.lower() == "sim"]
        total = len(subset)
        lt7 = 0
        gt7 = 0
        nao = 0
        for _, row in subset.iterrows():
            criacao = row["Data Criação_dt"]
            data_resp = row[data_col]
            if pd.notna(data_resp) and pd.notna(criacao):
                dias = (data_resp - criacao).days
                if dias <= 7:
                    lt7 += 1
                else:
                    gt7 += 1
            else:
                nao += 1
        return {
            "total": total,
            "lt7": lt7,
            "lt7_pct": f"{(lt7 / total * 100):.1f}%" if total else "0.0%",
            "gt7": gt7,
            "gt7_pct": f"{(gt7 / total * 100):.1f}%" if total else "0.0%",
            "nao": nao,
            "nao_pct": f"{(nao / total * 100):.1f}%" if total else "0.0%"
        }

    resumo_setor = {
        "Tutor": setor_stats("FlagTutor", "Data Atendimento Tutor_dt"),
        "Coordenação": setor_stats("FlagCoord", "Data Atendimento Coord_dt"),
        "Gestão": setor_stats("FlagGestao", "Data Atendimento Gestao_dt")
    }

    # Renderiza a página com as estruturas já calculadas (sem gráfico)
    return render_template(
        "relatorio_geral.html",
        salas_stats=salas_stats,
        resumo_geral=resumo_geral,
        resumo_setor=resumo_setor
    )

@app.route("/relatorio_tutor")
def relatorio_tutor():
    return render_template("relatorio_tutor.html")

@app.route("/relatorio_tutoraluno")
def relatorio_tutoraluno():
    return render_template("relatorio_tutoraluno.html")

@app.route("/gerar_pdf_geral", methods=["POST"])
def gerar_pdf_geral():
    """
    Gera o PDF do Relatório Geral contendo 3 tabelas:
     - Ocorrências por Sala (com número e porcentagem)
     - Resumo Geral (Respondidas <7, >7, Não Respondidas) — considerando apenas ocorrências que tiveram solicitação
     - Resumo por Setor (Tutor / Coordenação / Gestão)
    Também atualiza o Status para 'Assinada' e faz backup (conforme pedido).
    """
    df = carregar_dados()
    if df.empty:
        return "Nenhuma ocorrência encontrada.", 404

    # Atualiza status de todas as ocorrências para "Assinada" (mesma lógica que você usou)
    df["Status"] = "Assinada"
    salvar_dados(df)
    backup_dados()

    # Recalcula as mesmas estatísticas que a view /relatorio_geral provê
    # Para manter código enxuto, chamamos a view logic localmente (poderia refatorar em função)
    # Build minimal stats here (reusing same approach as relatorio_geral)
    df["Data Criação_dt"] = df["Data Criação"].apply(_parse_date_safe)
    df["Data Atendimento Tutor_dt"] = df["Data Atendimento Tutor"].apply(_parse_date_safe)
    df["Data Atendimento Coord_dt"] = df["Data Atendimento Coord"].apply(_parse_date_safe)
    df["Data Atendimento Gestao_dt"] = df["Data Atendimento Gestao"].apply(_parse_date_safe)

    total_ocorrencias = len(df)
    salas = df["Sala"].fillna("Sem Sala").astype(str).unique().tolist()
    data_salas = [["Sala", "Total Ocorrências", "Porcentagem", "Respondidas <7 dias", "Respondidas >7 dias", "Não Respondidas"]]
    for s in salas:
        sub = df[df["Sala"].fillna("Sem Sala").astype(str) == s]
        total = len(sub)
        pct = (total / total_ocorrencias * 100) if total_ocorrencias else 0

        solicitado = sub[
            (sub["FlagTutor"].astype(str).str.strip().str.lower() == "sim") |
            (sub["FlagCoord"].astype(str).str.strip().str.lower() == "sim") |
            (sub["FlagGestao"].astype(str).str.strip().str.lower() == "sim")
        ]

        resp_lt7 = resp_gt7 = nao_resp = 0
        for _, row in solicitado.iterrows():
            criacao = row["Data Criação_dt"]
            responses_days = []
            if str(row.get("FlagTutor", "")).strip().lower() == "sim":
                if pd.notna(row["Data Atendimento Tutor_dt"]) and pd.notna(criacao):
                    responses_days.append((row["Data Atendimento Tutor_dt"] - criacao).days)
            if str(row.get("FlagCoord", "")).strip().lower() == "sim":
                if pd.notna(row["Data Atendimento Coord_dt"]) and pd.notna(criacao):
                    responses_days.append((row["Data Atendimento Coord_dt"] - criacao).days)
            if str(row.get("FlagGestao", "")).strip().lower() == "sim":
                if pd.notna(row["Data Atendimento Gestao_dt"]) and pd.notna(criacao):
                    responses_days.append((row["Data Atendimento Gestao_dt"] - criacao).days)

            if responses_days:
                if min(responses_days) <= 7:
                    resp_lt7 += 1
                else:
                    resp_gt7 += 1
            else:
                nao_resp += 1

        data_salas.append([s, total, f"{pct:.1f}%", resp_lt7, resp_gt7, nao_resp])

    # Resumo Geral
    solicitado_total = df[
        (df["FlagTutor"].astype(str).str.strip().str.lower() == "sim") |
        (df["FlagCoord"].astype(str).str.strip().str.lower() == "sim") |
        (df["FlagGestao"].astype(str).str.strip().str.lower() == "sim")
    ]
    total_solicitado = len(solicitado_total)
    geral_resp_lt7 = geral_resp_gt7 = geral_nao = 0
    for _, row in solicitado_total.iterrows():
        criacao = row["Data Criação_dt"]
        responses_days = []
        if str(row.get("FlagTutor", "")).strip().lower() == "sim":
            if pd.notna(row["Data Atendimento Tutor_dt"]) and pd.notna(criacao):
                responses_days.append((row["Data Atendimento Tutor_dt"] - criacao).days)
        if str(row.get("FlagCoord", "")).strip().lower() == "sim":
            if pd.notna(row["Data Atendimento Coord_dt"]) and pd.notna(criacao):
                responses_days.append((row["Data Atendimento Coord_dt"] - criacao).days)
        if str(row.get("FlagGestao", "")).strip().lower() == "sim":
            if pd.notna(row["Data Atendimento Gestao_dt"]) and pd.notna(criacao):
                responses_days.append((row["Data Atendimento Gestao_dt"] - criacao).days)

        if responses_days:
            if min(responses_days) <= 7:
                geral_resp_lt7 += 1
            else:
                geral_resp_gt7 += 1
        else:
            geral_nao += 1

    data_resumo = [
        ["Tipo", "Número", "Porcentagem"],
        ["Respondidas <7 dias", geral_resp_lt7, f"{(geral_resp_lt7/total_solicitado*100):.1f}%" if total_solicitado else "0.0%"],
        ["Respondidas >7 dias", geral_resp_gt7, f"{(geral_resp_gt7/total_solicitado*100):.1f}%" if total_solicitado else "0.0%"],
        ["Não Respondidas", geral_nao, f"{(geral_nao/total_solicitado*100):.1f}%" if total_solicitado else "0.0%"],
    ]

    # Resumo por setor
    def setor_table_rows(flag_col, data_col, nome):
        subset = df[df[flag_col].astype(str).str.strip().str.lower() == "sim"]
        total = len(subset)
        lt7 = gt7 = nao = 0
        for _, row in subset.iterrows():
            criacao = row["Data Criação_dt"]
            if pd.notna(row[data_col]) and pd.notna(criacao):
                dias = (row[data_col] - criacao).days
                if dias <= 7:
                    lt7 += 1
                else:
                    gt7 += 1
            else:
                nao += 1
        return [nome, total, f"{lt7} ({(lt7/total*100):.1f}%)" if total else f"0 (0.0%)",
                f"{gt7} ({(gt7/total*100):.1f}%)" if total else f"0 (0.0%)",
                f"{nao} ({(nao/total*100):.1f}%)" if total else f"0 (0.0%)"]

    data_setor = [
        ["Setor", "Total", "Respondidas <7 dias", "Respondidas >7 dias", "Não Respondidas"],
        setor_table_rows("FlagTutor", "Data Atendimento Tutor_dt", "Tutor"),
        setor_table_rows("FlagCoord", "Data Atendimento Coord_dt", "Coordenação"),
        setor_table_rows("FlagGestao", "Data Atendimento Gestao_dt", "Gestão"),
    ]

    # Montar PDF com 3 tabelas
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Relatório Geral de Ocorrências", styles["Title"]))
    story.append(Spacer(1, 12))

    # Tabela 1: Ocorrências por Sala
    story.append(Paragraph("Ocorrências por Sala", styles["Heading2"]))
    t1 = Table(data_salas, hAlign="LEFT")
    t1.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("ALIGN", (1,1), (-1,-1), "CENTER"),
    ]))
    story.append(t1)
    story.append(Spacer(1, 12))

    # Tabela 2: Resumo Geral
    story.append(Paragraph("Resumo Geral", styles["Heading2"]))
    t2 = Table(data_resumo, hAlign="LEFT")
    t2.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("ALIGN", (1,1), (-1,-1), "CENTER"),
    ]))
    story.append(t2)
    story.append(Spacer(1, 12))

    # Tabela 3: Resumo por Setor
    story.append(Paragraph("Resumo por Setor", styles["Heading2"]))
    t3 = Table(data_setor, hAlign="LEFT")
    t3.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("ALIGN", (1,1), (-1,-1), "CENTER"),
    ]))
    story.append(t3)

    doc.build(story)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name="Relatorio_Geral.pdf",
        mimetype="application/pdf"
    )

# -------------------- Main --------------------
if __name__ == "__main__":
    app.run(debug=True)
