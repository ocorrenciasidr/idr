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
# app.py (Bloco de Configuração no início do arquivo)

# -------------------------- Configuração --------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")

# ATENÇÃO: Corrigido o nome das variáveis de ambiente para o padrão
# Se você está usando um arquivo .env, as chaves devem ser 'SUPABASE_URL' e 'SUPABASE_KEY'.
# Se não estiver usando .env, substitua o segundo argumento (valor padrão) pelos seus valores reais.
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
def upperize_row_keys(row):
    """Converte todas as chaves do dicionário para MAIÚSCULAS."""
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
    pdf.cell(0, 7, 'Data:       /       /       ', 0, 1, 'L')
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

# app.py (Função carregar_dados_ocorrencias)

def carregar_dados_ocorrencias() -> list:
    supabase = conectar_supabase()
    if not supabase:
        print("❌ DEBUG: Falha na conexão com o Supabase.")
        return []
    try:
        # CORREÇÃO CRUCIAL: Usar 'ocorrencia' (singular) para consistência
        resp = supabase.table("ocorrencias").select("*").execute()
        data = resp.data or []
        
        # DEBUG: Imprime a quantidade de dados recebidos
        print(f"✅ DEBUG: {len(data)} registros de ocorrências carregados do Supabase.")
        
        # ensure uppercase keys for convenience
        normalized = [upperize_row_keys(r) for r in data]
        # sort by ID descending if exists
        # ... (o restante da função é o mesmo)
        
        # ... (restante da função)
        
        return normalized
    except Exception as e:
        print("❌ Erro ao carregar ocorrencias:", e)
        return []

# -------------------------- ROTAS --------------------------
@app.route("/")
@app.route("/home")
def home():
    ano = datetime.now(TZ_SAO).year
    return render_template("home.html", ano=ano)

@app.route("/index")
def index():
    registros = carregar_dados_ocorrencias()
    return render_template("index.html", registros=registros)

# --- Nova ocorrência ---
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
        # mantém a convenção de colunas MAIÚSCULAS em 'ocorrencia'
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

    # buscar ocorrência
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
        return render_template("editar.html", ocorrencias=ocorr, professores_disp=professores, salas_disp=salas)

    # POST: atualizar registro (sem senha)
    form = request.form
    update = {}

    # atualizar campos livres
    update["DESCRICAO"] = form.get("DESCRICAO", ocorr.get("DESCRICAO", ""))
    update["ATP"] = form.get("ATP", ocorr.get("ATP", ""))
    update["PROFESSOR"] = form.get("PROFESSOR", ocorr.get("PROFESSOR", ""))
    update["SALA"] = form.get("SALA", ocorr.get("SALA", ""))
    update["ALUNO"] = form.get("ALUNO", ocorr.get("ALUNO", ""))
    update["TUTOR"] = form.get("TUTOR", ocorr.get("TUTOR", ""))

    # Se as ações foram solicitadas (FT/FC/FG) e foram respondidas, gravar ATT/ATC/ATG e marcar DT/DC/DG
    now_iso = datetime.now(TZ_SAO).isoformat()

    # ATT (Tutor)
    if form.get("ATT") is not None:
        update["ATT"] = form.get("ATT", ocorr.get("ATT", ""))
    # ATC (Coordenação)
    if form.get("ATC") is not None:
        update["ATC"] = form.get("ATC", ocorr.get("ATC", ""))
    # ATG (Gestão)
    if form.get("ATG") is not None:
        update["ATG"] = form.get("ATG", ocorr.get("ATG", ""))

    # Se o checkbox FT/FC/FG ainda for SIM no banco e o form trouxe texto de atendimento,
    # consideramos que a solicitação foi atendida — atualizamos a flag para 'NÃO' e guardamos DT/DC/DG.
    # Caso queira outro comportamento, avise.
    # Nota: a UI enviará FT/FC/FG como checkbox quando estiver presente; aqui só ajustamos baseado no conteúdo.
    if ocorr.get("FT") == "SIM" and update.get("ATT"):
        update["FT"] = "NÃO"
        update["DT"] = now_iso
    if ocorr.get("FC") == "SIM" and update.get("ATC"):
        update["FC"] = "NÃO"
        update["DC"] = now_iso
    if ocorr.get("FG") == "SIM" and update.get("ATG"):
        update["FG"] = "NÃO"
        update["DG"] = now_iso

    # ajustar STATUS: se algum FT/FC/FG ainda for 'SIM' => ATENDIMENTO, senão FINALIZADA
    # buscamos os valores atuais (priorizar updates)
    ft = update.get("FT", ocorr.get("FT", "NÃO"))
    fc = update.get("FC", ocorr.get("FC", "NÃO"))
    fg = update.get("FG", ocorr.get("FG", "NÃO"))
    if "SIM" in (ft, fc, fg):
        update["STATUS"] = "ATENDIMENTO"
    else:
        update["STATUS"] = "FINALIZADA"

    try:
        supabase.table("ocorrencia").update(update).eq("ID", oid).execute()
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
    salas = sorted(set([r.get("Sala") for r in (supabase.table("Alunos").select("Sala").execute().data or []) if r.get("Sala")] ))
    alunos = sorted(set([r.get("Aluno") for r in (supabase.table("Alunos").select("Aluno").execute().data or []) if r.get("Aluno")] ))
    sala_sel = request.args.get("sala", "")
    aluno_sel = request.args.get("aluno", "")

    ocorrencias = []
    if aluno_sel:
        try:
            q = supabase.table("ocorrencia").select("*").eq("ALUNO", aluno_sel)
            if sala_sel:
                q = q.eq("SALA", sala_sel)
            resp = q.execute()
            ocorrencias = resp.data or []
            # normalize for display keys similar to templates earlier
            # transform keys to display-friendly names if needed in template
            # template expects fields like 'Nº Ocorrência', 'DCO', etc.
            normalized = []
            for o in ocorrencias:
                r = upperize_row_keys(o)
                r_display = {
                    "ID": r.get("ID"),
                    "Nº Ocorrência": r.get("ID"),
                    "DCO": r.get("DCO"),
                    "HCO": r.get("HCO"),
                    "Descrição da Ocorrência": r.get("DESCRICAO"),
                    "Status": r.get("STATUS", ""),
                }
                normalized.append(r_display)
            ocorrencias = normalized
        except Exception as e:
            print("Erro ao buscar ocorrencias por aluno:", e)
            ocorrencias = []

    return render_template("relatorio_aluno.html", salas=salas, alunos=alunos, sala_sel=sala_sel, aluno_sel=aluno_sel, ocorrencias=ocorrencias)

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
        resp = supabase.table("ocorrencia").select("*").in_("ID", ids).execute()
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
            supabase.table("ocorrencia").update({"STATUS": "ASSINADA", "ASSINADA": True}).eq("ID", o.get("ID")).execute()
        except Exception:
            pass

    out = BytesIO()
    pdf.output(out)
    out.seek(0)
    filename = f"Relatorio_{aluno or 'Aluno'}.pdf"
    return send_file(out, as_attachment=True, download_name=filename, mimetype="application/pdf")

@app.route("/relatorio_geral")
def relatorio_geral():
    # Para exibir: agregue os dados conforme templates
    supabase = conectar_supabase()
    data_inicio = request.args.get("data_inicio")
    data_fim = request.args.get("data_fim")
    # pega todas as ocorrencias e processa localmente
    resp = supabase.table("ocorrencia").select("*").execute()
    data = resp.data or []
    df = pd.DataFrame([upperize_row_keys(r) for r in data])
    # garante colunas
    if df.empty:
        rel_sala = []
        rel_setor = []
    else:
        df = ensure_cols_for_geral(df)
        # aplicar filtros de data se fornecidos (DCO em iso)
        if data_inicio:
            try:
                df = df[pd.to_datetime(df["DCO"], errors="coerce") >= pd.to_datetime(data_inicio)]
            except Exception:
                pass
        if data_fim:
            try:
                df = df[pd.to_datetime(df["DCO"], errors="coerce") <= pd.to_datetime(data_fim)]
            except Exception:
                pass
        rel_sala = calcular_relatorio_por_sala_df(df)
        rel_setor = calcular_relatorio_estatistico_df(df)
    return render_template("relatorio_geral.html", relatorio_sala=rel_sala, relatorio_setor=rel_setor, data_inicio=data_inicio, data_fim=data_fim)

@app.route("/relatorio_tutor")
def relatorio_tutor():
    supabase = conectar_supabase()
    start = request.args.get("start")
    end = request.args.get("end")
    # pega ocorrências FT == 'SIM' ou FT == 'SIM'/'NÃO' dependendo de sua lógica
    resp = supabase.table("ocorrencia").select("*").execute()
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
        qtd = len(supabase.table("ocorrencia").select("*").eq("ALUNO", a.get("Aluno")).execute().data or [])
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
