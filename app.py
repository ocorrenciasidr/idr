import os
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
import base64

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash

# Imports para gspread e autenticação
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dateutil.parser import parse # Para análise robusta de datas

# ReportLab e FPDF (Geração de PDF)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from fpdf import FPDF # Usado no gerar_pdf_aluno
import io
from PIL import Image # Necessário se usar gráficos

# -------------------- Fuso horário --------------------
# Define o fuso horário de São Paulo
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except ImportError:
    # Fallback para sistemas sem zoneinfo (ex: Python < 3.9)
    TZ_SAO = timezone(timedelta(hours=-3))

app = Flask(__name__)
# Chave Secreta para uso de 'flash' e segurança de sessão
app.secret_key = os.environ.get('SECRET_KEY', 'idrgestao')

# -------------------- Configuração do Google Sheets --------------------

# !!! ATENÇÃO: SUBSTITUA PELO SEU ID DA PLANILHA REAL !!!
SHEET_ID = '1Jyle_LCRCKQfbDShoIj-9MPNIkVSkYxWaCwQrhmxSoE'
SCOPE = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
CREDENTIALS_FILE = 'credentials.json'

def conectar_sheets():
    """Conecta-se ao Google Sheets e retorna o objeto WorkSheet de 'Dados'."""
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPE)
        client = gspread.authorize(creds)
        
        # Abre a planilha principal
        sheet = client.open_by_key(SHEET_ID)
        
        # O nome da aba (WorkSheet) onde estão os dados das ocorrências
        ws_ocorrencias = sheet.worksheet("Dados")
        # O nome da aba (WorkSheet) onde está a lista de alunos/tutores
        ws_alunos = sheet.worksheet("Alunos")
        
        return ws_ocorrencias, ws_alunos
    except Exception as e:
        print(f"Erro ao conectar ao Google Sheets: {e}")
        return None, None

def carregar_dados():
    """Carrega os dados das ocorrências do Sheets para um DataFrame do Pandas."""
    ws_ocorrencias, _ = conectar_sheets()
    if ws_ocorrencias is None:
        return pd.DataFrame()
        
    data = ws_ocorrencias.get_all_records()
    df = pd.DataFrame(data)
    
    # Se a coluna 'Nº Ocorrência' (ID) existir, garante que é inteira e a define como índice
    if 'ID' in df.columns:
        df['ID'] = pd.to_numeric(df['ID'], errors='coerce').fillna(0).astype(int)
    
    # Lógica de formatação de colunas DCO/HCO, Status, etc. se necessário
    # Exemplo: df['Data Criação'] = pd.to_datetime(df['DCO'], errors='coerce').dt.strftime('%d/%m/%Y')
        
    return df

def carregar_alunos():
    """Carrega a lista de alunos e tutores."""
    _, ws_alunos = conectar_sheets()
    if ws_alunos is None:
        return pd.DataFrame()
        
    data = ws_alunos.get_all_records()
    df_alunos = pd.DataFrame(data)
    
    # Normaliza nomes de colunas para garantir 'Sala', 'Aluno', 'Tutor'
    df_alunos.columns = [c.strip() for c in df_alunos.columns]
        
    return df_alunos
    
def carregar_professores():
    """Carrega a lista de professores de uma planilha auxiliar (assumindo uma aba 'Professores')."""
    # Esta função precisaria de uma aba 'Professores' no Sheets
    # Por enquanto, retorna uma lista mockada ou você adapta para ler de outra aba/fonte
    return ["MARCELO", "ANA CAROLINA", "JERONIMO", "GESTÃO", "AGENTES ORG. ESCOLAR"]

def carregar_salas():
    """Carrega a lista de salas."""
    df_alunos = carregar_alunos()
    if not df_alunos.empty and 'Sala' in df_alunos.columns:
        return sorted(df_alunos['Sala'].dropna().unique().tolist())
    return ["1º SÉRIE A", "6º ANO A", "9º ANO D"] # Lista Mock se falhar

# --- ROTAS API (Assumidas pelo nova.html) ---

@app.route("/api/alunos_sala/<sala>", methods=["GET"])
def api_alunos_sala(sala):
    """Retorna a lista de alunos e seus tutores para uma sala específica."""
    df_alunos = carregar_alunos()
    
    if df_alunos.empty:
        return jsonify([])

    # Filtra pelo nome da sala
    alunos = df_alunos[df_alunos['Sala'].str.lower() == sala.lower()]
    
    # Seleciona as colunas 'Aluno' e 'Tutor'
    return jsonify(alunos[['Aluno', 'Tutor']].to_dict('records'))

# --- ROTAS PRINCIPAIS ---

@app.route("/")
def home():
    """Rota para a página inicial (home.html)."""
    return render_template("home.html")

@app.route("/ocorrencias", methods=["GET"])
def index():
    """Rota para a listagem/filtro de ocorrências (index.html)."""
    df = carregar_dados()
    
    # 1. Aplicar filtros (GET params)
    tutor_filtro = request.args.get('tutor')
    status_filtro = request.args.get('status')
    
    df_filtrado = df.copy()

    if tutor_filtro:
        df_filtrado = df_filtrado[df_filtrado['Tutor'] == tutor_filtro]
        
    if status_filtro:
        df_filtrado = df_filtrado[df_filtrado['Status'] == status_filtro]

    ocorrencias = df_filtrado.sort_values(by='ID', ascending=False).to_dict('records')
    
    # 2. Buscar opções únicas para filtros
    tutores = sorted(df['Tutor'].dropna().unique().tolist()) if not df.empty and 'Tutor' in df.columns else []
    status_opcoes = sorted(df['Status'].dropna().unique().tolist()) if not df.empty and 'Status' in df.columns else ['Aberto', 'ASSINADA', 'Finalizada']
    
    return render_template("index.html", 
                           ocorrencias=ocorrencias,
                           tutores=tutores,
                           status_opcoes=status_opcoes)

@app.route("/nova", methods=["GET", "POST"])
def nova():
    """Rota para o cadastro de nova ocorrência (nova.html)."""
    
    professores = carregar_professores()
    salas = carregar_salas()
    
    if request.method == "POST":
        try:
            ws_ocorrencias, _ = conectar_sheets()
            if ws_ocorrencias is None:
                flash("Erro de conexão com a planilha. Tente mais tarde.", "danger")
                return redirect(url_for("nova"))

            data = request.form
            
            # 1. Definir o próximo ID
            df = carregar_dados()
            next_id = df['ID'].max() + 1 if not df.empty else 1
            
            # 2. Gerar DCO e HCO (Data e Hora de Criação)
            agora = datetime.now(TZ_SAO)
            dco = agora.strftime('%Y-%m-%d')
            hco = agora.strftime('%H:%M:%S')

            # 3. Montar a linha (MANTENHA A ORDEM EXATA DAS COLUNAS DA SUA PLANILHA)
            # Colunas assumidas: ID, DCO, HCO, Professor, Sala, Aluno, Tutor, Descrição da Ocorrência, Atendimento Professor, ATT, ATC, ATG, FT, FC, FG, DT, DC, DG, Status
            nova_linha = [
                next_id,
                dco,
                hco,
                data.get('professor'), 
                data.get('sala'),
                data.get('aluno'),
                data.get('tutor'),
                data.get('descricao_ocorrencia'),
                '', # Atendimento Professor (inicia vazio)
                '', # ATT (Atendimento Tutor - inicia vazio)
                '', # ATC (Atendimento Coordenação - inicia vazio)
                '', # ATG (Atendimento Gestão - inicia vazio)
                'NÃO', # FT (Feito Tutor)
                'NÃO', # FC (Feito Coordenação)
                'NÃO', # FG (Feito Gestão)
                '', # DT (Data Tutor)
                '', # DC (Data Coordenação)
                '', # DG (Data Gestão)
                'Aberto' # Status inicial
            ]

            # 4. Inserir na planilha (A PARTIR DA LINHA 2, se já houver cabeçalho)
            ws_ocorrencias.append_row(nova_linha, value_input_option='USER_ENTERED')
            flash(f"Ocorrência Nº {next_id} registrada com sucesso!", "success")

        except Exception as e:
            flash(f"Erro ao salvar a ocorrência: {e}", "danger")
            # Log do erro (opcional)
            print(f"Erro no POST /nova: {e}")

        return redirect(url_for("index"))
        
    return render_template("nova.html", professores=professores, salas=salas)

@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    """Rota para visualização e edição de uma ocorrência específica (editar.html)."""
    df = carregar_dados()
    
    try:
        ocorrencia = df[df["ID"] == oid].iloc[0].to_dict()
    except IndexError:
        flash("Ocorrência não encontrada.", "danger")
        return redirect(url_for("index"))

    # Lógica de Permissões (ASSUMIDA)
    # Você precisaria de um sistema de login real para definir 'papel_do_usuario'
    permissoes = {'professor': True, 'tutor': True, 'coord': True, 'gestao': False}

    if request.method == "POST":
        # --- LÓGICA DE ATUALIZAÇÃO ---
        # 1. Reconectar ao Sheets
        ws_ocorrencias, _ = conectar_sheets()
        if ws_ocorrencias is None:
            flash("Erro de conexão com a planilha.", "danger")
            return redirect(url_for("index"))
            
        data = request.form
        
        # 2. Encontrar a linha da ocorrência pelo ID
        # A linha é o índice do pandas + 2 (cabeçalho + índice base zero)
        row_index = df[df["ID"] == oid].index[0] + 2
        
        # 3. Atualizar as células com base no papel e nas colunas da sua planilha:
        agora = datetime.now(TZ_SAO).strftime('%Y-%m-%d %H:%M:%S')

        # Colunas (Assumidas: Atendimento Professor = I, ATT = J, ATC = K, ATG = L)
        # Colunas de Flag e Data (Assumidas: FT = M, FC = N, FG = O, DT = P, DC = Q, DG = R)

        if permissoes['professor']:
             ws_ocorrencias.update_cell(row_index, 9, data.get('at_professor', '')) # Coluna I
        
        if permissoes['tutor']:
             ws_ocorrencias.update_cell(row_index, 10, data.get('at_tutor', ''))      # Coluna J
             if data.get('at_tutor'):
                 ws_ocorrencias.update_cell(row_index, 13, 'SIM') # FT = SIM (Coluna M)
                 ws_ocorrencias.update_cell(row_index, 16, agora) # DT (Coluna P)
             
        if permissoes['coord']:
             ws_ocorrencias.update_cell(row_index, 11, data.get('at_coord', ''))     # Coluna K
             if data.get('at_coord'):
                 ws_ocorrencias.update_cell(row_index, 14, 'SIM') # FC = SIM (Coluna N)
                 ws_ocorrencias.update_cell(row_index, 17, agora) # DC (Coluna Q)

        if permissoes['gestao']:
             ws_ocorrencias.update_cell(row_index, 12, data.get('at_gestao', ''))    # Coluna L
             if data.get('at_gestao'):
                 ws_ocorrencias.update_cell(row_index, 15, 'SIM') # FG = SIM (Coluna O)
                 ws_ocorrencias.update_cell(row_index, 18, agora) # DG (Coluna R)

        # Lógica para atualizar o Status
        novo_status = data.get('status', ocorrencia.get('Status', 'Aberto'))
        ws_ocorrencias.update_cell(row_index, 19, novo_status) # Status (Coluna S)

        flash(f"Ocorrência Nº {oid} atualizada com sucesso!", "success")
        return redirect(url_for("index"))
        
    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes)

@app.route("/tutoria")
def tutoria():
    """Rota para a página de Tutoria (tutoria.html)."""
    return render_template("tutoria.html")

# --- ROTAS DE RELATÓRIO (NOME CORRIGIDO: relatorio_inicial) ---

@app.route("/relatorio_inicial")
def relatorio_inicial(): # CORRIGIDO: O nome da função deve ser 'relatorio_inicial'
    """Rota para a página inicial de seleção de relatórios (relatorio_inicial.html)."""
    return render_template("relatorio_inicial.html")

@app.route("/relatorio_aluno", methods=["GET"])
def relatorio_aluno():
    """Rota para o filtro de ocorrências por aluno (relatorio_aluno.html)."""
    df = carregar_dados()
    df_alunos = carregar_alunos()
    
    salas_disponiveis = sorted(df_alunos['Sala'].dropna().unique().tolist())
    
    sala_sel = request.args.get('sala')
    aluno_sel = request.args.get('aluno')
    ocorrencias = []
    alunos_da_sala = []
    
    if sala_sel:
        alunos_da_sala = sorted(df_alunos[df_alunos['Sala'] == sala_sel]['Aluno'].dropna().unique().tolist())
        
        if aluno_sel:
            # Filtra as ocorrências do aluno na sala selecionada
            df_ocorrencias = df[
                (df['Sala'] == sala_sel) & 
                (df['Aluno'] == aluno_sel)
            ].sort_values(by='ID', ascending=False)
            ocorrencias = df_ocorrencias.to_dict('records')
            
    return render_template("relatorio_aluno.html", 
                           salas=salas_disponiveis, 
                           alunos=alunos_da_sala, 
                           ocorrencias=ocorrencias, 
                           sala_sel=sala_sel, 
                           aluno_sel=aluno_sel)

@app.route("/relatorio_geral", methods=["GET", "POST"])
def relatorio_geral():
    """Rota para a estatística geral (relatorio_geral.html)."""
    # **SUA LÓGICA COMPLETA DE FILTRAGEM DE DATAS, CÁLCULO DE ESTATÍSTICAS E GERAÇÃO DE GRÁFICO AQUI**
    
    # Placeholder: carrega todos os dados sem filtro de data, sem gráfico
    df = carregar_dados()
    
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    
    # Lógica de filtragem de data...
    
    return render_template("relatorio_geral.html", 
                           ocorrencias=df.to_dict('records'), 
                           data_inicio=data_inicio, 
                           data_fim=data_fim, 
                           grafico_base64="")

@app.route("/relatorio_tutor", methods=["GET"])
def relatorio_tutor():
    """Rota para a estatística por tutor (relatorio_tutor.html)."""
    # **SUA LÓGICA COMPLETA DE CÁLCULO POR TUTOR E PRAZOS AQUI**
    
    # Placeholder: Retorna dados vazios
    return render_template("relatorio_tutor.html", relatorio={})

@app.route("/relatorio_tutoraluno", methods=["GET"])
def relatorio_tutoraluno():
    """Rota para a lista Tutor/Tutorados (relatorio_tutoraluno.html)."""
    df_alunos = carregar_alunos()
    
    # Agrupa por Tutor e transforma em formato {Tutor: [Aluno1, Aluno2...]}
    dados = {}
    if not df_alunos.empty and 'Tutor' in df_alunos.columns:
        grupos = df_alunos.groupby('Tutor')
        for tutor, grupo in grupos:
            # Garante que o nome do tutor seja uma string válida
            if pd.isna(tutor) or tutor == '':
                tutor = 'SEM TUTOR NA PLANILHA'
            # Garante que 'Aluno' e 'Sala' existem para cada registro
            dados[tutor] = grupo[['Aluno', 'Sala']].to_dict('records')
            
    return render_template("relatorio_tutoraluno.html", dados=dados)

@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    """Rota POST para gerar o PDF a partir do filtro de aluno."""
    # **SUA LÓGICA COMPLETA DE GERAÇÃO DE PDF COM FPDF OU REPORTLAB AQUI**
    
    sala = request.form.get("sala")
    aluno = request.form.get("aluno")
    ocorrencias_ids = request.form.getlist("ocorrencias")
    
    if not ocorrencias_ids:
        flash("Selecione pelo menos uma ocorrência para gerar o PDF.", "warning")
        return redirect(url_for("relatorio_aluno", sala=sala, aluno=aluno))

    df = carregar_dados() 
    
    # Filtra as ocorrências selecionadas
    ocorrencias = df[df['ID'].isin([int(oid) for oid in ocorrencias_ids])]

    # Gerar PDF (usando FPDF)
    pdf = FPDF(unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    pdf.cell(0, 10, txt=f"RELATÓRIO DE OCORRÊNCIAS - {aluno} ({sala})", ln=1, align='C')
    pdf.ln(5)

    for i, row in ocorrencias.iterrows():
        pdf.set_fill_color(220, 220, 220)
        pdf.set_font("Arial", 'B', 10)
        pdf.cell(0, 7, txt=f"OCORRÊNCIA N° {row['ID']} - Status: {row['Status']}", ln=1, fill=True)
        
        pdf.set_font("Arial", size=9)
        pdf.multi_cell(0, 5, txt=f"Data/Hora: {row.get('DCO', 'N/A')} {row.get('HCO', 'N/A')}")
        pdf.multi_cell(0, 5, txt=f"Professor: {row.get('Professor', 'N/A')}")
        pdf.multi_cell(0, 5, txt=f"Tutor: {row.get('Tutor', 'N/A')}")
        pdf.multi_cell(0, 5, txt=f"Descrição: {row.get('Descrição da Ocorrência', 'N/A')}")
        
        pdf.ln(2)
        
        pdf.set_font("Arial", 'B', 9)
        pdf.cell(0, 5, txt="ATENDIMENTOS:", ln=1)
        
        pdf.set_font("Arial", size=8)
        pdf.multi_cell(0, 4, txt=f"Professor: {row.get('Atendimento Professor', 'N/A')}")
        pdf.multi_cell(0, 4, txt=f"Tutor (ATT): {row.get('ATT', 'N/A')}")
        pdf.multi_cell(0, 4, txt=f"Coordenação (ATC): {row.get('ATC', 'N/A')}")
        pdf.multi_cell(0, 4, txt=f"Gestão (ATG): {row.get('ATG', 'N/A')}")

        pdf.ln(5)

    output = BytesIO()
    pdf.output(output)
    output.seek(0)
    
    return send_file(output, download_name=f"relatorio_{aluno}_{sala}.pdf", as_attachment=True)
    
# --- ROTA DE EXEMPLO DE LÓGICA DE ABERT