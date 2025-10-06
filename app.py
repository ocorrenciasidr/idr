# app.py
import os
from io import BytesIO
from datetime import datetime, timedelta, timezone

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_file
)
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

# Timezone São Paulo (UTC-3)
TZ_SAO = timezone(timedelta(hours=-3))

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
def upperize_row_keys(row: dict) -> dict:
    """Converte todas as chaves de um dicionário para maiúsculas."""
    return {k.upper(): v for k, v in row.items()}


def normalize_checkbox(val) -> str:
    """Return 'SIM' if checked/true-ish, else 'NÃO'."""
    if val is None:
        return "NÃO"
    v = str(val).strip().lower()
    if v in ("1", "true", "on", "sim", "yes"):
        return "SIM"
    return "NÃO"

# -------------------------- PDF helper (mantido) --------------------------
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

    bloco('Descrição:', 'DESCRICAO')
    bloco('Atendimento Professor (ATP):', 'ATP')
    bloco('Atendimento Tutor (ATT):', 'ATT')
    bloco('Atendimento Coordenação (ATC):', 'ATC')
    bloco('Atendimento Gestão (ATG):', 'ATG')
    pdf.ln(8)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(100, 7, 'Assinatura Responsável:', 0, 0, 'L')
    pdf.cell(0, 7, 'Data:        /        /        ', 0, 1, 'L')
    pdf.ln(5)
    pdf.set_font('Arial', 'I', 8)
    pdf.cell(0, 5, 'Ocorrência registrada no SGCE.', 0, 1, 'R')

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

# -------------------------- Função que carrega ocorrências (ajustada PT/PC/PG) --------------------------
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
        atualizados = 0

        for ocorr in normalized:
            update = {}

            # PT -> ATT
            if ocorr.get("PT") == "SIM" and (ocorr.get("ATT") or "").strip() != "":
                update["PT"] = "NÃO"
                update["DT"] = datetime.now(TZ_SAO).date().isoformat()

            # PC -> ATC
            if ocorr.get("PC") == "SIM" and (ocorr.get("ATC") or "").strip() != "":
                update["PC"] = "NÃO"
                update["DC"] = datetime.now(TZ_SAO).date().isoformat()

            # PG -> ATG
            if ocorr.get("PG") == "SIM" and (ocorr.get("ATG") or "").strip() != "":
                update["PG"] = "NÃO"
                update["DG"] = datetime.now(TZ_SAO).date().isoformat()

            # STATUS
            pt = update.get("PT", ocorr.get("PT", "NÃO"))
            pc = update.get("PC", ocorr.get("PC", "NÃO"))
            pg = update.get("PG", ocorr.get("PG", "NÃO"))

            if pt == "NÃO" and pc == "NÃO" and pg == "NÃO":
                if ocorr.get("STATUS") != "ASSINADA":
                    update["STATUS"] = "FINALIZADA"
            else:
                if ocorr.get("STATUS") != "ASSINADA":
                    update["STATUS"] = "ATENDIMENTO"

            if update:
                try:
                    supabase.table("ocorrencias").update(update).eq("ID", ocorr["ID"]).execute()
                    atualizados += 1
                except Exception as e:
                    print(f"⚠️ Erro ao atualizar ocorrência {ocorr['ID']}: {e}")

        if atualizados > 0:
            print(f"✅ {atualizados} ocorrências atualizadas automaticamente (PT/PC/PG e STATUS).")

        print(f"✅ DEBUG: {len(normalized)} registros de ocorrências carregados do Supabase.")
        return normalized

    except Exception as e:
        print("❌ Erro ao carregar ocorrencias:", e)
        return []

def carregar_tutores_com_ocorrencias() -> list:
    supabase = conectar_supabase()
    if not supabase: return []
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

@app.route("/index", methods=["GET"])
def index():
    try:
        supabase = conectar_supabase()
        if not supabase:
            flash("Erro de conexão com o banco.", "danger")
            return redirect(url_for("home"))

        filtro_tutor = request.args.get("tutor_filtro")
        filtro_status = request.args.get("status_filtro")

        registros = carregar_dados_ocorrencias(filtro_tutor, filtro_status)

        try:
            tutores_data = supabase.table("ocorrencias").select("TUTOR").execute().data or []
            tutores_disp = sorted(list({r.get("TUTOR") for r in tutores_data if r.get("TUTOR")}))
        except Exception as e:
            print("Erro ao carregar tutores:", e)
            tutores_disp = []

        status_disp = ["ATENDIMENTO", "FINALIZADA", "ASSINADA"]

        print(f"✅ DEBUG: {len(registros)} registros de ocorrências carregados do Supabase.")
        return render_template(
            "index.html",
            registros=registros,
            tutores_disp=["Todos"] + tutores_disp,
            status_disp=["Todos"] + status_disp,
            filtro_tutor_sel=filtro_tutor,
            filtro_status_sel=filtro_status
        )

    except Exception as e:
        print("❌ Erro na rota /index:", e)
        return "Erro interno na rota /index", 500

# --- Rota de Atendimento (PT, PC, PG) ---
@app.route("/atendimento/<int:oid>/<tipo_acao>", methods=["GET", "POST"])
def atendimento(oid, tipo_acao):
    # Tipos válidos de ação/setor: PT, PC, PG
    if tipo_acao not in ["PT", "PC", "PG"]:
        flash("Ação inválida.", "danger")
        return redirect(url_for("index"))

    supabase = conectar_supabase()
    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
        return redirect(url_for("index"))

    # Mapeamentos
    # PT -> ATT, PC -> ATC, PG -> ATG (Campo de Texto de Resposta)
    campo_atendimento = {"PT": "ATT", "PC": "ATC", "PG": "ATG"}[tipo_acao]
    # PT -> DT, PC -> DC, PG -> DG (Campo de Data de Resposta)
    campo_data = {"PT": "DT", "PC": "DC", "PG": "DG"}[tipo_acao]

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
        return render_template("editar.html", 
                               ocorrencia=ocorr, 
                               professores_disp=professores, 
                               salas_disp=salas, 
                               modo="ATENDIMENTO_INDIVIDUAL", 
                               tipo_acao=tipo_acao,
                               campo_atendimento=campo_atendimento)
                               
    # POST (salva)
    form = request.form
    atendimento_texto = form.get(campo_atendimento)
    
    if not atendimento_texto:
        flash("O campo de atendimento não pode estar vazio.", "danger")
        return redirect(url_for("atendimento", oid=oid, tipo_acao=tipo_acao))
        
    now_iso = datetime.now(TZ_SAO).date().isoformat()
    update = {}
    update[campo_atendimento] = atendimento_texto

    if atendimento_texto.strip() != "":
        update[campo_data] = now_iso 
        # marca a flag como "NÃO"
        update[tipo_acao] = "NÃO"

    # calcular valores atuais dos outros flags
    pt = update.get("PT") if tipo_acao == "PT" else ocorr.get("PT", "NÃO")
    pc = update.get("PC") if tipo_acao == "PC" else ocorr.get("PC", "NÃO")
    pg = update.get("PG") if tipo_acao == "PG" else ocorr.get("PG", "NÃO")

    if pt == "NÃO" and pc == "NÃO" and pg == "NÃO":
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

# Nova rota de edição completa (usada apenas após senha)
@app.route("/editar_completo/<int:oid>", methods=["GET", "POST"])
def editar_completo(oid):
    if request.method == "POST":
        senha = request.form.get("senha")
        if senha != "idrgestao":
            flash("Senha incorreta para edição completa.", "danger")
            return redirect(url_for("index"))
            
    supabase = conectar_supabase()
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
        return render_template("editar.html", ocorrencia=ocorr, professores_disp=professores, salas_disp=salas, modo="EDITAR")

    form = request.form
    update = {}

    update["PROFESSOR"] = form.get("PROFESSOR", ocorr.get("PROFESSOR", ""))
    update["SALA"] = form.get("SALA", ocorr.get("SALA", ""))
    update["ALUNO"] = form.get("ALUNO", ocorr.get("ALUNO", ""))
    update["TUTOR"] = form.get("TUTOR", ocorr.get("TUTOR", ""))
    update["PT"] = normalize_checkbox(form.get("PT"))
    update["PC"] = normalize_checkbox(form.get("PC"))
    update["PG"] = normalize_checkbox(form.get("PG"))
    update["STATUS"] = form.get("STATUS", ocorr.get("STATUS", "ATENDIMENTO"))
    update["ASSINADA"] = normalize_checkbox(form.get("ASSINADA"))

    try:
        supabase.table("ocorrencias").update(update).eq("ID", oid).execute()
        flash("Ocorrência editada com sucesso. (Modo Completo)", "success")
    except Exception as e:
        print("Erro ao atualizar ocorrência:", e)
        flash("Erro ao atualizar ocorrência.", "danger")
        
    return redirect(url_for("index"))

@app.route("/nova", methods=["GET", "POST"])
def nova():
    supabase = conectar_supabase()
    professores = carregar_lookup("Professores", column="Professor")
    salas = carregar_lookup("Salas", column="Sala")

    if request.method == "GET":
        return render_template("nova.html", professores_disp=professores, salas_disp=salas)

    # POST: salvar
    form = request.form
    # garantir PT/PC/PG presentes
    pt_val = form.get("PT") or ("SIM" if form.get("PT") == "SIM" else "NÃO")
    pc_val = form.get("PC") or ("SIM" if form.get("PC") == "SIM" else "NÃO")
    pg_val = form.get("PG") or ("SIM" if form.get("PG") == "SIM" else "NÃO")

    payload = {
        "DCO": datetime.now(TZ_SAO).date().isoformat(),
        "HCO": datetime.now(TZ_SAO).strftime("%H:%M"),
        "ALUNO": form.get("ALUNO", ""),
        "SALA": form.get("SALA", ""),
        "PROFESSOR": form.get("PROFESSOR", ""),
        "TUTOR": form.get("TUTOR", ""),
        "DESCRICAO": form.get("DESCRICAO", ""),
        "ATP": form.get("ATP", "") or "",
        "ATT": "", "ATC": "", "ATG": "",
        "PT": normalize_checkbox(form.get("PT")),
        "PC": normalize_checkbox(form.get("PC")),
        "PG": normalize_checkbox(form.get("PG")),
        "DT": None, "DC": None, "DG": None,
        "STATUS": "ATENDIMENTO" if ("SIM" in (normalize_checkbox(form.get("PT")), normalize_checkbox(form.get("PC")), normalize_checkbox(form.get("PG")))) else "FINALIZADA",
        "ASSINADA": False
    }

    try:
        supabase.table("ocorrencias").insert(payload).execute()
    except Exception as e:
        print("Erro ao inserir nova ocorrência:", e)
        flash("Erro ao registrar ocorrência.", "danger")
        return redirect(url_for("nova"))

    return redirect(url_for("index"))

# --- API alunos por sala ---
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

# --- Editar ocorrência (modo padrão) ---
@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
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
        flash("Erro ao buscar ocorrência.", "danger")
        return redirect(url_for("index"))

    professores = carregar_lookup("Professores", column="Professor")
    salas = carregar_lookup("Salas", column="Sala")

    if request.method == "GET":
        return render_template("editar.html", ocorrencias=ocorr, professores_disp=professores, salas_disp=salas)

    form = request.form
    update = {}
    update["DESCRICAO"] = form.get("DESCRICAO", ocorr.get("DESCRICAO", ""))
    update["ATP"] = form.get("ATP", ocorr.get("ATP", ""))
    update["PROFESSOR"] = form.get("PROFESSOR", ocorr.get("PROFESSOR", ""))
    update["SALA"] = form.get("SALA", ocorr.get("SALA", ""))
    update["ALUNO"] = form.get("ALUNO", ocorr.get("ALUNO", ""))
    update["TUTOR"] = form.get("TUTOR", ocorr.get("TUTOR", ""))

    now_iso = datetime.now(TZ_SAO).isoformat()

    if form.get("ATT"): update["ATT"] = form.get("ATT")
    if form.get("ATC"): update["ATC"] = form.get("ATC")
    if form.get("ATG"): update["ATG"] = form.get("ATG")

    if ocorr.get("PT") == "SIM" and update.get("ATT"):
        update["PT"] = "NÃO"; update["DT"] = now_iso
    if ocorr.get("PC") == "SIM" and update.get("ATC"):
        update["PC"] = "NÃO"; update["DC"] = now_iso
    if ocorr.get("PG") == "SIM" and update.get("ATG"):
        update["PG"] = "NÃO"; update["DG"] = now_iso

    ft_val, fc_val, fg_val = update.get("PT", ocorr.get("PT")), update.get("PC", ocorr.get("PC")), update.get("PG", ocorr.get("PG"))
    update["STATUS"] = "ATENDIMENTO" if "SIM" in (ft_val, fc_val, fg_val) else "FINALIZADA"

    try:
        supabase.table("ocorrencias").update(update).eq("ID", oid).execute()
        flash("Ocorrência atualizada com sucesso.", "success")
    except Exception as e:
        print("Erro ao atualizar ocorrência:", e)
        flash("Erro ao atualizar ocorrência.", "danger")

    return redirect(url_for("index"))

# --- Relatórios e APIs mantidos (sem alteração significativa nas partes não relacionadas) ---
# ... (restante do seu código permanece inalterado; não incluí aqui para manter o foco nas alterações)
# Se preferir, posso enviar o arquivo inteiro com absolutamente tudo (copiando seu original e aplicando textual substitutions).
# -------------------------- Run --------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
