import os
import base64
from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
import gspread
from gspread.models import Cell
from pytz import timezone

# --- CONFIGURAÇÃO ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'idrgestao')
SHEET_ID = os.environ.get('SHEET_ID', '1Jyle_LCRCKQfbDShoIj-9MPNIkVSkYxWaCwQrhmxSoE')
TZ_SAO = timezone('America/Sao_Paulo')

# Constantes
ABA_OCORRENCIAS = 'Ocorrencias'
ABA_TUTORES = 'Tutores'
EXPECTED_COLUMNS = [
    'ID','DCO','HCO','Professor','Sala','Aluno','Tutor','Descrição da Ocorrência',
    'Atendimento Professor','ATT','ATC','ATG','FT','FC','FG','DT','DC','DG','Status'
]

HAS_MATPLOTLIB = True
try:
    import matplotlib.pyplot as plt
except ImportError:
    HAS_MATPLOTLIB = False

# --- UTILITÁRIOS ---
def conectar_sheets():
    try:
        gc = gspread.service_account(filename='credentials.json')
        sh = gc.open_by_key(SHEET_ID)
        return gc, sh
    except Exception as e:
        print(f"Erro ao conectar Sheets: {e}")
        return None, None

def carregar_dados():
    client, sh = conectar_sheets()
    if sh is None:
        return pd.DataFrame(columns=EXPECTED_COLUMNS)
    ws = sh.worksheet(ABA_OCORRENCIAS)
    data = ws.get_all_records()
    return pd.DataFrame(data)

def carregar_lista(aba, coluna):
    client, sh = conectar_sheets()
    if sh is None:
        return []
    ws = sh.worksheet(aba)
    return [r[coluna] for r in ws.get_all_records()]

def gerar_pdf_ocorrencias_aluno(aluno, sala, tutor, ocorrencias):
    # Placeholder: substitua pelo seu código de geração PDF
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph(f"Relatório do aluno: {aluno}", styles['Title']))
    for oc in ocorrencias:
        elements.append(Paragraph(str(oc), styles['Normal']))
        elements.append(Spacer(1,12))
    doc.build(elements)
    buffer.seek(0)
    return buffer

def gerar_pdf_ocorrencia(ocorrencia):
    buffer = BytesIO()
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph(f"Ocorrência ID {ocorrencia['ID']}", styles['Title'])]
    for k,v in ocorrencia.items():
        elements.append(Paragraph(f"{k}: {v}", styles['Normal']))
        elements.append(Spacer(1,6))
    doc.build(elements)
    buffer.seek(0)
    return buffer

def gerar_grafico_barras(relatorio_final, tutor):
    fig, ax = plt.subplots()
    labels = ['prazo','fora','nao','aberto']
    values = [relatorio_final.get(l,0) for l in labels]
    ax.bar(labels, values, color='skyblue')
    ax.set_title(f'Relatório do Tutor {tutor}')
    ax.set_ylabel('Quantidade')
    buffer = BytesIO()
    plt.savefig(buffer, format='png')
    plt.close(fig)
    buffer.seek(0)
    return buffer

def gerar_pdf_tutor(relatorio_final, tutor, registros, grafico_buffer=None):
    buffer = BytesIO()
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph(f"Relatório Tutor {tutor}", styles['Title'])]
    for k,v in relatorio_final.items():
        elements.append(Paragraph(f"{k}: {v}", styles['Normal']))
    elements.append(Spacer(1,12))
    if grafico_buffer:
        img = Image(grafico_buffer)
        img.drawHeight = 200
        img.drawWidth = 400
        elements.append(img)
    for rec in registros.to_dict('records'):
        elements.append(Spacer(1,6))
        elements.append(Paragraph(str(rec), styles['Normal']))
    doc.build(elements)
    buffer.seek(0)
    return buffer

# --- ROTAS ---

@app.route('/')
def index():
    df = carregar_dados()
    return render_template('index.html', registros=df.to_dict('records'))

@app.route('/nova', methods=['GET','POST'])
def nova():
    if request.method == 'POST':
        try:
            client, sh = conectar_sheets()
            ws = sh.worksheet(ABA_OCORRENCIAS)
            all_ids = ws.col_values(1)[1:]
            last_id = max([int(i) for i in all_ids if i.isdigit()] or [0])
            novo_id = last_id + 1

            form_data = request.form
            hoje = datetime.now(TZ_SAO).strftime('%Y-%m-%d')
            agora = datetime.now(TZ_SAO).strftime('%H:%M:%S')

            nova_ocorrencia = {
                'ID': novo_id,
                'DCO': hoje,
                'HCO': agora,
                'Professor': form_data.get('professor'),
                'Sala': form_data.get('sala'),
                'Aluno': form_data.get('aluno'),
                'Tutor': form_data.get('tutor'),
                'Descrição da Ocorrência': form_data.get('descricao'),
                'Atendimento Professor': form_data.get('at_professor',''),
                'ATT':'',
                'ATC':'',
                'ATG':'',
                'FT':'SIM' if form_data.get('req_ft')=='on' else 'NÃO',
                'FC':'SIM' if form_data.get('req_fc')=='on' else 'NÃO',
                'FG':'SIM' if form_data.get('req_fg')=='on' else 'NÃO',
                'DT':'',
                'DC':'',
                'DG':'',
                'Status':'Em Aberto'
            }

            row_to_insert = [nova_ocorrencia.get(col,'') for col in EXPECTED_COLUMNS]
            ws.append_row(row_to_insert)
            flash(f"Ocorrência ID {novo_id} salva com sucesso!",'success')
            return redirect(url_for('index'))

        except Exception as e:
            print(f"Erro ao salvar ocorrência: {e}")
            flash(f"Erro ao salvar: {e}",'danger')
            return redirect(url_for('nova'))

    tutores = carregar_lista(ABA_TUTORES,'Tutor')
    return render_template('nova.html', tutores=tutores)

# --- EDITAR ---
@app.route("/editar/<int:oid>", methods=['GET','POST'])
def editar(oid):
    df = carregar_dados()
    ocorrencia = df[df['ID']==oid].iloc[0].to_dict() if not df[df['ID']==oid].empty else None
    if ocorrencia is None:
        flash("Ocorrência não encontrada",'warning')
        return redirect(url_for('index'))

    permissoes = {
        "att": ocorrencia.get('FT')=='SIM' and not ocorrencia.get('ATT'),
        "atc": ocorrencia.get('FC')=='SIM' and not ocorrencia.get('ATC'),
        "atg": ocorrencia.get('FG')=='SIM' and not ocorrencia.get('ATG')
    }

    agora = datetime.now(TZ_SAO).strftime('%Y-%m-%d %H:%M:%S')

    if request.method=='POST':
        try:
            client, sh = conectar_sheets()
            ws = sh.worksheet(ABA_OCORRENCIAS)
            cell = ws.find(str(oid))
            if cell is None:
                flash("ID não encontrado na planilha",'danger')
                return redirect(url_for('editar',oid=oid))
            row_index = cell.row
            form_data = request.form
            updates = {}

            if permissoes['att']:
                att_texto = form_data.get('att_texto')
                if att_texto and att_texto.strip():
                    updates['ATT'] = att_texto
                    updates['DT'] = agora
                    updates['FT'] = 'NÃO'
            if permissoes['atc']:
                atc_texto = form_data.get('atc_texto')
                if atc_texto and atc_texto.strip():
                    updates['ATC'] = atc_texto
                    updates['DC'] = agora
                    updates['FC'] = 'NÃO'
            if permissoes['atg']:
                atg_texto = form_data.get('atg_texto')
                if atg_texto and atg_texto.strip():
                    updates['ATG'] = atg_texto
                    updates['DG'] = agora
                    updates['FG'] = 'NÃO'

            novo_status = form_data.get('novo_status')
            if novo_status and novo_status!=ocorrencia.get('Status'):
                updates['Status'] = novo_status

            nova_desc = form_data.get('descricao')
            if nova_desc is not None and nova_desc!=ocorrencia.get('Descrição da Ocorrência'):
                updates['Descrição da Ocorrência'] = nova_desc

            at_professor = form_data.get('at_professor')
            if at_professor is not None and at_professor!=ocorrencia.get('Atendimento Professor'):
                updates['Atendimento Professor'] = at_professor

            ft = updates.get('FT',ocorrencia.get('FT'))
            fc = updates.get('FC',ocorrencia.get('FC'))
            fg = updates.get('FG',ocorrencia.get('FG'))
            if ft=='SIM' or fc=='SIM' or fg=='SIM':
                updates['Status'] = 'ATENDIMENTO'
            elif ft=='NÃO' and fc=='NÃO' and fg=='NÃO':
                updates['Status'] = 'FINALIZADA'

            col_map = {col:i+1 for i,col in enumerate(EXPECTED_COLUMNS)}
            cells_to_update = [Cell(row_index,col_map[col],val) for col,val in updates.items() if col in col_map]
            if cells_to_update:
                ws.update_cells(cells_to_update)
                flash(f"Ocorrência ID {oid} atualizada com sucesso!",'success')
            else:
                flash("Nenhuma alteração detectada.",'info')

            return redirect(url_for('editar',oid=oid))

        except Exception as e:
            print(f"Erro ao editar ocorrência: {e}")
            flash(f"Erro ao editar: {e}",'danger')
            return redirect(url_for('editar',oid=oid))

    tutores = carregar_lista(ABA_TUTORES,'Tutor')
    status_list = ['Em Aberto','ATENDIMENTO','ASSINADA','FINALIZADA']
    return render_template('editar.html', ocorrencia=ocorrencia, permissoes=permissoes, tutores=tutores, status_list=status_list)

# --- DETALHES E PDF ---
@app.route("/detalhes/<int:oid>")
def detalhes(oid):
    df = carregar_dados()
    ocorrencia = df[df['ID']==oid].iloc[0].to_dict() if not df[df['ID']==oid].empty else None
    if ocorrencia is None:
        flash("Ocorrência não encontrada",'warning')
        return redirect(url_for('index'))
    tutores = carregar_lista(ABA_TUTORES,'Tutor')
    return render_template('detalhes.html', ocorrencia=ocorrencia, tutores=tutores)

@app.route("/detalhes/<int:oid>/pdf")
def pdf_ocorrencia(oid):
    df = carregar_dados()
    ocorrencia = df[df['ID']==oid].iloc[0].to_dict() if not df[df['ID']==oid].empty else None
    if ocorrencia is None:
        return "Ocorrência não encontrada",404
    pdf_output = gerar_pdf_ocorrencia(ocorrencia)
    return send_file(pdf_output, mimetype='application/pdf', as_attachment=True, download_name=f'SGCE_Ocorrencia_{oid}.pdf')

# --- PDF ALUNO ---
@app.route("/gerar_pdf_aluno", methods=['POST'])
def gerar_pdf_aluno():
    ids = request.form.getlist("ocorrencias")
    sala = request.form.get("sala")
    aluno = request.form.get("aluno")
    try:
        client, sh = conectar_sheets()
        ws = sh.worksheet(ABA_OCORRENCIAS)
        df = carregar_dados()
        for oid in ids:
            mask = df["ID"].astype(str)==str(oid)
            if mask.any():
                df.loc[mask,"Status"]="ASSINADA"
                cell = ws.find(str(oid))
                if cell:
                    col_status = EXPECTED_COLUMNS.index("Status")+1
                    ws.update_cell(cell.row,col_status,"ASSINADA")
        selecionadas = df[df["ID"].astype(str).isin(ids)].to_dict("records")
        tutor = selecionadas[0].get("Tutor","") if selecionadas else ""
        pdf_output = gerar_pdf_ocorrencias_aluno(aluno,sala,tutor,selecionadas)
        flash("PDF gerado e ocorrências atualizadas para ASSINADA!", "success")
        return send_file(pdf_output,mimetype="application/pdf",as_attachment=True,download_name=f"Relatorio_{aluno}.pdf")
    except Exception as e:
        print(f"Erro ao gerar PDF aluno: {e}")
        flash(f"Erro ao gerar PDF: {e}", "danger")
        return redirect(url_for("index"))

# --- RELATORIOS TUTOR ---
@app.route("/relatorio_inicial")
def relatorio_inicial():
    df = carregar_dados()
    tutores = sorted(list(df['Tutor'].unique())) if not df.empty and 'Tutor' in df.columns else carregar_lista(ABA_TUTORES,'Tutor')
    return render_template("relatorio_inicial.html", tutores=tutores)

# Rota PDF tutor e relatórios já seguem a mesma lógica do código anterior
# ...

if __name__=="__main__":
    app.run(debug=True)
