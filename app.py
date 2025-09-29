import os
import json 
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
import base64

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash

# NOVO: Necessário para a correção do erro de data
from dateutil.parser import parse

# Imports para Google Sheets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ReportLab (gera o PDF)
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from fpdf import FPDF
import io

# -------------------- Fuso horário --------------------
# Define o fuso horário de São Paulo, robusto contra falta da biblioteca zoneinfo
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ_SAO = timezone(timedelta(hours=-3))

app = Flask(__name__)
# Use uma variável de ambiente para a chave secreta em produção
app.secret_key = os.environ.get('SECRET_KEY', 'sua_chave_secreta_padrao') 

# -------------------- Configuração do Google Sheets --------------------
def conectar_sheets():
    # Carrega credenciais do JSON armazenado na variável de ambiente
    creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
    if not creds_json:
        # Erro se a variável não estiver configurada no Render
        raise Exception("GOOGLE_SHEETS_CREDENTIALS environment variable not set.")
        
    creds_dict = json.loads(creds_json)
    
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # Busca o URL da planilha da variável de ambiente
    sheet_url = os.environ.get('SPREADSHEET_URL')
    if not sheet_url:
        raise Exception("SPREADSHEET_URL environment variable not set.")
        
    sh = client.open_by_url(sheet_url)
    return sh

def carregar_dados(sheet_name="Dados"):
    """Função base para carregar dados de qualquer aba da planilha."""
    try:
        sh = conectar_sheets()
        ws = sh.worksheet(sheet_name)
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        
        # Limpa nomes das colunas (remove espaços em branco extras)
        df.columns = df.columns.str.strip() 
        return df
    except Exception as e:
        print(f"Erro ao carregar dados da aba '{sheet_name}': {e}")
        flash(f"Erro ao carregar dados da aba '{sheet_name}'. Verifique as configurações.", "danger")
        return pd.DataFrame()

# -------------------- NOVAS FUNÇÕES DE CARREGAMENTO PARA CONTROLES --------------------

def carregar_lista_controle(sheet_name, column_name):
    """Carrega uma lista única de valores de uma coluna específica de uma aba."""
    # Reutiliza a função base para ler a aba
    df = carregar_dados(sheet_name=sheet_name)
    
    if df.empty or column_name not in df.columns:
        return []
    
    # Retorna uma lista ordenada, única e sem valores nulos
    return sorted(df[column_name].astype(str).str.strip().dropna().unique().tolist())

def carregar_professores():
    # Busca Professor na Aba Professores e Coluna A (assume header 'Professor')
    return carregar_lista_controle("Professores", "Professor")

def carregar_salas():
    # Busca Sala na Aba Salas e Coluna A (assume header 'Sala')
    return carregar_lista_controle("Salas", "Sala")

def carregar_alunos_tutor_df():
    # Busca dados na Aba Alunos: Coluna A (Sala), Coluna B (Aluno), Coluna C (Tutor)
    df = carregar_dados(sheet_name="Alunos")
    
    # Normaliza e valida colunas
    if 'Sala' in df.columns and 'Aluno' in df.columns and 'Tutor' in df.columns:
        # Pega apenas as colunas relevantes, limpa espaços e remove linhas sem Sala/Aluno
        df = df[['Sala', 'Aluno', 'Tutor']].astype(str).apply(lambda x: x.str.strip())
        return df.dropna(subset=['Aluno', 'Sala'])
    else:
        print("Erro: Colunas 'Sala', 'Aluno' ou 'Tutor' não encontradas na aba 'Alunos'.")
        return pd.DataFrame()

# -------------------- ROTAS --------------------

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/ocorrencias", methods=["GET"])
def index():
    df = carregar_dados() # Carrega a aba 'Dados'
    
    # Se o DataFrame estiver vazio, retorna a página vazia
    if df.empty:
        return render_template("Index.html", registros=[], tutores=[], status_list=[], salas=[])

    # Filtros
    tutor_filtro = request.args.get("tutor")
    status_filtro = request.args.get("status")
    
    if tutor_filtro:
        df = df[df["Tutor"].str.strip() == tutor_filtro.strip()]
    
    if status_filtro:
        df = df[df["Status"].str.strip() == status_filtro.strip()]
        
    # CORREÇÃO CRÍTICA DO CÁLCULO DE PRAZO (Tratamento de formatos de data)
    def calcular_prazo(data_str):
        if not data_str or data_str.strip() == '':
            return None
        
        # 1. Limpa a string (remove a hora se existir, ex: "2025-09-29 16:23:11")
        data_pura = data_str.split(' ')[0]
        
        try:
            # 2. Tenta o formato ISO (Y-M-D)
            data_criacao = datetime.strptime(data_pura, "%Y-%m-%d").replace(tzinfo=TZ_SAO)
        except ValueError:
            try:
                # 3. Tenta o formato brasileiro (D/M/Y)
                data_criacao = datetime.strptime(data_pura, "%d/%m/%Y").replace(tzinfo=TZ_SAO)
            except ValueError:
                # 4. Tenta o parser robusto (dayfirst=True para tratar D/M/Y sem ser estrito)
                try:
                    data_criacao = parse(data_pura, dayfirst=True).replace(tzinfo=TZ_SAO)
                except Exception:
                    # Se tudo falhar, retorna um valor nulo
                    return None 
        
        return (datetime.now(TZ_SAO) - data_criacao).days

    df["Prazo"] = df["DCO"].apply(calcular_prazo)
    # Fim da correção do Prazo
    
    registros = df.to_dict("records")
    
    # Listas para filtros
    tutores = carregar_lista_controle("Tutores", "Tutor") # Assume que existe uma aba 'Tutores'
    status_list = sorted(df["Status"].dropna().unique().tolist() if not df.empty else [])
    salas = carregar_salas()

    return render_template(
        "Index.html",
        registros=registros,
        tutores=tutores,
        status_list=status_list,
        salas=salas
    )

# --- ROTA NOVA (CORRIGIDA) ---
@app.route("/nova")
def nova():
    # Carrega listas das abas de controle
    professores = carregar_professores()
    salas = carregar_salas()
    
    # A lista de alunos será carregada via AJAX no nova.html
    return render_template("nova.html", professores=professores, salas=salas)

# --- ROTA API ALUNOS (CORRIGIDA) ---
@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    df_alunos = carregar_alunos_tutor_df()
    
    if df_alunos.empty:
        return jsonify([])

    # 1. Filtra o DataFrame pela sala (comparação sem case-sensitive e sem espaços extras)
    df_filtrado = df_alunos[
        df_alunos["Sala"].str.lower() == sala.strip().lower()
    ]
    
    # 2. Seleciona as colunas 'Aluno' e 'Tutor' e converte para JSON
    lista_alunos_tutor = df_filtrado[['Aluno', 'Tutor']].to_dict('records')
    
    return jsonify(lista_alunos_tutor)

@app.route("/salvar_ocorrencia", methods=["POST"])
def salvar_ocorrencia():
    data = request.form
    try:
        sh = conectar_sheets()
        ws = sh.worksheet("Dados") # Aba de dados principal

        # 1. Encontrar o próximo ID
        all_ids = ws.col_values(1)[1:] # Assume que ID está na primeira coluna (A)
        try:
            next_id = max([int(i) for i in all_ids if i.isdigit()]) + 1
        except:
            next_id = 1 # Primeiro registro
        
        # 2. Preparar os dados para inserção (adaptar aos seus cabeçalhos)
        data_criacao = datetime.now(TZ_SAO).strftime("%Y-%m-%d %H:%M:%S")

        # Sequência das colunas da sua planilha "Dados"
        nova_linha = [
            next_id,
            data_criacao,
            data.get('sala'),
            data.get('aluno'),
            data.get('tutor'),
            data.get('professor'),
            data.get('descricao'),
            data.get('status', 'Aberto'), # Define 'Aberto' como padrão
            '', # Atendimento Professor
            '', # Atendimento Tutor
            '', # Atendimento Coordenação
            ''  # Atendimento Gestão
            # Adicionar outras colunas conforme sua planilha
        ]

        # 3. Inserir na planilha
        ws.append_row(nova_linha)
        flash(f"Ocorrência Nº {next_id} registrada com sucesso!", "success")

    except Exception as e:
        flash(f"Erro ao salvar: {e}", "danger")

    return redirect(url_for("index"))

# --- ROTAS DE EDIÇÃO/RELATÓRIOS (Simplificadas) ---

@app.route("/editar/<oid>")
def editar(oid):
    # Lógica de edição (recuperar dados, verificar permissões, renderizar 'editar.html')
    df = carregar_dados()
    ocorrencia = df[df["Nº Ocorrência"] == int(oid)].to_dict('records')[0] if not df.empty else {}
    
    # Assumindo que você tem alguma lógica para definir 'permissoes'
    permissoes = {'professor': True, 'tutor': True, 'coord': False, 'gestao': False}
    
    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes)

@app.route("/relatorio_inicial")
def relatorio_inicial():
    return render_template("relatorio_inicial.html")

@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")

# Adicione aqui outras rotas como /relatorio_aluno, /gerar_pdf_aluno, etc.
# ...

if __name__ == "__main__":
    app.run(debug=True)