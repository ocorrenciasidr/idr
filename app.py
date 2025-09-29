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

# -------------------- FUNÇÕES DE CARREGAMENTO PARA CONTROLES --------------------

def carregar_lista_controle(sheet_name, column_name):
    """Carrega uma lista única de valores de uma coluna específica de uma aba."""
    df = carregar_dados(sheet_name=sheet_name)
    
    # Assumimos que o nome da coluna é o nome do header
    if df.empty or column_name not in df.columns:
        return []
    
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
    
    # Normaliza e valida colunas (Certifique-se que os cabeçalhos são EXATAMENTE estes)
    if 'Sala' in df.columns and 'Aluno' in df.columns and 'Tutor' in df.columns:
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
    
    if df.empty:
        return render_template("Index.html", registros=[], tutores=[], status_list=[], salas=[])

    # Filtros (assumindo que as colunas existem)
    tutor_filtro = request.args.get("tutor")
    status_filtro = request.args.get("status")
    
    if tutor_filtro and 'Tutor' in df.columns:
        df = df[df["Tutor"].str.strip() == tutor_filtro.strip()]
    
    if status_filtro and 'Status' in df.columns:
        df = df[df["Status"].str.strip() == status_filtro.strip()]
        
    # A coluna "Prazo" foi removida da tela Index, então não precisamos mais calcular o prazo aqui.
    
    registros = df.to_dict("records")
    
    # Listas para filtros
    tutores = carregar_lista_controle("Tutores", "Tutor") # Assume que existe uma aba 'Tutores'
    status_list = sorted(df["Status"].dropna().unique().tolist() if 'Status' in df.columns and not df.empty else [])
    salas = carregar_salas()

    return render_template(
        "Index.html",
        registros=registros,
        tutores=tutores,
        status_list=status_list,
        salas=salas
    )

# --- ROTA NOVA ---
@app.route("/nova")
def nova():
    professores = carregar_professores()
    salas = carregar_salas()
    
    return render_template("nova.html", professores=professores, salas=salas)

# --- ROTA API ALUNOS ---
@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    df_alunos = carregar_alunos_tutor_df()
    
    if df_alunos.empty:
        return jsonify([])

    # Filtra o DataFrame pela sala (comparação sem case-sensitive e sem espaços extras)
    df_filtrado = df_alunos[
        df_alunos["Sala"].str.lower() == sala.strip().lower()
    ]
    
    # Seleciona as colunas 'Aluno' e 'Tutor' e converte para JSON
    lista_alunos_tutor = df_filtrado[['Aluno', 'Tutor']].to_dict('records')
    
    return jsonify(lista_alunos_tutor)

# --- ROTA SALVAR OCORRÊNCIA (ATUALIZADA) ---
@app.route("/salvar_ocorrencia", methods=["POST"])
def salvar_ocorrencia():
    data = request.form
    try:
        sh = conectar_sheets()
        ws = sh.worksheet("Dados") # Aba de dados principal

        # 1. Encontrar o próximo ID
        all_ids = ws.col_values(1)[1:] # Assume que ID está na primeira coluna (A)
        try:
            # Filtra IDs válidos (numéricos)
            next_id = max([int(i) for i in all_ids if i.isdigit()]) + 1
        except:
            next_id = 1 # Primeiro registro
        
        # 2. Preparar os dados para inserção
        data_criacao = datetime.now(TZ_SAO).strftime("%Y-%m-%d %H:%M:%S")

        # Captura o campo de Atendimento Professor inicial
        atendimento_professor = data.get('at_professor', '')
        
        # Captura e define o valor para ATT/ATC/ATG: "SIM" se marcado, "" se não marcado
        req_att = data.get('req_att') # Checkbox para ATT
        req_atc = data.get('req_atc') # Checkbox para ATC
        req_atg = data.get('req_atg') # Checkbox para ATG

        att_value = "SIM" if req_att == 'on' else ""
        atc_value = "SIM" if req_atc == 'on' else ""
        atg_value = "SIM" if req_atg == 'on' else ""


        # Sequência das colunas da sua planilha "Dados" (Ajustar se necessário)
        # Assumindo a ordem: ID, DCO, Sala, Aluno, Tutor, Professor, Descricao, Status, Atendimento Professor, ATT, ATC, ATG
        nova_linha = [
            next_id,
            data_criacao,
            data.get('sala'),
            data.get('aluno'),
            data.get('tutor'),
            data.get('professor'),
            data.get('descricao'),
            data.get('status', 'Aberto'), # Define 'Aberto' como padrão
            atendimento_professor, # Atendimento Professor (Texto inicial da nova.html)
            att_value, # ATT (Agora recebe "SIM" ou "" do checkbox)
            atc_value, # ATC (Agora recebe "SIM" ou "" do checkbox)
            atg_value  # ATG (Agora recebe "SIM" ou "" do checkbox)
        ]

        # 3. Inserir na planilha
        ws.append_row(nova_linha)
        flash(f"Ocorrência Nº {next_id} registrada com sucesso!", "success")

    except Exception as e:
        flash(f"Erro ao salvar: {e}", "danger")

    return redirect(url_for("index"))

# --- ROTA DE EDIÇÃO (ATUALIZADA: Lógica de Permissões com 'papel') ---
@app.route("/editar/<oid>")
def editar(oid):
    df = carregar_dados()
    
    # Busca a ocorrência
    try:
        # A coluna de ID é a 'Nº Ocorrência' ou 'ID'
        id_col = 'Nº Ocorrência' if 'Nº Ocorrência' in df.columns else 'ID'
        # Garante que o ID é tratado como string para comparação robusta com a planilha
        ocorrencia = df[df[id_col].astype(str) == str(oid)].iloc[0].to_dict()
    except (IndexError, KeyError):
        flash(f"Ocorrência Nº {oid} não encontrada ou coluna de ID incorreta.", "danger")
        return redirect(url_for("index"))

    # 1. Obter o papel/ação da query string. Padrão é 'lupa' (view-only)
    papel = request.args.get('papel', 'lupa').lower() 
    
    # 2. Lógica de Permissões baseada no papel:
    # Por padrão, todos são 'readonly'
    permissoes = {'professor': False, 'tutor': False, 'coord': False, 'gestao': False, 'status': False, 'descricao': False}
    
    if papel == 'lapis':
        # LAPIS: Acesso total de Edição (Gestão)
        permissoes = {'professor': True, 'tutor': True, 'coord': True, 'gestao': True, 'status': True, 'descricao': True}
    elif papel == 'professor':
        # CLIQUE no Atendimento Professor: Edita apenas o campo Atendimento Professor
        permissoes['professor'] = True
    elif papel == 'tutor':
        # CLIQUE no ATT: Edita apenas o campo ATT
        permissoes['tutor'] = True
    elif papel == 'coord':
        # CLIQUE no ATC: Edita apenas o campo ATC
        permissoes['coord'] = True
    elif papel == 'gestao':
        # CLIQUE no ATG: Edita apenas o campo ATG
        permissoes['gestao'] = True

    # Se o papel for 'lupa', permanece tudo False (view-only)

    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes)


# --- ROTA ATUALIZAR OCORRÊNCIA (ATUALIZADA) ---
@app.route("/atualizar_ocorrencia/<oid>", methods=["POST"])
def atualizar_ocorrencia(oid):
    data = request.form
    try:
        sh = conectar_sheets()
        ws = sh.worksheet("Dados")
        
        # 1. Encontrar a linha (busca pelo ID na primeira coluna)
        cell_list = ws.find(str(oid), in_column=1) 
        if not cell_list:
            flash(f"Erro: Ocorrência Nº {oid} não encontrada.", "danger")
            return redirect(url_for("index"))
        
        row_index = cell_list.row
        
        # 2. Mapeamento das colunas (Verifique a ordem na sua planilha)
        # Assumindo a ordem: ... Status(8), Atendimento Professor(9), ATT(10), ATC(11), ATG(12)
        COL_STATUS = 8
        COL_AT_PROF = 9
        COL_ATT = 10
        COL_ATC = 11
        COL_ATG = 12
        COL_DESCRICAO = 7 # Descrição da Ocorrência

        # 3. Preparar as atualizações
        updates = []

        # Atualiza Status
        if 'status' in data:
             updates.append(gspread.Cell(row_index, COL_STATUS, data['status']))
        
        # Atualiza Descrição da Ocorrência
        if 'descricao' in data:
             updates.append(gspread.Cell(row_index, COL_DESCRICAO, data['descricao']))

        # Atualiza campos de Atendimento
        if 'at_professor' in data:
            updates.append(gspread.Cell(row_index, COL_AT_PROF, data['at_professor']))
            
        if 'at_tutor' in data:
            updates.append(gspread.Cell(row_index, COL_ATT, data['at_tutor']))

        if 'at_coord' in data:
             updates.append(gspread.Cell(row_index, COL_ATC, data['at_coord']))

        if 'at_gestao' in data:
             updates.append(gspread.Cell(row_index, COL_ATG, data['at_gestao']))

        # 4. Enviar atualizações em lote
        if updates:
            ws.update_cells(updates)
            flash(f"Ocorrência Nº {oid} atualizada com sucesso.", "success")
        else:
            flash("Nenhuma alteração para salvar.", "warning")

    except Exception as e:
        flash(f"Erro ao salvar: {e}", "danger")

    return redirect(url_for("index"))


@app.route("/relatorio_inicial")
def relatorio_inicial():
    return render_template("relatorio_inicial.html")

@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")

if __name__ == "__main__":
    app.run(debug=True)