import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
import base64

from flask import Flask, render_template, request, redirect, url_for, flash, send_file
import pandas as pd
import gspread
from gspread.utils import rowcol_to_a1

# --- Configurações ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fallback_secret_key')

SHEET_ID = os.environ.get('SHEET_ID', 'sua_sheet_id_aqui')
ABA_OCORRENCIAS = "Ocorrencias"
ABA_TUTORES = "Tutores"
EXPECTED_COLUMNS = [
    'ID', 'DCO', 'HCO', 'Professor', 'Sala', 'Aluno', 'Tutor',
    'Descrição da Ocorrência', 'Atendimento Professor',
    'ATT', 'ATC', 'ATG',
    'FT', 'FC', 'FG',
    'DT', 'DC', 'DG',
    'Status'
]

TZ_SAO = timezone(timedelta(hours=-3))  # São Paulo UTC-3

HAS_MATPLOTLIB = True
try:
    import matplotlib.pyplot as plt
except ImportError:
    HAS_MATPLOTLIB = False

# --- Conexão com Google Sheets ---
def conectar_sheets():
    try:
        gc = gspread.service_account(filename='credentials.json')
        sh = gc.open_by_key(SHEET_ID)
        return gc, sh
    except Exception as e:
        print(f"Erro de conexão com Sheets: {e}")
        return None, None

# --- Carrega dados do Sheets em DataFrame ---
def carregar_dados():
    _, sh = conectar_sheets()
    if sh is None:
        return pd.DataFrame(columns=EXPECTED_COLUMNS)
    ws = sh.worksheet(ABA_OCORRENCIAS)
    data = ws.get_all_records()
    return pd.DataFrame(data)

# --- Carrega lista de tutores ---
def carregar_lista(aba, coluna):
    _, sh = conectar_sheets()
    if sh is None:
        return []
    ws = sh.worksheet(aba)
    return [str(v) for v in ws.col_values(1)[1:] if v]

# --- Funções para PDFs (placeholders) ---
def gerar_pdf_ocorrencias_aluno(aluno, sala, tutor, ocorrencias):
    return BytesIO(b"%PDF-1.4\n%Placeholder PDF")

def gerar_pdf_ocorrencia(ocorrencia):
    return BytesIO(b"%PDF-1.4\n%Placeholder PDF")

def gerar_pdf_tutor(relatorio, tutor, registros, img_buffer=None):
    return BytesIO(b"%PDF-1.4\n%Placeholder PDF")

def gerar_grafico_barras(relatorio, tutor):
    buf = BytesIO()
    fig, ax = plt.subplots()
    ax.bar(relatorio.keys(), relatorio.values())
    plt.title(f"Relatório {tutor}")
    plt.savefig(buf, format='png')
    buf.seek(0)
    return buf

# --- Rotas ---
@app.route("/")
def index():
    df = carregar_dados()
    registros = df.sort_values(by='DCO', ascending=False).to_dict('records')
    return render_template("index.html", registros=registros)

@app.route("/nova", methods=['GET', 'POST'])
def nova():
    if request.method == 'POST':
        try:
            _, sh = conectar_sheets()
            if sh is None:
                flash("Erro de conexão com a planilha!", 'danger')
                return redirect(url_for('nova'))

            ws_ocorrencias = sh.worksheet(ABA_OCORRENCIAS)

            # Próximo ID
            all_ids = ws_ocorrencias.col_values(1)[1:]
            last_id = max([int(x) for x in all_ids if x.isdigit()] or [0])
            novo_id = last_id + 1

            form_data = request.form
            agora = datetime.now(TZ_SAO).strftime('%H:%M:%S')
            hoje = datetime.now(TZ_SAO).strftime('%Y-%m-%d')

            nova_ocorrencia = {
                'ID': novo_id,
                'DCO': hoje,
                'HCO': agora,
                'Professor': form_data.get('professor'),
                'Sala': form_data.get('sala'),
                'Aluno': form_data.get('aluno'),
                'Tutor': form_data.get('tutor'),
                'Descrição da Ocorrência': form_data.get('descricao'),
                'Atendimento Professor': form_data.get('at_professor', ''),
                'ATT': '', 'ATC': '', 'ATG': '',
                'FT': 'SIM' if form_data.get('req_ft') == 'on' else 'NÃO',
                'FC': 'SIM' if form_data.get('req_fc') == 'on' else 'NÃO',
                'FG': 'SIM' if form_data.get('req_fg') == 'on' else 'NÃO',
                'DT': '', 'DC': '', 'DG': '',
                'Status': 'Em Aberto'
            }

            row_to_insert = [nova_ocorrencia.get(col, '') for col in EXPECTED_COLUMNS]
            ws_ocorrencias.append_row(row_to_insert)
            flash(f"Ocorrência ID {novo_id} salva com sucesso!", 'success')
            return redirect(url_for('index'))
        except Exception as e:
            print(f"Erro ao salvar ocorrência: {e}")
            flash(f"Erro ao salvar: {e}", 'danger')
            return redirect(url_for('nova'))

    tutores = carregar_lista(ABA_TUTORES, 'Tutor')
    return render_template("nova.html", tutores=tutores)

@app.route("/editar/<int:oid>", methods=['GET','POST'])
def editar(oid):
    df = carregar_dados()
    ocorrencia = df[df['ID']==oid].iloc[0].to_dict() if not df[df['ID']==oid].empty else None
    if not ocorrencia:
        flash("Ocorrência não encontrada.", 'warning')
        return redirect(url_for('index'))

    permissoes = {
        "att": ocorrencia.get('FT')=='SIM' and not ocorrencia.get('ATT'),
        "atc": ocorrencia.get('FC')=='SIM' and not ocorrencia.get('ATC'),
        "atg": ocorrencia.get('FG')=='SIM' and not ocorrencia.get('ATG')
    }

    agora = datetime.now(TZ_SAO).strftime('%Y-%m-%d %H:%M:%S')

    if request.method=='POST':
        try:
            _, sh = conectar_sheets()
            if sh is None:
                flash("Erro de conexão com a planilha!", 'danger')
                return redirect(url_for('editar', oid=oid))
            ws = sh.worksheet(ABA_OCORRENCIAS)
            cell = ws.find(str(oid))
            if not cell:
                flash("ID não encontrado", 'danger')
                return redirect(url_for('editar', oid=oid))
            row_index = cell.row

            form_data = request.form
            updates = {}

            if permissoes['att']:
                val = form_data.get('att_texto')
                if val and val.strip():
                    updates['ATT'] = val
                    updates['DT'] = agora
                    updates['FT'] = 'NÃO'

            if permissoes['atc']:
                val = form_data.get('atc_texto')
                if val and val.strip():
                    updates['ATC'] = val
                    updates['DC'] = agora
                    updates['FC'] = 'NÃO'

            if permissoes['atg']:
                val = form_data.get('atg_texto')
                if val and val.strip():
                    updates['ATG'] = val
                    updates['DG'] = agora
                    updates['FG'] = 'NÃO'

            novo_status = form_data.get('novo_status')
            if novo_status and novo_status != ocorrencia.get('Status'):
                updates['Status'] = novo_status

            nova_desc = form_data.get('descricao')
            if nova_desc and nova_desc != ocorrencia.get('Descrição da Ocorrência'):
                updates['Descrição da Ocorrência'] = nova_desc

            at_professor = form_data.get('at_professor')
            if at_professor and at_professor != ocorrencia.get('Atendimento Professor'):
                updates['Atendimento Professor'] = at_professor

            ft = updates.get('FT', ocorrencia.get('FT'))
            fc = updates.get('FC', ocorrencia.get('FC'))
            fg = updates.get('FG', ocorrencia.get('FG'))

            if ft=='SIM' or fc=='SIM' or fg=='SIM':
                updates['Status']='ATENDIMENTO'
            elif ft=='NÃO' and fc=='NÃO' and fg=='NÃO':
                updates['Status']='FINALIZADA'

            col_map = {col:i+1 for i,col in enumerate(EXPECTED_COLUMNS)}
            batch = []
            for col,val in updates.items():
                if col in col_map:
                    batch.append({
                        'range': rowcol_to_a1(row_index, col_map[col]),
                        'values': [[val]]
                    })
            if batch:
                ws.batch_update(batch)
                flash(f"Ocorrência ID {oid} atualizada com sucesso!", 'success')
            else:
                flash("Nenhuma alteração detectada.", 'info')
            return redirect(url_for('editar', oid=oid))
        except Exception as e:
            print(f"Erro ao editar ocorrência: {e}")
            flash(f"Erro ao editar: {e}", 'danger')
            return redirect(url_for('editar', oid=oid))

    tutores = carregar_lista(ABA_TUTORES, 'Tutor')
    status_list = ['Em Aberto', 'ATENDIMENTO', 'ASSINADA', 'FINALIZADA']
    return render_template("editar.html", ocorrencia=ocorrencia, tutores=tutores, permissoes=permissoes, status_list=status_list)

# --- Outras rotas de relatório e PDF podem ser adaptadas de forma similar usando batch_update ---

if __name__=='__main__':
    app.run(debug=True)
