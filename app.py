import os
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
from dateutil import parser as date_parser
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from fpdf import FPDF
from supabase import create_client, Client

# -------------------------- CONFIGURAÇÃO --------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
PRAZO_DIAS = int(os.environ.get("PRAZO_DIAS", 7))  # Prazo para classificar atendimento
SETORES_ATENDIMENTO = ["Tutor", "Coordenação", "Gestão"]

# Timezone São Paulo (UTC-3)
TZ_SAO = timezone(timedelta(hours=-3))

# Cria cliente supabase (pode ser None se variáveis não estiverem configuradas)
def conectar_supabase() -> Client | None:
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ SUPABASE_URL ou SUPABASE_KEY não configurados.")
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print("❌ Erro ao conectar ao Supabase:", e)
        return None

# -------------------------- UTILITÁRIOS --------------------------
def upperize_row_keys(row: dict) -> dict:
    """Retorna novo dict com chaves em MAIÚSCULAS (não modifica valores)."""
    return {str(k).upper(): v for k, v in (row or {}).items()}

def ensure_columns_df(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    """Garante colunas presentes no DataFrame (preenche com None quando ausentes)."""
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df

# -------------------------- PDF --------------------------
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

def adicionar_ocorrencia_ao_pdf(pdf: PDF, ocorrencia: dict):
    """Adiciona um bloco com os dados de uma ocorrência ao PDF."""
    w_label, w_value = 45, 145
    pdf.set_font('Arial', 'B', 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.set_draw_color(0, 0, 0)

    def add_meta_row(label, value):
        value_display = str(value).split(' ')[0] if (label == 'Data:' and value) else (str(value) if value is not None else '')
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(w_label, 7, label, 'LR', 0, 'L', 1)
        pdf.set_font('Arial', '', 10)
        pdf.cell(w_value, 7, value_display, 'LR', 1, 'L', 0)

    pdf.cell(w_label + w_value, 0, '', 'T', 1, 'L')
    add_meta_row('Aluno:', ocorrencia.get('ALUNO', 'N/D'))
    add_meta_row('Tutor:', ocorrencia.get('TUTOR', 'N/D'))
    add_meta_row('Data:', ocorrencia.get('DCO', 'N/D'))
    add_meta_row('Professor:', ocorrencia.get('PROFESSOR', 'N/D'))
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Sala:', 'LBR', 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value, 7, ocorrencia.get('SALA', 'N/D'), 'RBT', 1, 'L', 0)
    pdf.ln(2)

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Ocorrência nº:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value / 2, 7, str(ocorrencia.get('ID', 'N/D')), 1, 0, 'L')
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label / 2, 7, 'Hora:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value / 2 - w_label / 2, 7, ocorrencia.get('HCO', 'N/D'), 1, 1, 'L')
    pdf.ln(5)

    def adicionar_bloco_texto(label, campo_db):
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 7, label, 1, 1, 'L', 1)
        pdf.set_font('Arial', '', 10)
        conteudo = ocorrencia.get(campo_db, '') or ''
        conteudo = str(conteudo).strip()
        if not conteudo:
            conteudo = 'NÃO APLICÁVEL'
        pdf.multi_cell(0, 6, conteudo, 1, 'L', 0)
        pdf.ln(2)

    adicionar_bloco_texto('Descrição:', 'DESCRICAO')
    adicionar_bloco_texto('Atendimento Professor:', 'ATP')
    adicionar_bloco_texto('Atendimento Tutor (Se solicitado):', 'ATT')
    adicionar_bloco_texto('Atendimento Coordenação (Se solicitado):', 'ATC')
    adicionar_bloco_texto('Atendimento Gestão (Se solicitado):', 'ATG')
    pdf.ln(10)

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(100, 7, 'Assinatura Responsável:', 0, 0, 'L')
    pdf.cell(0, 7, 'Data:       /       /       ', 0, 1, 'L')
    pdf.ln(5)
    pdf.set_font('Arial', '', 8)
    pdf.cell(0, 1, '-' * 125, 0, 1, 'L')
    pdf.set_font('Arial', 'I', 8)
    pdf.cell(0, 5, 'Ocorrência registrada no SGCE.', 0, 1, 'R')

# -------------------------- CARREGAMENTO / CONSULTAS --------------------------
def carregar_dados_ocorrencias() -> pd.DataFrame:
    """Retorna DataFrame padronizado (colunas MAIÚSCULAS) com ocorrências."""
    supabase = conectar_supabase()
    if not supabase:
        return pd.DataFrame()

    try:
        resp = supabase.table("ocorrencias").select("*").execute()
        if not resp.data:
            return pd.DataFrame()
        df = pd.DataFrame(resp.data)
        # padroniza colunas
        df.columns = [c.upper() for c in df.columns]
        # garante colunas essenciais
        df = ensure_columns_df(df, ["ID","DCO","HCO","ALUNO","SALA","PROFESSOR","TUTOR","STATUS","FT","FC","FG","DESCRICAO"])
        return df
    except Exception as e:
        print("❌ Erro ao carregar ocorrencias:", e)
        return pd.DataFrame()

def carregar_lookup(table_name: str) -> list:
    """Busca lista simples de registros de tabelas de suporte (alunos, professores, salas)."""
    supabase = conectar_supabase()
    if not supabase:
        return []
    try:
        resp = supabase.table(table_name).select("*").order("id", desc=False).execute()
        if not resp.data:
            return []
        # normaliza chaves para MAIÚSCULAS e retorna lista de dicts
        return [upperize_row_keys(r) for r in resp.data]
    except Exception as e:
        print(f"❌ Erro ao carregar {table_name}:", e)
        return []

# -------------------------- ROTAS --------------------------
@app.route("/")
@app.route("/index")
def index():
    df = carregar_dados_ocorrencias()
    if df.empty:
        registros = []
    else:
        # opcional: formatar data/hora para exibição
        if "DCO" in df.columns:
            try:
                df["DCO"] = pd.to_datetime(df["DCO"], errors="coerce").dt.strftime("%d/%m/%Y")
            except Exception:
                pass
        if "HCO" in df.columns:
            try:
                df["HCO"] = pd.to_datetime(df["HCO"], errors="coerce").dt.strftime("%H:%M")
            except Exception:
                pass

        # ordena por ID decrescente quando possível
        if "ID" in df.columns:
            try:
                df = df.sort_values(by="ID", ascending=False)
            except Exception:
                pass

        registros = df.to_dict(orient="records")

    return render_template("index.html", registros=registros)

@app.route("/nova", methods=["GET", "POST"])
def nova():
    supabase = conectar_supabase()
    if request.method == "GET":
        # busca dados para os selects do formulário
        alunos = carregar_lookup("alunos")
        professores = carregar_lookup("professores")
        salas = carregar_lookup("salas")
        return render_template("nova.html", alunos=alunos, professores=professores, salas=salas)

    # POST: grava nova ocorrência
    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
        return redirect(url_for("index"))

    data = request.form.to_dict()
    # Normaliza keys de entrada para match com a sua tabela
    payload = {
        "ALUNO": data.get("aluno") or data.get("ALUNO"),
        "SALA": data.get("sala") or data.get("SALA"),
        "PROFESSOR": data.get("professor") or data.get("PROFESSOR"),
        "TUTOR": data.get("tutor") or data.get("TUTOR"),
        "DESCRICAO": data.get("descricao") or data.get("DESCRICAO"),
        "FT": data.get("ft", "NÃO"),
        "FC": data.get("fc", "NÃO"),
        "FG": data.get("fg", "NÃO"),
        "STATUS": data.get("status", "ABERTA"),
        # registra data/hora local em ISO
        "DCO": datetime.now(TZ_SAO).isoformat(),
        "HCO": datetime.now(TZ_SAO).strftime("%H:%M")
    }

    try:
        resp = supabase.table("ocorrencias").insert(payload).execute()
        if resp.error:
            flash(f"Erro ao inserir ocorrência: {resp.error}", "danger")
        else:
            flash("Ocorrência registrada com sucesso.", "success")
    except Exception as e:
        print("❌ Erro ao inserir ocorrência:", e)
        flash("Erro ao gravar ocorrência.", "danger")

    return redirect(url_for("index"))

@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro ao conectar ao banco de dados.", "danger")
        return redirect(url_for("index"))

    try:
        resp = supabase.table("ocorrencias").select("*").eq("ID", oid).execute()
        if not resp.data:
            # Tenta por id minúsculo (às vezes a PK é 'id')
            resp = supabase.table("ocorrencias").select("*").eq("id", oid).execute()
    except Exception as e:
        print("❌ Erro ao buscar ocorrência:", e)
        resp = None

    if not resp or not resp.data:
        flash(f"Ocorrência Nº {oid} não encontrada.", "danger")
        return redirect(url_for("index"))

    # normaliza chaves para MAIÚSCULAS
    ocorrencia = upperize_row_keys(resp.data[0])

    if request.method == "POST":
        form = request.form.to_dict()
        update_data = {}
        now_local = datetime.now(TZ_SAO)

        # lógica de atualização similar à que você tinha
        # se FT/FC/FG estavam marcados como "SIM" no registro original, preenche atendimento e marca como NÃO
        if ocorrencia.get("FT") == "SIM":
            update_data["ATT"] = form.get("ATT", ocorrencia.get("ATT", ""))
            update_data["FT"] = "NÃO"
            update_data["DT"] = now_local.isoformat()
        if ocorrencia.get("FC") == "SIM":
            update_data["ATC"] = form.get("ATC", ocorrencia.get("ATC", ""))
            update_data["FC"] = "NÃO"
            update_data["DC"] = now_local.isoformat()
        if ocorrencia.get("FG") == "SIM":
            update_data["ATG"] = form.get("ATG", ocorrencia.get("ATG", ""))
            update_data["FG"] = "NÃO"
            update_data["DG"] = now_local.isoformat()

        # campos editáveis
        update_data["DESCRICAO"] = form.get("DESCRICAO", ocorrencia.get("DESCRICAO", ""))
        update_data["ATP"] = form.get("ATP", ocorrencia.get("ATP", ""))

        # ajusta STATUS
        if "SIM" in [update_data.get("FT", "NÃO"), update_data.get("FC", "NÃO"), update_data.get("FG", "NÃO")]:
            update_data["STATUS"] = "ATENDIMENTO"
        else:
            update_data["STATUS"] = "FINALIZADA"

        try:
            supabase.table("ocorrencias").update(update_data).eq("ID", oid).execute()
            flash(f"Ocorrência Nº {oid} atualizada com sucesso!", "success")
        except Exception as e:
            print("❌ Erro ao atualizar ocorrência:", e)
            flash("Erro ao atualizar ocorrência.", "danger")

        return redirect(url_for("index"))

    # GET: mostra a página de edição
    campos_editaveis = {
        "DESCRICAO": True,
        "ATP": True,
        "ATT": ocorrencia.get("FT", "NÃO") == "SIM",
        "ATC": ocorrencia.get("FC", "NÃO") == "SIM",
        "ATG": ocorrencia.get("FG", "NÃO") == "SIM"
    }
    papel = request.args.get("papel", "ver")
    modo = "view" if papel == "ver" else "edit"
    if modo == "view":
        for k in campos_editaveis:
            campos_editaveis[k] = False

    return render_template("editar.html", ocorrencia=ocorrencia, campos_editaveis=campos_editaveis, modo=modo)

@app.route("/relatorio_aluno", methods=["GET","POST"])
def relatorio_aluno():
    """
    Se GET: mostra a página de seleção (salas, alunos).
    Se POST: recebe lista de IDs (ocorrencias[]) e gera PDF, baixando-o.
    """
    supabase = conectar_supabase()
    if request.method == "GET":
        # opções para filtros
        df = carregar_dados_ocorrencias()
        salas = sorted(df["SALA"].dropna().unique().tolist()) if not df.empty and "SALA" in df.columns else []
        alunos = sorted(df["ALUNO"].dropna().unique().tolist()) if not df.empty and "ALUNO" in df.columns else []
        return render_template("relatorio_aluno.html", registros=[], salas=salas, alunos=alunos, sala_sel="", aluno_sel="")

    # POST -> gerar PDF
    selecionadas = request.form.getlist("ocorrencias[]")
    aluno = request.form.get("aluno", "")
    sala = request.form.get("sala", "")
    if not selecionadas:
        flash("Nenhuma ocorrência selecionada.", "warning")
        return redirect(url_for("relatorio_aluno", sala=sala, aluno=aluno))

    try:
        ids = [int(x) for x in selecionadas]
    except Exception:
        flash("IDs de ocorrências inválidos.", "danger")
        return redirect(url_for("relatorio_aluno"))

    if not supabase:
        flash("Erro de conexão com o banco.", "danger")
        return redirect(url_for("relatorio_aluno"))

    resp = supabase.table("ocorrencias").select("*").in_("ID", ids).execute()
    ocorrencias = [upperize_row_keys(r) for r in (resp.data or [])]

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    for o in ocorrencias:
        adicionar_ocorrencia_ao_pdf(pdf, o)
        # tenta marcar ASSINADA (silencioso em caso de erro)
        try:
            supabase.table("ocorrencias").update({"STATUS": "ASSINADA"}).eq("ID", o.get("ID")).execute()
        except Exception:
            pass

    out = BytesIO()
    pdf.output(out)
    out.seek(0)
    filename = f"Relatorio_{aluno or 'Aluno'}.pdf"
    return send_file(out, as_attachment=True, download_name=filename, mimetype="application/pdf")

@app.route("/validar_senha", methods=["POST"])
def validar_senha():
    """
    Endpoint simples para modal de senha — ajuste conforme sua lógica real.
    Retorna redirect para editar ou realiza ação.
    """
    oid = request.form.get("oid")
    acao = request.form.get("acao")
    senha = request.form.get("senha")
    # Aqui: você pode validar a senha real (ex: comparar com variavel de ambiente)
    senha_correta = os.environ.get("ADMIN_SENHA", "1234")
    if senha == senha_correta:
        # redireciona para editar (ou outra ação)
        try:
            oid_int = int(oid)
            return redirect(url_for("editar", oid=oid_int))
        except Exception:
            return redirect(url_for("index"))
    else:
        flash("Senha incorreta.", "danger")
        return redirect(url_for("index"))

# -------------------------- RELATÓRIOS ESTATÍSTICOS (EXTRAS) --------------------------
def calcular_status_prazo(row):
    status = {}
    for setor, col in zip(SETORES_ATENDIMENTO, ['DT', 'DC', 'DG']):
        valor = row.get(col)
        if not valor or str(valor) in ('', 'None'):
            status[setor] = 'Não Respondida'
        else:
            try:
                dco = pd.to_datetime(row['DCO']).date()
                atendimento = date_parser.parse(valor).date()
                dias = (atendimento - dco).days
                status[setor] = 'No Prazo' if dias <= PRAZO_DIAS else 'Fora do Prazo'
            except Exception:
                status[setor] = 'Não Respondida'
    return status

def calcular_relatorio_estatistico():
    df = carregar_dados_ocorrencias()
    if df.empty:
        return []
    df['PRAZO_STATUS'] = df.apply(calcular_status_prazo, axis=1)
    resumo = []
    for setor in SETORES_ATENDIMENTO:
        contagem = {'No Prazo': 0, 'Fora do Prazo': 0, 'Não Respondida': 0}
        for st in df['PRAZO_STATUS']:
            contagem[st[setor]] += 1
        total = len(df)
        resumo.append({
            'Setor': setor,
            'Total': total,
            'Respondidas <7 dias': contagem['No Prazo'],
            'Respondidas >7 dias': contagem['Fora do Prazo'],
            'Não Respondidas': contagem['Não Respondida']
        })
    return resumo

def calcular_relatorio_por_sala():
    df = carregar_dados_ocorrencias()
    if df.empty:
        return []
    relatorio = []
    total_geral = len(df)
    # agrupa por sala (atenção: coluna 'SALA' em MAIÚSCULAS)
    agrup = df.groupby('SALA') if 'SALA' in df.columns else []
    for sala, grupo in agrup:
        contagem = {'<7': 0, '>7': 0, 'Não Respondidas': 0}
        for idx, row in grupo.iterrows():
            datas = [str(row.get(c)) for c in ['DT','DC','DG'] if str(row.get(c,'None')) not in ('','None')]
            if datas:
                try:
                    dco = pd.to_datetime(row['DCO']).date()
                    atendimentos = [date_parser.parse(d).date() for d in datas]
                    if (min(atendimentos) - dco).days <= PRAZO_DIAS:
                        contagem['<7'] += 1
                    else:
                        contagem['>7'] += 1
                except Exception:
                    contagem['Não Respondidas'] += 1
            else:
                contagem['Não Respondidas'] += 1
        total_sala = len(grupo)
        relatorio.append({
            'Sala': sala,
            'Total Ocorrências': total_sala,
            'Porcentagem': f"{(total_sala/total_geral*100):.1f}%" if total_geral>0 else '0%',
            'Respondidas <7 dias': contagem['<7'],
            'Respondidas >7 dias': contagem['>7'],
            'Não Respondidas': contagem['Não Respondidas']
        })
    return relatorio

# -------------------------- RUN --------------------------
if __name__ == "__main__":
    # Para desenvolvimento local
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, port=port, host="0.0.0.0")
