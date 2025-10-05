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
        # if column provided, return list of values; else list of dicts
        if column:
            return [r.get(column) for r in resp.data if r.get(column) is not None]
        return resp.data
    except Exception as e:
        print(f"Erro ao carregar {table_name}:", e)
        return []


# app.py

# ... (código anterior)

def carregar_dados_ocorrencias(filtro_tutor=None, filtro_status=None):
    """
    Carrega as ocorrências do Supabase aplicando filtros opcionais por tutor e status.
    Retorna sempre uma lista (mesmo que vazia).
    """
    try:
        supabase = conectar_supabase()
        if not supabase:
            print("❌ Erro: Supabase não conectado.")
            return []

        # --- Construção da query ---
        query = supabase.table("ocorrencias").select("*")

        # aplica filtro por tutor (se não for "Todos" e não vazio)
        if filtro_tutor and filtro_tutor != "Todos":
            query = query.eq("TUTOR", filtro_tutor)

        # aplica filtro por status (se não for "Todos" e não vazio)
        if filtro_status and filtro_status != "Todos":
            query = query.eq("STATUS", filtro_status)

        # executa consulta corretamente
        resp = query.execute()
        data = resp.data or []

        print(f"✅ DEBUG: {len(data)} registros carregados do Supabase.")

        # normaliza chaves para maiúsculas, se precisar em templates
        registros = [upperize_row_keys(r) for r in data]

        return registros

    except Exception as e:
        print("❌ Erro ao carregar dados de ocorrências:", e)
        return []

def carregar_tutores_com_ocorrencias() -> list:
    """Carrega uma lista de tutores que aparecem na tabela ocorrencias."""
    supabase = conectar_supabase()
    if not supabase: return []
    try:
        # Usa distinct (a coluna deve ser lowercase 'tutor' no banco)
        resp = supabase.table("ocorrencias").select("TUTOR", count='exact').not_("TUTOR", "is", None).order("TUTOR", desc=False).execute()
        # Retorna apenas os valores de TUTOR, removendo duplicados
        tutores = sorted(list(set([r['TUTOR'] for r in resp.data if r.get('TUTOR')])))
        return tutores
    except Exception as e:
        print("Erro ao carregar tutores:", e)
        return []
  
# ... (restante do código)
# -------------------------- ROTAS --------------------------
@app.route("/")
@app.route("/home")
def home():
    ano = datetime.now(TZ_SAO).year
    return render_template("home.html", ano=ano)

# app.py

# ... (código anterior)

@app.route("/index", methods=["GET"])
def index():
    try:
        supabase = conectar_supabase()
        if not supabase:
            flash("Erro de conexão com o banco.", "danger")
            return redirect(url_for("home"))  # ✅ não deixa a função acabar sem return

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

# --- Rota de Atendimento (FT, FC, FG) ---
@app.route("/atendimento/<int:oid>/<tipo_acao>", methods=["GET", "POST"])
def atendimento(oid, tipo_acao):
    # Tipos válidos de ação/setor: T=Tutor, C=Coordenação, G=Gestão
    if tipo_acao not in ["FT", "FC", "FG"]:
        flash("Ação inválida.", "danger")
        return redirect(url_for("index"))

    supabase = conectar_supabase()
    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
        return redirect(url_for("index"))

    # Mapeamentos
    # FT -> ATT, FC -> ATC, FG -> ATG (Campo de Texto de Resposta)
    campo_atendimento = "A" + tipo_acao[1] + "T"
    # FT -> DT, FC -> DC, FG -> DG (Campo de Data de Resposta)
    campo_data = tipo_acao[0] + "T"

    # 1. Busca a ocorrência
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
    
    # 2. Se GET (apenas visualiza a tela de edição, liberando SOMENTE o campo de atendimento)
    if request.method == "GET":
        # modo="ATENDIMENTO_INDIVIDUAL" indicará qual campo liberar no HTML
        return render_template("editar.html", 
                               ocorrencia=ocorr, 
                               professores_disp=professores, 
                               salas_disp=salas, 
                               modo="ATENDIMENTO_INDIVIDUAL", 
                               tipo_acao=tipo_acao,
                               campo_atendimento=campo_atendimento) # Passamos o campo liberado
                               
    # 3. Se POST (salva o atendimento)
    form = request.form
    atendimento_texto = form.get(campo_atendimento)
    
    if not atendimento_texto:
        flash("O campo de atendimento não pode estar vazio.", "danger")
        return redirect(url_for("atendimento", oid=oid, tipo_acao=tipo_acao))
        
    # a) Gera a data atual
    now_iso = datetime.now(TZ_SAO).date().isoformat()
    
    update = {}
    update[campo_atendimento] = atendimento_texto
    
    # REGRA DE INDIVIDUALIZAÇÃO E ATUALIZAÇÃO DO STATUS:
    if atendimento_texto.strip() != "":
        # Se preencheu o campo de atendimento,
        # 1. Atualiza o texto de atendimento.
        update[campo_atendimento] = atendimento_texto
        # 2. Grava a data de resposta.
        update[campo_data] = now_iso 
        # 3. Muda o flag de solicitação (FT, FC ou FG) para NÃO.
        update[tipo_acao] = "NÃO"    

    # Lógica de STATUS: Checa se todos os flags (FT, FC, FG) AGORA são 'NÃO'.
    # Usa o valor atual da ocorrência para os outros flags, e o valor do 'update' para o flag sendo alterado.
    
    ft = update.get("FT") if tipo_acao == "FT" else ocorr.get("FT", "NÃO")
    fc = update.get("FC") if tipo_acao == "FC" else ocorr.get("FC", "NÃO")
    fg = update.get("FG") if tipo_acao == "FG" else ocorr.get("FG", "NÃO")
    
    # Se TODOS forem 'NÃO', o status se torna 'FINALIZADA'
    if ft == "NÃO" and fc == "NÃO" and fg == "NÃO":
        update["STATUS"] = "FINALIZADA"
    elif ocorr.get("STATUS") != "ASSINADA":
        # Se ainda há flags SIM, o status se mantém em ATENDIMENTO (a menos que já esteja ASSINADA)
        update["STATUS"] = "ATENDIMENTO" 
        
    # d) Armazena os dados na tabela ocorrencias
    try:
        supabase.table("ocorrencias").update(update).eq("ID", oid).execute()
        flash(f"Atendimento {tipo_acao} registrado e ocorrência atualizada.", "success")
    except Exception as e:
        print(f"Erro ao salvar atendimento {tipo_acao}:", e)
        flash("Erro ao salvar o atendimento.", "danger")
        
    return redirect(url_for("index"))
# app.py

# ... (código anterior)

# Nova rota de edição completa (usada apenas após senha)
@app.route("/editar_completo/<int:oid>", methods=["GET", "POST"])
def editar_completo(oid):
    if request.method == "POST":
        senha = request.form.get("senha")
        # Senha hardcoded (idealmente, use autenticação de usuário/hash)
        if senha != "idrgestao":
            flash("Senha incorreta para edição completa.", "danger")
            return redirect(url_for("index"))
            
    # O restante da lógica de GET e POST do antigo /editar deve vir aqui
    # (Com exceção da parte de atendimento FT/FC/FG, que foi para a nova rota /atendimento)

    # Busca ocorrência (código copiado do antigo /editar)
    supabase = conectar_supabase()
    # ... (código de busca da ocorrência por ID)
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
        # Modo 'EDITAR' permite edição restrita, fundo branco
        return render_template("editar.html", ocorrencia=ocorr, professores_disp=professores, salas_disp=salas, modo="EDITAR")

    # POST: atualizar registro
    form = request.form
    update = {}
    
    # CAMPOS PERMITIDOS PARA EDIÇÃO COMPLETA:
    # Professor, Sala, Aluno, Tutor, FT, FC, FG, Status, Assinada
    update["PROFESSOR"] = form.get("PROFESSOR", ocorr.get("PROFESSOR", ""))
    update["SALA"] = form.get("SALA", ocorr.get("SALA", ""))
    update["ALUNO"] = form.get("ALUNO", ocorr.get("ALUNO", ""))
    update["TUTOR"] = form.get("TUTOR", ocorr.get("TUTOR", ""))
    update["FT"] = normalize_checkbox(form.get("FT"))
    update["FC"] = normalize_checkbox(form.get("FC"))
    update["FG"] = normalize_checkbox(form.get("FG"))
    update["STATUS"] = form.get("STATUS", ocorr.get("STATUS", "ATENDIMENTO"))
    update["ASSINADA"] = normalize_checkbox(form.get("ASSINADA")) # Assumindo que você tem um campo ASSINADA
    
    # Campos que NÃO PODEM SER EDITADOS (exceto se for primeira vez, o que não é o caso aqui):
    # DESCRICAO, ATP, ATT, ATC, ATG
    
    try:
        supabase.table("ocorrencias").update(update).eq("ID", oid).execute()
        flash("Ocorrência editada com sucesso. (Modo Completo)", "success")
    except Exception as e:
        print("Erro ao atualizar ocorrência:", e)
        flash("Erro ao atualizar ocorrência.", "danger")
        
    return redirect(url_for("index"))


# ... (restante do código)

@app.route("/nova", methods=["GET", "POST"])
def nova():
    supabase = conectar_supabase()
    # select options
    professores = carregar_lookup("Professores", column="Professor")
    salas = carregar_lookup("Salas", column="Sala")

    if request.method == "GET":
        return render_template("nova.html", professores_disp=professores, salas_disp=salas)

    # POST: salvar
    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
        return redirect(url_for("index"))

    form = request.form
    payload = {
        # mantém a convenção de colunas MAIÚSCULAS em 'ocorrencias'
        "DCO": datetime.now(TZ_SAO).date().isoformat(),
        "HCO": datetime.now(TZ_SAO).strftime("%H:%M"),
        "ALUNO": form.get("ALUNO", ""),
        "SALA": form.get("SALA", ""),
        "PROFESSOR": form.get("PROFESSOR", ""),
        "TUTOR": form.get("TUTOR", ""),
        "DESCRICAO": form.get("DESCRICAO", ""),
        "ATP": form.get("ATP", "") or "",
        "ATT": "", "ATC": "", "ATG": "",
        "FT": normalize_checkbox(form.get("FT")),
        "FC": normalize_checkbox(form.get("FC")),
        "FG": normalize_checkbox(form.get("FG")),
        "DT": None, "DC": None, "DG": None,
        "STATUS": "ATENDIMENTO" if ("SIM" in (normalize_checkbox(form.get("FT")), normalize_checkbox(form.get("FC")), normalize_checkbox(form.get("FG")))) else "FINALIZADA",
        "ASSINADA": False
    }

    try:
        # CORREÇÃO APLICADA: 'ocorrencias' (PLURAL)
        resp = supabase.table("ocorrencias").insert(payload).execute()
        if resp.error:
            flash("Erro ao inserir ocorrências.", "danger")
        else:
            flash("Ocorrência registrada com sucesso.", "success")
    except Exception as e:
        print("Erro ao inserir ocorrências:", e)
        flash("Erro ao gravar ocorrências.", "danger")

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

# --- Editar ocorrência ---
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

    # GET → exibir tela
    if request.method == "GET":
        return render_template("editar.html", ocorrencias=ocorr, professores_disp=professores, salas_disp=salas)

    # POST → atualizar
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

    if ocorr.get("FT") == "SIM" and update.get("ATT"):
        update["FT"] = "NÃO"; update["DT"] = now_iso
    if ocorr.get("FC") == "SIM" and update.get("ATC"):
        update["FC"] = "NÃO"; update["DC"] = now_iso
    if ocorr.get("FG") == "SIM" and update.get("ATG"):
        update["FG"] = "NÃO"; update["DG"] = now_iso

    ft, fc, fg = update.get("FT", ocorr.get("FT")), update.get("FC", ocorr.get("FC")), update.get("FG", ocorr.get("FG"))
    update["STATUS"] = "ATENDIMENTO" if "SIM" in (ft, fc, fg) else "FINALIZADA"

    try:
        supabase.table("ocorrencias").update(update).eq("ID", oid).execute()
        flash("Ocorrência atualizada com sucesso.", "success")
    except Exception as e:
        print("Erro ao atualizar ocorrência:", e)
        flash("Erro ao atualizar ocorrência.", "danger")

    return redirect(url_for("index"))

# --- Relatórios ---
@app.route("/relatorio_inicial")
def relatorio_inicial():
    ano = datetime.now(TZ_SAO).year
    return render_template("relatorio_inicial.html", ano=ano)

@app.route("/relatorio_aluno", methods=["GET", "POST"])
def relatorio_aluno():
    supabase = conectar_supabase()

    # Carregar salas e alunos
    salas = sorted({r.get("Sala") for r in (supabase.table("Alunos").select("Sala").execute().data or []) if r.get("Sala")})
    alunos = sorted({r.get("Aluno") for r in (supabase.table("Alunos").select("Aluno").execute().data or []) if r.get("Aluno")})

    sala_sel = request.args.get("sala", "")
    aluno_sel = request.args.get("aluno", "")
    ocorrencias = []

    if aluno_sel:
        try:
            q = supabase.table("ocorrencias").select("*").eq("ALUNO", aluno_sel)
            if sala_sel:
                q = q.eq("SALA", sala_sel)
            resp = q.execute()
            ocorrencias = [upperize_row_keys(o) for o in (resp.data or [])]
        except Exception as e:
            print("Erro ao buscar ocorrências do aluno:", e)

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
    supabase = conectar_supabase()
    selecionadas = request.form.getlist("ocorrencias[]")
    aluno = request.form.get("aluno", "")
    sala = request.form.get("sala", "")
    if not selecionadas:
        flash("Nenhuma ocorrência selecionada.", "warning")
        return redirect(url_for("relatorio_aluno", sala=sala, aluno=aluno))

    try:
        ids = [int(x) for x in selecionadas]
    except Exception:
        flash("IDs inválidos.", "danger")
        return redirect(url_for("relatorio_aluno"))

    try:
        # CORREÇÃO APLICADA: 'ocorrencias' (PLURAL)
        resp = supabase.table("ocorrencias").select("*").in_("ID", ids).execute()
        ocorrs = [upperize_row_keys(o) for o in (resp.data or [])]
    except Exception as e:
        print("Erro buscar ocorrencias para PDF:", e)
        flash("Erro ao preparar PDF.", "danger")
        return redirect(url_for("relatorio_aluno"))

    # gera PDF
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    for o in ocorrs:
        adicionar_ocorrencia_ao_pdf(pdf, o)
        try:
            # CORREÇÃO APLICADA: 'ocorrencias' (PLURAL)
            supabase.table("ocorrencias").update({"STATUS": "ASSINADA", "ASSINADA": True}).eq("ID", o.get("ID")).execute()
        except Exception:
            pass

    out = BytesIO()
    pdf.output(out)
    out.seek(0)
    filename = f"Relatorio_{aluno or 'Aluno'}.pdf"
    return send_file(out, as_attachment=True, download_name=filename, mimetype="application/pdf")

@app.route("/relatorio_geral", methods=["GET"])
def relatorio_geral():
    supabase = conectar_supabase()
    data_inicio = request.args.get("data_inicio")
    data_fim = request.args.get("data_fim")

    resp = supabase.table("ocorrencias").select("*").execute()
    data = resp.data or []
    df = pd.DataFrame([upperize_row_keys(r) for r in data])

    if df.empty:
        rel_sala = []; rel_setor = []
    else:
        df = ensure_cols_for_geral(df)
        if data_inicio:
            df = df[pd.to_datetime(df["DCO"], errors="coerce") >= pd.to_datetime(data_inicio)]
        if data_fim:
            df = df[pd.to_datetime(df["DCO"], errors="coerce") <= pd.to_datetime(data_fim)]
        rel_sala = calcular_relatorio_por_sala_df(df)
        rel_setor = calcular_relatorio_estatistico_df(df)

    return render_template(
        "relatorio_geral.html",
        relatorio_sala=rel_sala,
        relatorio_setor=rel_setor,
        data_inicio=data_inicio,
        data_fim=data_fim
    )


# --- API auxiliar (usada por JS ou relatórios dinâmicos) ---
@app.route("/api/ocorrencias")
def api_ocorrencias():
    try:
        data = supabase.table("ocorrencias").select("*").execute().data
        return jsonify(data)
    except Exception as e:
        print("Erro na API /api/ocorrencias:", e)
        return jsonify({"erro": str(e)}), 500


@app.route("/relatorio_tutor")
def relatorio_tutor():
    supabase = conectar_supabase()
    start = request.args.get("start")
    end = request.args.get("end")
    # pega ocorrências FT == 'SIM' ou FT == 'SIM'/'NÃO' dependendo de sua lógica
    # CORREÇÃO APLICADA: 'ocorrencias' (PLURAL)
    resp = supabase.table("ocorrencias").select("*").execute()
    data = resp.data or []
    df = pd.DataFrame([upperize_row_keys(r) for r in data])
    if df.empty:
        rel = {}
    else:
        # filtra por data se fornecido
        try:
            if start:
                df = df[pd.to_datetime(df["DCO"], errors="coerce") >= pd.to_datetime(start)]
            if end:
                df = df[pd.to_datetime(df["DCO"], errors="coerce") <= pd.to_datetime(end)]
        except Exception:
            pass
        # consideramos FT == 'SIM' como solicitado (ajuste se usar outro valor)
        df_ft = df[df["FT"] == "SIM"] if "FT" in df.columns else pd.DataFrame()
        rel = {}
        for idx, row in df_ft.iterrows():
            tutor = row.get("TUTOR", "SEM TUTOR")
            if tutor not in rel:
                rel[tutor] = {"total": 0, "prazo": 0, "fora": 0, "nao": 0}
            rel[tutor]["total"] += 1
            # calcular prazo usando DT (data de resposta tutor) e DCO
            dco = None
            try:
                dco = pd.to_datetime(row.get("DCO"))
            except Exception:
                dco = None
            dt = None
            try:
                dt = date_parser.parse(str(row.get("DT"))) if row.get("DT") else None
            except Exception:
                dt = None
            if dt is None:
                rel[tutor]["nao"] += 1
            else:
                dias = (dt.date() - dco.date()).days if dco is not None else 9999
                if dias <= PRAZO_DIAS:
                    rel[tutor]["prazo"] += 1
                else:
                    rel[tutor]["fora"] += 1
    return render_template("relatorio_tutor.html", relatorio=rel, start=start, end=end)

@app.route("/relatorio_tutoraluno")
def relatorio_tutoraluno():
    supabase = conectar_supabase()
    alunos = supabase.table("Alunos").select("*").execute().data or []
    dados = {}
    for a in alunos:
        tutor = a.get("Tutor", "SEM TUTOR")
        if tutor not in dados:
            dados[tutor] = []
        # CORREÇÃO APLICADA: 'ocorrencias' (PLURAL)
        qtd = len(supabase.table("ocorrencias").select("*").eq("ALUNO", a.get("Aluno")).execute().data or [])
        dados[tutor].append({"Aluno": a.get("Aluno"), "Sala": a.get("Sala"), "Quantidade Ocorrências": qtd})
    return render_template("relatorio_tutoraluno.html", dados=dados)

# -------------------------- Pequenas funções de relatório --------------------------
def ensure_cols_for_geral(df: pd.DataFrame) -> pd.DataFrame:
    needed = ["SALA", "DCO", "DT", "DC", "DG"]
    for c in needed:
        if c not in df.columns:
            df[c] = None
    return df

def calcular_relatorio_por_sala_df(df: pd.DataFrame) -> list:
    rel = []
    total = len(df)
    agrup = df.groupby("SALA") if "SALA" in df.columns else []
    for sala, grupo in agrup:
        cont = {"<7": 0, ">7": 0, "Não Respondidas": 0}
        for _, row in grupo.iterrows():
            datas = []
            for c in ["DT", "DC", "DG"]:
                val = row.get(c)
                if val and str(val) not in ("", "None"):
                    try:
                        datas.append(date_parser.parse(str(val)).date())
                    except Exception:
                        pass
            if datas:
                try:
                    dco = pd.to_datetime(row["DCO"]).date()
                    if (min(datas) - dco).days <= PRAZO_DIAS:
                        cont["<7"] += 1
                    else:
                        cont[">7"] += 1
                except Exception:
                    cont["Não Respondidas"] += 1
            else:
                cont["Não Respondidas"] += 1
        total_sala = len(grupo)
        rel.append({
            "Sala": sala,
            "Total Ocorrências": total_sala,
            "Porcentagem": f"{(total_sala/total*100):.1f}%" if total>0 else "0%",
            "Respondidas <7 dias": cont["<7"],
            "Respondidas >7 dias": cont[">7"],
            "Não Respondidas": cont["Não Respondidas"]
        })
    return rel

def calcular_relatorio_estatistico_df(df: pd.DataFrame) -> list:
    resumo = []
    setores = [("Tutor","DT"),("Coordenação","DC"),("Gestão","DG")]
    for setor, col in setores:
        cont = {"No Prazo":0, "Fora do Prazo":0, "Não Respondida":0}
        for _, row in df.iterrows():
            val = row.get(col)
            if not val or str(val) in ("", "None"):
                cont["Não Respondida"] += 1
            else:
                try:
                    dco = pd.to_datetime(row["DCO"]).date()
                    atend = date_parser.parse(str(val)).date()
                    dias = (atend - dco).days
                    if dias <= PRAZO_DIAS:
                        cont["No Prazo"] += 1
                    else:
                        cont["Fora do Prazo"] += 1
                except Exception:
                    cont["Não Respondida"] += 1
        total = len(df)
        resumo.append({
            "Setor": setor,
            "Total": total,
            "Respondidas <7 dias": cont["No Prazo"],
            "Respondidas >7 dias": cont["Fora do Prazo"],
            "Não Respondidas": cont["Não Respondida"]
        })
    return resumo

# -------------------------- Run --------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)









