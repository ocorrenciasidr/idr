# app.py
import os
from io import BytesIO
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from fpdf import FPDF
from supabase import create_client, Client
import pandas as pd
from dateutil import parser as date_parser

# -------------------------- Configuração --------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rimuhgulxliduugenxro.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJpbXVoZ3VseGxpZHV1Z2VueHJvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTkzNTU3NTgsImV4cCI6MjA3NDkzMTc1OH0.h5E_WzZLbXSAaACPjDNe7GtEYQFL6nkIdU2isUNbXiA")

# Prazo (dias) para avaliar "No Prazo"
PRAZO_DIAS = int(os.environ.get("PRAZO_DIAS", 7))
TZ_SAO = timezone(timedelta(hours=-3))  # São Paulo (UTC-3)

def conectar_supabase() -> Client | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ SUPABASE_URL ou SUPABASE_KEY não configurados.")
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print("❌ Erro ao conectar ao Supabase:", e)
        return None

# -------------------------- Utilitários --------------------------
def upperize_row_keys(row):
    return {k.upper(): v for k, v in row.items()}

def normalize_checkbox(val) -> str:
    if val is None:
        return "NÃO"
    v = str(val).strip().lower()
    if v in ("1", "true", "on", "sim", "yes"):
        return "SIM"
    return "NÃO"

# -------------------------- PDF helper --------------------------
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 12)
        self.set_draw_color(0, 51, 102)
        self.cell(0, 10, 'RELATÓRIO DE REGISTRO DE OCORRÊNCIAS', 'B', 1, 'C')
        self.set_font('Arial', '', 10)
        self.cell(0, 5, 'E.E. PEI PROFESSOR IRENE DIAS RIBEIRO', 0, 1, 'C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Página {self.page_no()}/{{nb}}', 0, 0, 'C')

def adicionar_ocorrencia_ao_pdf(pdf: PDF, o: dict):
    w_label, w_value = 45, 145
    pdf.set_font('Arial', 'B', 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.set_draw_color(0, 0, 0)

    def add_meta_row(label, value):
        value_display = value if value not in (None, '') else ''
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(w_label, 7, label, 'LR', 0, 'L', 1)
        pdf.set_font('Arial', '', 10)
        pdf.cell(w_value, 7, str(value_display), 'LR', 1, 'L', 0)
        pdf.cell(w_label + w_value, 0, '', 'T', 1, 'L')

    add_meta_row('Aluno:', o.get('ALUNO', 'N/D'))
    add_meta_row('Tutor:', o.get('TUTOR', 'N/D'))
    add_meta_row('Data:', o.get('DCO', 'N/D'))
    add_meta_row('Professor:', o.get('PROFESSOR', 'N/D'))

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Sala:', 'LBR', 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value, 7, o.get('SALA', 'N/D'), 'RBT', 1, 'L', 0)
    pdf.ln(2)

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Ocorrência nº:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value / 2, 7, str(o.get('ID', 'N/D')), 1, 0, 'L')
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label / 2, 7, 'Hora:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value / 2 - w_label / 2, 7, o.get('HCO', 'N/D'), 1, 1, 'L')
    pdf.ln(5)

    def bloco(label, key):
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 7, label, 1, 1, 'L', 1)
        pdf.set_font('Arial', '', 10)
        conteudo = o.get(key, '') or ''
        if not str(conteudo).strip():
            conteudo = 'NÃO APLICÁVEL'
        pdf.multi_cell(0, 6, str(conteudo), 1, 'L', 0)
        pdf.ln(2)

    for lbl, k in [('Descrição:', 'DESCRICAO'), ('Atendimento Professor (ATP):', 'ATP'),
                   ('Atendimento Tutor (ATT):', 'ATT'), ('Atendimento Coordenação (ATC):', 'ATC'),
                   ('Atendimento Gestão (ATG):', 'ATG')]:
        bloco(lbl, k)

    pdf.ln(8)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(100, 7, 'Assinatura Responsável:', 0, 0, 'L')
    pdf.cell(0, 7, 'Data: / / ', 0, 1, 'L')
    pdf.ln(5)
    pdf.set_font('Arial', 'I', 8)
    pdf.cell(0, 5, 'Ocorrência registrada no SGCE.', 0, 1, 'R')

def normalize_checkbox(value):
    """Converte checkbox em 'SIM' ou 'NÃO'"""
    return "SIM" if value in ("on", "SIM", True) else "NÃO"


# -------------------------- Carregamento helpers --------------------------
def carregar_lookup(table_name: str, column=None) -> list:
    supabase = conectar_supabase()
    if not supabase:
        return []
    try:
        sel = "*" if column is None else column
        resp = supabase.table(table_name).select(sel).order(column if column else "id", desc=False).execute()
        if not resp.data:
            return []
        if column:
            return [r.get(column) for r in resp.data if r.get(column) is not None]
        return resp.data
    except Exception as e:
        print(f"Erro ao carregar {table_name}:", e)
        return []

def carregar_dados_ocorrencias(filtro_tutor=None, filtro_status=None) -> list:
    supabase = conectar_supabase()
    if not supabase:
        print("❌ DEBUG: Falha na conexão com o Supabase.")
        return []
    try:
        query = supabase.table("ocorrencias").select("*").order("ID", desc=True)
        if filtro_tutor and filtro_tutor != "Todos":
            query = query.eq("TUTOR", filtro_tutor)
        if filtro_status and filtro_status != "Todos":
            status_map = {"ATENDIMENTO": "ATENDIMENTO", "FINALIZADA": "FINALIZADA", "ASSINADA": "ASSINADA"}
            db_status = status_map.get(filtro_status)
            if db_status:
                query = query.eq("STATUS", db_status)
        resp = query.execute()
        data = resp.data or []
        normalized = [upperize_row_keys(r) for r in data]
        return normalized
    except Exception as e:
        print("❌ Erro ao carregar ocorrencias:", e)
        return []

def carregar_tutores_com_ocorrencias() -> list:
    supabase = conectar_supabase()
    if not supabase:
        return []
    try:
        resp = supabase.table("ocorrencias").select("TUTOR", count='exact').not_("TUTOR", "is", None).order("TUTOR", desc=False).execute()
        tutores = sorted(list(set([r['TUTOR'] for r in resp.data if r.get('TUTOR')])))
        return tutores
    except Exception as e:
        print("Erro ao carregar tutores:", e)
        return []

# -------------------------- ROTAS --------------------------
@app.route("/")
@app.route("/home")
def home():
    ano = datetime.now(TZ_SAO).year
    return render_template("home.html", ano=ano)

@app.route("/editar/<int:oid>")
def editar(oid):
    # Substitua pelo que sua função editar_completo faz
    return editar_completo(oid)



@app.route("/relatorio_inicial")
def relatorio_inicial():
    # Aqui você pode renderizar o template do relatório ou apenas retornar um texto temporário
    return render_template("relatorio_inicial.html")  # crie relatorio.html ou substitua por um retorno de teste

# app (5).py - Substitua a função index

# app (5).py - Substituir a função index (aproximadamente linha 190)

@app.route("/index")
def index():
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
        return redirect(url_for("home"))

    # 1. Pegando filtros da URL
    filtro_tutor = request.args.get("tutor_filtro")
    filtro_status = request.args.get("status_filtro")

    # 2. Buscar ocorrências JÁ filtradas (usando a função auxiliar)
    # Isso torna o filtro funcional e eficiente.
    registros = carregar_dados_ocorrencias(
        filtro_tutor=filtro_tutor,
        filtro_status=filtro_status
    )

    # 3. Gerar listas de filtros (incluindo "Todos")
    tutores_disp = ["Todos"] + carregar_tutores_com_ocorrencias()
    status_disp = ["Todos", "ATENDIMENTO", "FINALIZADA", "ASSINADA"]

    return render_template(
        "index.html",
        registros=registros,
        tutores_disp=tutores_disp,
        status_disp=status_disp,
        filtro_tutor_sel=filtro_tutor,
        filtro_status_sel=filtro_status
    ) ---
    
@app.route("/atendimento/<int:oid>/<tipo_acao>", methods=["GET", "POST"])
def atendimento(oid, tipo_acao):
    if tipo_acao not in ["FT", "FC", "FG"]:
        flash("Ação inválida.", "danger")
        return redirect(url_for("index"))
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
        return redirect(url_for("index"))
    campo_atendimento = "A" + tipo_acao[1] + "T"
    campo_data = tipo_acao[0] + "T"
    try:
        resp = supabase.table("ocorrencias").select("*").eq("ID", oid).execute()
        data = resp.data or []
        if not data:
            flash("Ocorrência não encontrada.", "danger")
            return redirect(url_for("index"))
        ocorr = upperize_row_keys(data[0])
    except Exception as e:
        print("Erro ao buscar ocorrência para atendimento:", e)
        flash("Erro ao buscar ocorrência.", "danger")
        return redirect(url_for("index"))
    professores = carregar_lookup("Professores", column="Professor")
    salas = carregar_lookup("Salas", column="Sala")
    if request.method == "GET":
        return render_template("editar.html", ocorrencia=ocorr, professores_disp=professores,
                               salas_disp=salas, modo="ATENDIMENTO_INDIVIDUAL",
                               tipo_acao=tipo_acao, campo_atendimento=campo_atendimento)
    form = request.form
    atendimento_texto = form.get(campo_atendimento)
    if not atendimento_texto:
        flash("O campo de atendimento não pode estar vazio.", "danger")
        return redirect(url_for("atendimento", oid=oid, tipo_acao=tipo_acao))
    now_iso = datetime.now(TZ_SAO).date().isoformat()
    update = {campo_atendimento: atendimento_texto, campo_data: now_iso, tipo_acao: "NÃO"}
    ft = update.get("FT") if tipo_acao == "FT" else ocorr.get("FT", "NÃO")
    fc = update.get("FC") if tipo_acao == "FC" else ocorr.get("FC", "NÃO")
    fg = update.get("FG") if tipo_acao == "FG" else ocorr.get("FG", "NÃO")
    if ft == "NÃO" and fc == "NÃO" and fg == "NÃO":
        update["STATUS"] = "FINALIZADA"
    elif ocorr.get("STATUS") != "ASSINADA":
        update["STATUS"] = "ATENDIMENTO"
    try:
        supabase.table("ocorrencias").update(update).eq("ID", oid).execute()
        flash(f"Atendimento {tipo_acao} registrado e ocorrência atualizada.", "success")
    except Exception as e:
        print(f"Erro ao salvar atendimento {tipo_acao}:", e)
        flash("Erro ao salvar o atendimento.", "danger")
    return redirect(url_for("index"))

# --- Edição completa (após senha) ---
@app.route("/editar_completo/<int:oid>", methods=["GET", "POST"])
def editar_completo(oid):
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
        return redirect(url_for("index"))
    if request.method == "POST":
        senha = request.form.get("senha")
        if senha != "idrgestao":
            flash("Senha incorreta para edição completa.", "danger")
            return redirect(url_for("index"))
    try:
        resp = supabase.table("ocorrencias").select("*").eq("ID", oid).execute()
        data = resp.data or []
        if not data:
            flash("Ocorrência não encontrada.", "danger")
            return redirect(url_for("index"))
        ocorr = upperize_row_keys(data[0])
    except Exception as e:
        print("Erro ao buscar ocorrências:", e)
        flash("Erro ao buscar ocorrências.", "danger")
        return redirect(url_for("index"))
    professores = carregar_lookup("Professores", column="Professor")
    salas = carregar_lookup("Salas", column="Sala")
    if request.method == "GET":
        return render_template("editar.html", ocorrencia=ocorr, professores_disp=professores,
                               salas_disp=salas, modo="EDITAR")
    form = request.form
    update = {
        "PROFESSOR": form.get("PROFESSOR", ocorr.get("PROFESSOR", "")),
        "SALA": form.get("SALA", ocorr.get("SALA", "")),
        "ALUNO": form.get("ALUNO", ocorr.get("ALUNO", "")),
        "TUTOR": form.get("TUTOR", ocorr.get("TUTOR", "")),
        "FT": normalize_checkbox(form.get("FT")),
        "FC": normalize_checkbox(form.get("FC")),
        "FG": normalize_checkbox(form.get("FG")),
        "STATUS": form.get("STATUS", ocorr.get("STATUS", "ATENDIMENTO")),
        "ASSINADA": normalize_checkbox(form.get("ASSINADA"))
    }
    try:
        supabase.table("ocorrencias").update(update).eq("ID", oid).execute()
        flash("Ocorrência editada com sucesso. (Modo Completo)", "success")
    except Exception as e:
        print("Erro ao atualizar ocorrência:", e)
        flash("Erro ao atualizar ocorrência.", "danger")
    return redirect(url_for("index"))

# --- Criar nova ocorrência ---

Ótimo! Para resolver o problema de indentação no app (5).py e as questões de funcionalidade (salvar dados e filtros), vou fornecer os blocos de código completos e corrigidos para cada parte.

1. Correção de Sintaxe (Linha 383, app.py) e Lógica da Função nova
O erro IndentationError na linha 383 estava impedindo seu servidor de iniciar. A causa é o alinhamento incorreto do bloco try/except na função nova.

Abaixo está a função nova completa e corrigida para o seu app (5).py, que resolve a indentação e implementa sua regra de negócio:

Lê: FT, FC, FG (Requerimento - vindo do formulário).

Inicializa: ATT, ATC, ATG como vazios.

Calcula: PT, PC, PG (Pendência) com a sua regra ('S' se requisitado e não atendido).

Corrige a indentação do try/except no final.

Python

# app (5).py - Substituir a função nova (aproximadamente linha 220)

@app.route("/nova", methods=["GET", "POST"])
def nova():
    supabase = conectar_supabase()
    professores = carregar_lookup("Professores", column="Professor")
    salas = carregar_lookup("Salas", column="Sala")

    if request.method == "GET":
        return render_template("nova.html", professores_disp=professores, salas_disp=salas)

    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
        return redirect(url_for("index"))

    form = request.form

    # 1. Lê os campos de Requerimento de Atendimento (FT, FC, FG) do formulário
    # (Supondo que você corrigiu o nova.html para enviar FT, FC, FG)
    ft = normalize_checkbox(form.get("FT"))
    fc = normalize_checkbox(form.get("FC"))
    fg = normalize_checkbox(form.get("FG"))

    # 2. Inicializa os campos de Texto de Atendimento (ATT, ATC, ATG) como vazios na criação
    att = ""
    atc = ""
    atg = ""

    # 3. Calcula PT, PC, PG (Pendência de Atendimento)
    # Regra: PENDENTE ('S') se atendimento foi REQUISITADO (FT/FC/FG='SIM') E AINDA NÃO ATENDIDO (ATT/ATC/ATG='')
    pt = "S" if ft == "SIM" and att == "" else "N"
    pc = "S" if fc == "SIM" and atc == "" else "N"
    pg = "S" if fg == "SIM" and atg == "" else "N"

    # 4. Calcula STATUS: ATENDIMENTO se algum PT/PC/PG for 'S'
    if pt == "N" and pc == "N" and pg == "N":
        status = "FINALIZADA"
    else:
        status = "ATENDIMENTO"

    payload = {
        "DCO": datetime.now(TZ_SAO).date().isoformat(),
        "HCO": datetime.now(TZ_SAO).strftime("%H:%M"),
        "ALUNO": form.get("ALUNO", ""),
        "SALA": form.get("SALA", ""),
        "PROFESSOR": form.get("PROFESSOR", ""),
        "TUTOR": form.get("TUTOR", ""),
        "DESCRICAO": form.get("DESCRICAO", ""),
        "ATP": form.get("ATP", "") or "",
        "ATT": att,  # Vazio
        "ATC": atc,  # Vazio
        "ATG": atg,  # Vazio
        "FT": ft,    # Requerimento (SIM/NÃO)
        "FC": fc,    # Requerimento (SIM/NÃO)
        "FG": fg,    # Requerimento (SIM/NÃO)
        "PT": pt,    # Status Pendente (S/N)
        "PC": pc,    # Status Pendente (S/N)
        "PG": pg,    # Status Pendente (S/N)
        "DT": None,
        "DC": None,
        "DG": None,
        "STATUS": status,
        "ASSINADA": False
    }

    try:
        resp = supabase.table("ocorrencias").insert(payload).execute()
        if resp.error:
            # Recomendo adicionar a mensagem de erro do Supabase no print para debug
            print(f"Erro ao inserir ocorrência: {resp.error.message}")
            flash("Erro ao inserir ocorrências. Verifique os logs.", "danger")
        else:
            flash("Ocorrência registrada com sucesso.", "success")
    # Indentação corrigida
    except Exception as e:
        print("Erro ao gravar ocorrências:", e)
        flash("Erro ao gravar ocorrências.", "danger")

    return redirect(url_for("index"))
2.
# --- API: alunos por sala ---
@app.route("/api/alunos_por_sala/<sala>")
def api_alunos_por_sala(sala):
    supabase = conectar_supabase()
    if not supabase:
        return jsonify([])
    try:
        resp = supabase.table("Alunos").select("*").eq("Sala", sala).execute()
        return jsonify(resp.data or [])
    except Exception as e:
        print("Erro api/alunos_por_sala:", e)
        return jsonify([])

# -------------------------- Relatórios e PDF --------------------------
# Relatórios simplificados (mesma lógica que você já tinha)
# ... aqui você mantém todas as funções de relatório do seu código anterior
# garantir que nomes das tabelas sejam 'ocorrencias' e colunas consistentes

# -------------------------- Run --------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)

   












