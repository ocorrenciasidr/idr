import os
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from dateutil import parser as date_parser

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from datetime import datetime
from fpdf import FPDF

# Supabase
from supabase import create_client, Client

# -------------------------- CONFIGURAÇÃO --------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
PRAZO_DIAS = 7  # Prazo para classificar atendimento como no prazo
SETORES_ATENDIMENTO = ["Tutor", "Coordenação", "Gestão"]

TZ_SAO = timezone(timedelta(hours=-3))  # São Paulo UTC-3

def conectar_supabase() -> Client:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        return supabase
    except Exception as e:
        print(f"Erro ao conectar ao Supabase: {e}")
        return None

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

def _adicionar_ocorrencia_ao_pdf(pdf, ocorrencia):
    w_label, w_value = 45, 145
    pdf.set_font('Arial', 'B', 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.set_draw_color(0, 0, 0)

    def add_meta_row(label, value):
        value_display = str(value).split(' ')[0] if label == 'Data:' and value else str(value)
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(w_label, 7, label, 'LR', 0, 'L', 1)
        pdf.set_font('Arial', '', 10)
        pdf.cell(w_value, 7, value_display, 'LR', 1, 'L', 0)

    pdf.cell(w_label + w_value, 0, '', 'T', 1, 'L')
    add_meta_row('Aluno:', ocorrencia.get('Aluno', 'N/D'))
    add_meta_row('Tutor:', ocorrencia.get('Tutor', 'N/D'))
    add_meta_row('Data:', ocorrencia.get('DCO', 'N/D'))
    add_meta_row('Professor:', ocorrencia.get('PROFESSOR', 'N/D'))
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Sala:', 'LBR', 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value, 7, ocorrencia.get('Sala', 'N/D'), 'RBT', 1, 'L', 0)
    pdf.ln(2)

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Ocorrência nº:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value / 2, 7, str(ocorrencia.get('Nº Ocorrência', 'N/D')), 1, 0, 'L')
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label / 2, 7, 'Hora:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value / 2 - w_label / 2, 7, ocorrencia.get('HCO', 'N/D'), 1, 1, 'L')
    pdf.ln(5)

    def adicionar_bloco_texto(label, campo_db):
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 7, label, 1, 1, 'L', 1)
        pdf.set_font('Arial', '', 10)
        conteudo = ocorrencia.get(campo_db, '').strip()
        if not conteudo:
            conteudo = 'NÃO APLICÁVEL'
        pdf.multi_cell(0, 6, conteudo, 1, 'L', 0)
        pdf.ln(2)

    adicionar_bloco_texto('Descrição:', 'Descrição da Ocorrência')
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

# -------------------------- FUNÇÕES AUXILIARES --------------------------
def carregar_dados():
    supabase = conectar_supabase()
    if not supabase:
        return pd.DataFrame()
    try:
        response = supabase.table("ocorrencias").select("*").execute()
        df = pd.DataFrame(response.data)
        return df
    except:
        return pd.DataFrame()

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
            except:
                status[setor] = 'Não Respondida'
    return status

def calcular_relatorio_estatistico():
    df = carregar_dados()
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
    df = carregar_dados()
    if df.empty:
        return []
    relatorio = []
    total_geral = len(df)
    for sala, grupo in df.groupby('Sala'):
        contagem = {'<7': 0, '>7': 0, 'Não Respondidas': 0}
        for idx, row in grupo.iterrows():
            datas = [str(row.get(c)) for c in ['DT','DC','DG'] if str(row.get(c,'None')) not in ('','None')]
            if datas:
                dco = pd.to_datetime(row['DCO']).date()
                atendimentos = [date_parser.parse(d).date() for d in datas]
                if (min(atendimentos) - dco).days <= PRAZO_DIAS:
                    contagem['<7'] += 1
                else:
                    contagem['>7'] += 1
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

# -------------------------- ROTAS --------------------------
@app.route("/")
def home():
    return render_template("home.html", ano=datetime.now().year)

@app.route("/index")
def index():
    # Gestão de Ocorrências
    return render_template("index.html")

@app.route("/relatorios_inicial")
def relatorios_inicial():
    # Gestão de Relatórios
    return render_template("relatorios_inicial.html")

@app.route("/nova", methods=["GET", "POST"])
def nova():
    return render_template("nova.html")

@app.route('/relatorio_tutor_aluno')
def relatorio_tutoraluno():
    # Obtenha os dados dos alunos por tutor
    dados = obter_dados_tutor_aluno()  # função que retorna dicionário de tutores e alunos
    return render_template("relatorio_tutoraluno.html", dados=dados)

@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")

@app.route("/relatorio_aluno", methods=["GET", "POST"])
def relatorio_aluno():
    sala_sel = request.args.get("sala","")
    aluno_sel = request.args.get("aluno","")
    df = carregar_dados()
    if df.empty:
        return render_template("relatorio_aluno.html", registros=[], salas=[], alunos=[], sala_sel="", aluno_sel="")

    df["DCO"] = pd.to_datetime(df["DCO"], errors="coerce").dt.strftime("%d/%m/%Y") if "DCO" in df else df.get("DCO")
    df["HCO"] = pd.to_datetime(df["HCO"], errors="coerce").dt.strftime("%H:%M") if "HCO" in df else df.get("HCO")

    if sala_sel:
        df = df[df["SALA"]==sala_sel]
    if aluno_sel:
        df = df[df["ALUNO"]==aluno_sel]

    registros = df.to_dict(orient="records")
    salas = sorted(df["SALA"].dropna().unique().tolist()) if "SALA" in df else []
    alunos = sorted(df["ALUNO"].dropna().unique().tolist()) if "ALUNO" in df else []

    return render_template("relatorio_aluno.html", registros=registros, salas=salas, alunos=alunos, sala_sel=sala_sel, aluno_sel=aluno_sel)

@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    aluno = request.form.get("aluno")
    sala = request.form.get("sala")
    selecionadas = request.form.getlist("ocorrencias[]")
    if not selecionadas:
        flash("Nenhuma ocorrência selecionada.", "warning")
        return redirect(url_for("relatorio_aluno", sala=sala, aluno=aluno))

    selecionadas = [int(x) for x in selecionadas]
    supabase = conectar_supabase()
    response = supabase.table("ocorrencias").select("*").in_("ID", selecionadas).execute()
    ocorrencias = response.data

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()

    for row in ocorrencias:
        _adicionar_ocorrencia_ao_pdf(pdf, row)
        # Atualiza status para ASSINADA
        supabase.table("ocorrencias").update({"Status":"ASSINADA"}).eq("ID",row["ID"]).execute()

    pdf_output = BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)

    return send_file(pdf_output, as_attachment=True, download_name=f"Relatorio_{aluno}.pdf", mimetype="application/pdf")

@app.route("/editar/<int:oid>", methods=["GET","POST"])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro ao conectar ao banco de dados.", "danger")
        return redirect(url_for("index"))

    response = supabase.table('ocorrencias').select("*").eq("ID", oid).execute()
    if not response.data:
        flash(f"Ocorrência Nº {oid} não encontrada.", "danger")
        return redirect(url_for("index"))

    ocorrencia = response.data[0]

    if request.method=="POST":
        data = request.form
        update_data = {}
        now_local = datetime.now(TZ_SAO)

        if ocorrencia.get("FT")=="SIM":
            update_data["ATT"] = data.get("ATT", ocorrencia.get("ATT",""))
            update_data["FT"]="NÃO"
            update_data["DT"]=now_local.isoformat()
        if ocorrencia.get("FC")=="SIM":
            update_data["ATC"] = data.get("ATC", ocorrencia.get("ATC",""))
            update_data["FC"]="NÃO"
            update_data["DC"]=now_local.isoformat()
        if ocorrencia.get("FG")=="SIM":
            update_data["ATG"] = data.get("ATG", ocorrencia.get("ATG",""))
            update_data["FG"]="NÃO"
            update_data["DG"]=now_local.isoformat()

        update_data["DESCRICAO"] = data.get("DESCRICAO", ocorrencia.get("DESCRICAO",""))
        update_data["ATP"] = data.get("ATP", ocorrencia.get("ATP",""))

        if "SIM" in [update_data.get("FT","NÃO"), update_data.get("FC","NÃO"), update_data.get("FG","NÃO")]:
            update_data["STATUS"]="ATENDIMENTO"
        else:
            update_data["STATUS"]="FINALIZADA"

        try:
            supabase.table('ocorrencias').update(update_data).eq("ID",oid).execute()
            flash(f"Ocorrência Nº {oid} atualizada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao atualizar ocorrência: {e}", "danger")

        return redirect(url_for("index"))

    campos_editaveis = {
        "DESCRICAO": True,
        "ATP": True,
        "ATT": ocorrencia.get("FT","NÃO")=="SIM",
        "ATC": ocorrencia.get("FC","NÃO")=="SIM",
        "ATG": ocorrencia.get("FG","NÃO")=="SIM"
    }

    papel = request.args.get("papel","ver")
    modo = "view" if papel=="ver" else "edit"
    if modo=="view":
        for k in campos_editaveis:
            campos_editaveis[k]=False

    return render_template("editar.html", ocorrencia=ocorrencia, campos_editaveis=campos_editaveis, modo=modo)

@app.route("/relatorio_geral")
def relatorio_geral():
    resumo = calcular_relatorio_estatistico()
    salas = calcular_relatorio_por_sala()
    return render_template("relatorio_geral.html", resumo=resumo, salas=salas,
                           data_geracao=datetime.now(TZ_SAO).strftime("%d/%m/%Y %H:%M:%S"))

@app.route("/relatorio_tutor")
def relatorio_tutor():
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    df = carregar_dados()
    # Lógica simplificada de tutor
    relatorio = {}
    for tutor in df["TUTOR"].dropna().unique().tolist() if "TUTOR" in df else []:
        relatorio[tutor] = {'total':0,'prazo':0,'fora':0,'nao':0}
    return render_template("relatorio_tutor.html", relatorio=relatorio, start=start_date_str, end=end_date_str)

# -------------------------- RUN --------------------------
if __name__=="__main__":
    app.run(debug=True, port=int(os.environ.get("PORT",5000)))






