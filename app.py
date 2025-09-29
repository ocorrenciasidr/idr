import os
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
import base64

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash

# Imports para Google Sheets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Imports para PDF (Mantenho os dois, usando FPDF para o exemplo de PDF simples)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from fpdf import FPDF
import io

# -------------------- Fuso horário --------------------
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ_SAO = timezone(timedelta(hours=-3))

# CONFIGURAÇÃO DO FLASK
app = Flask(__name__)
# A chave secreta é essencial para usar 'flash' (mensagens)
app.secret_key = os.environ.get('SECRET_KEY', 'sua_chave_secreta_padrao') 

# -------------------- Configuração do Google Sheets --------------------
# ATENÇÃO: Substitua 'SEU_JSON_DE_CREDENCIAIS.json' pelo nome real do seu arquivo de credenciais.
# E 'NOME_DA_SUA_PLANILHA' pelo nome exato da sua planilha no Google Sheets.
JSON_CRED = os.environ.get('GOOGLE_CREDENTIALS_JSON', 'SEU_JSON_DE_CREDENCIAIS.json')
SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME', 'NOME_DA_SUA_PLANILHA')
DATA_WORKSHEET_NAME = 'Dados'
ALUNOS_WORKSHEET_NAME = 'Alunos'
PROFESSORES_WORKSHEET_NAME = 'Professores'
SALAS_WORKSHEET_NAME = 'Salas'


def conectar_sheets():
    # Tenta carregar as credenciais do JSON, ou de uma variável de ambiente se estiver em um servidor
    if os.path.exists(JSON_CRED):
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_CRED, scope)
    else:
        # Tenta carregar do ambiente se estiver em um serviço como Heroku/Render
        creds_json = json.loads(os.environ.get('GSPREAD_CREDENTIALS', '{}'))
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)

    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME)
    return sheet.worksheet(DATA_WORKSHEET_NAME)

def carregar_dados():
    try:
        ws = conectar_sheets()
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        
        # Renomear colunas para consistência, se necessário
        if 'ID' in df.columns:
            df.rename(columns={'ID': 'Nº Ocorrência'}, inplace=True)
        if 'DCO' in df.columns:
            df.rename(columns={'DCO': 'Data Criação'}, inplace=True)
        
        return df
    except Exception as e:
        print(f"Erro ao carregar dados: {e}")
        return pd.DataFrame()

def obter_proximo_id(ws):
    # Obtém a coluna de IDs (coluna 1)
    ids = ws.col_values(1)[1:] # Ignora o cabeçalho
    if ids and ids[-1].isdigit():
        return int(ids[-1]) + 1
    return 1 # Começa em 1 se não houver dados ou IDs inválidos

def carregar_listas(sheet_name):
    try:
        sheet = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(JSON_CRED, ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']))
        ws = sheet.open(SHEET_NAME).worksheet(sheet_name)
        return [item[0] for item in ws.get_all_values()][1:] # Retorna lista, ignorando cabeçalho
    except Exception as e:
        print(f"Erro ao carregar lista de {sheet_name}: {e}")
        return []

# -------------------- ROTAS DA APLICAÇÃO --------------------

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/index")
def index():
    df = carregar_dados()
    
    # Lógica de filtros (tutor e status)
    tutor_filtro = request.args.get('tutor')
    status_filtro = request.args.get('status')
    
    if tutor_filtro:
        df = df[df['Tutor'] == tutor_filtro]
    if status_filtro:
        df = df[df['Status'] == status_filtro]

    # Ordena as ocorrências pela mais recente
    df.sort_values(by='Nº Ocorrência', ascending=False, inplace=True)

    # Convertendo para lista de dicionários para o Jinja
    ocorrencias = df.to_dict('records')
    
    # Carregar listas de filtros (tutores e status únicos)
    tutores = carregar_listas(ALUNOS_WORKSHEET_NAME)
    tutores = sorted(list(set(t['Tutor'] for t in ocorrencias if t['Tutor'])))
    
    status_unicos = sorted(list(df['Status'].unique()))
    
    return render_template("index.html", 
                           ocorrencias=ocorrencias, 
                           tutores=tutores, 
                           status_opcoes=status_unicos,
                           tutor_selecionado=tutor_filtro,
                           status_selecionado=status_filtro)


@app.route("/nova")
def nova():
    # Carrega as listas para os campos de seleção
    salas = carregar_listas(SALAS_WORKSHEET_NAME)
    professores = carregar_listas(PROFESSORES_WORKSHEET_NAME)
    
    # Para a tela 'nova.html', a lista de alunos é carregada via API (api/alunos_sala)
    
    return render_template("nova.html", salas=salas, professores=professores)


@app.route("/nova_ocorrencia", methods=["POST"])
def nova_ocorrencia():
    data = request.form
    try:
        ws = conectar_sheets()
        
        # 1. Obter o próximo ID
        next_id = obter_proximo_id(ws)

        # 2. Obter data/hora atual
        agora = datetime.now(TZ_SAO)
        dco = agora.strftime("%Y-%m-%d")
        hco = agora.strftime("%H:%M:%S")

        # 3. Construção da linha na ORDEM CORRETA DA PLANILHA (19 Colunas)
        nova_linha = [
            str(next_id),           # 1. ID 
            dco,                    # 2. DCO (Data Criação Ocorrência)
            hco,                    # 3. HCO (Hora Criação Ocorrência)
            data.get('professor'),  # 4. Professor
            data.get('sala'),       # 5. Sala
            data.get('aluno'),      # 6. Aluno
            data.get('tutor'),      # 7. Tutor
            data.get('descricao'),  # 8. Descrição da Ocorrência
            '',                     # 9. Atendimento Professor (Vazio)
            '',                     # 10. ATT (Vazio)
            '',                     # 11. ATC (Vazio)
            '',                     # 12. ATG (Vazio)
            'NÃO',                  # 13. FT (Feedback Tutor) (Padrão)
            'NÃO',                  # 14. FC (Feedback Coordenação) (Padrão)
            'NÃO',                  # 15. FG (Feedback Gestão) (Padrão)
            '',                     # 16. DT (Data Feedback Tutor) (Vazio)
            '',                     # 17. DC (Data Feedback Coordenação) (Vazio)
            '',                     # 18. DG (Data Feedback Gestão) (Vazio)
            'Aberto'                # 19. Status (Padrão)
        ]

        # 4. Inserir na planilha
        ws.append_row(nova_linha)
        flash(f"Ocorrência Nº {next_id} registrada com sucesso!", "success")

    except Exception as e:
        flash(f"Erro ao salvar: {e}", "danger")

    return redirect(url_for("index")) 

@app.route("/editar/<oid>")
def editar(oid):
    df = carregar_dados()
    
    # Encontra a linha da ocorrência
    ocorrencia_match = df[df['Nº Ocorrência'] == int(oid)]
    
    if ocorrencia_match.empty:
        flash("Ocorrência não encontrada.", "danger")
        return redirect(url_for("index"))
        
    ocorrencia = ocorrencia_match.iloc[0].to_dict()
    
    # Lógica de Permissões (Exemplo. Adapte conforme o seu sistema de login real)
    permissoes = {'professor': True, 'tutor': True, 'coord': True, 'gestao': True} 
    
    # Adicionar chaves para evitar erros na template se não existirem
    ocorrencia['Atendimento Professor'] = ocorrencia.get('Atendimento Professor', '')
    ocorrencia['ATT'] = ocorrencia.get('ATT', '')
    ocorrencia['ATC'] = ocorrencia.get('ATC', '')
    ocorrencia['ATG'] = ocorrencia.get('ATG', '')
    
    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes)


@app.route("/salvar_edicao/<oid>", methods=["POST"])
def salvar_edicao(oid):
    data = request.form
    try:
        ws = conectar_sheets()
        
        # 1. Encontra o número da linha na planilha
        cell = ws.find(oid, in_column=1)
        row_num = cell.row

        # 2. Prepara a linha de dados na ORDEM CORRETA DA PLANILHA (19 Colunas)
        # Campos de contexto são enviados como hidden ou readonly com name
        linha_completa = [
            data.get('id'),             # 1. ID (Hidden)
            data.get('dco'),            # 2. DCO (Hidden)
            data.get('hco'),            # 3. HCO (Hidden)
            data.get('professor'),      # 4. Professor (Readonly)
            data.get('sala'),           # 5. Sala (Readonly)
            data.get('aluno'),          # 6. Aluno (Readonly)
            data.get('tutor'),          # 7. Tutor (Readonly)
            data.get('descricao'),      # 8. Descrição da Ocorrência (Readonly)
            data.get('at_professor'),   # 9. Atendimento Professor (Editável)
            data.get('at_tutor'),       # 10. ATT (Editável)
            data.get('at_coord'),       # 11. ATC (Editável)
            data.get('at_gestao'),      # 12. ATG (Editável)
            data.get('ft'),             # 13. FT (Hidden)
            data.get('fc'),             # 14. FC (Hidden)
            data.get('fg'),             # 15. FG (Hidden)
            data.get('dt'),             # 16. DT (Hidden)
            data.get('dc'),             # 17. DC (Hidden)
            data.get('dg'),             # 18. DG (Hidden)
            data.get('status')          # 19. Status (Hidden)
        ]

        # 3. Atualiza a linha na planilha
        ws.update(f'A{row_num}:S{row_num}', [linha_completa])
        
        flash(f"Ocorrência Nº {oid} atualizada com sucesso!", "success")

    except Exception as e:
        flash(f"Erro ao salvar edição: {e}", "danger")

    return redirect(url_for("index")) # Redireciona para a lista principal

# Exemplo de API para carregar alunos dinamicamente em nova.html
@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    try:
        sheet = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(JSON_CRED, ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']))
        ws = sheet.open(SHEET_NAME).worksheet(ALUNOS_WORKSHEET_NAME)
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        
        # Filtra os alunos pela sala
        alunos = df[df['Sala'] == sala][['Aluno', 'Tutor']].to_dict('records')
        return jsonify(alunos)
    except Exception as e:
        print(f"Erro na API de alunos: {e}")
        return jsonify([])

# --- Adicione aqui suas outras rotas (relatórios, tutoria, etc.) ---

if __name__ == "__main__":
    # Importante: O Flask no Replit ou em ambientes de produção deve usar 
    # host='0.0.0.0' para ser acessível externamente.
    app.run(debug=True, host='0.0.0.0', port=5000)