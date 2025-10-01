import os
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
import base64
from urllib.parse import urlencode

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash, abort

# Imports para Google Sheets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Imports para Geração de PDF e Gráficos (necessita de `fpdf` e `matplotlib`)
from fpdf import FPDF
# Você precisará instalar o matplotlib: pip install matplotlib fpdf
try:
    import matplotlib.pyplot as plt
    plt.switch_backend('Agg') # Usa backend que não precisa de display gráfico
    HAS_MATPLOTLIB = True
except ImportError:
    plt = None
    HAS_MATPLOTLIB = False
    print("Aviso: Matplotlib não instalado. Gráficos não serão gerados.")

# Tenta importar ZoneInfo (Python 3.9+) ou usa timezone fallback
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except Exception:
    # Fallback para versões mais antigas
    TZ_SAO = timezone(timedelta(hours=-3))

app = Flask(__name__)
# Chave secreta: Mantenha esta chave secreta!
app.secret_key = os.environ.get('SECRET_KEY', 'idrgestao')

# !!! ATENÇÃO: SUBSTITUA PELO SEU ID DA PLANILHA REAL !!!
# Use este ID fictício para simulação. Se for rodar localmente, mude para um ID real.
SHEET_ID = os.environ.get('SHEET_ID', '1Jyle_LCRCKQfbDShoIj-9MPNIkVSkYxWaCwQrhmxSoE')

# Colunas esperadas (para validação e processamento)
EXPECTED_COLUMNS = [
    'ID', 'DCO', 'HCO', 'Professor', 'Sala', 'Aluno', 'Tutor', 
    'Descrição da Ocorrência', 'Atendimento Professor', 'ATT', 'ATC', 'ATG', 
    'FT', 'FC', 'FG', 'DT', 'DC', 'DG', 'Status'
]
# Nomes das abas da planilha
ABA_OCORRENCIAS = 'Dados'
ABA_PROFESSORES = 'Professores'
ABA_SALAS = 'Salas'
ABA_ALUNOS = 'Alunos'
ABA_TUTORES = 'Tutores' # Usado para carregar a lista de tutores no detalhes


# -------------------- Funções Auxiliares (Conexão e Carga) --------------------

def conectar_sheets():
    """
    Tenta estabelecer a conexão com Google Sheets.
    Prioriza a variável de ambiente GSPREAD_CREDENTIALS (para Render).
    Faz fallback para o arquivo service_account.json (para desenvolvimento local).
    """
    
    # 1. Tenta carregar as credenciais da variável de ambiente (Preferível no Render)
    creds_json = os.environ.get('GSPREAD_CREDENTIALS')
    
    # 2. Tenta carregar do arquivo local (Fallback para desenvolvimento)
    use_file = False
    if not creds_json and os.path.exists('service_account.json'):
        print("AVISO: Variável de ambiente GSPREAD_CREDENTIALS não encontrada. Usando 'service_account.json' local.")
        use_file = True
    
    if not creds_json and not use_file:
        print("ERRO: Credenciais de Google Sheets não configuradas.")
        return None, None

    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    client = None
    spreadsheet = None
    
    try:
        if use_file:
            # Conecta usando arquivo local
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
        else:
            # Conecta usando variável de ambiente (JSON string)
            creds_info = json.loads(creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
            
        client = gspread.authorize(creds)
        
        # Abre a planilha pelo ID (mais seguro que URL)
        spreadsheet = client.open_by_key(SHEET_ID)
        return client, spreadsheet
        
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"ERRO: Planilha com ID '{SHEET_ID}' não encontrada ou sem permissão. Verifique o ID e o compartilhamento.")
        return None, None
    except Exception as e:
        print(f"Erro ao conectar com Google Sheets: {e}")
        return None, None

def carregar_dados():
    """Carrega os dados da aba 'Dados' no Sheets para um DataFrame do Pandas."""
    client, spreadsheet = conectar_sheets()
    if spreadsheet is None:
        # Retorna um DataFrame vazio se a conexão falhar
        return pd.DataFrame(columns=EXPECTED_COLUMNS) 
        
    try:
        ws_ocorrencias = spreadsheet.worksheet(ABA_OCORRENCIAS)
        # Tenta carregar os dados. Assume que a primeira linha é o cabeçalho.
        data = ws_ocorrencias.get_all_records(head=1, default_blank='')
        
        df = pd.DataFrame(data)
        
        if not df.empty:
            df['ID'] = pd.to_numeric(df['ID'], errors='coerce').fillna(0).astype(int)
            df = df.sort_values(by='ID', ascending=False)
            for col in ['Tutor', 'Status']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.strip()
            
            # Adiciona colunas faltantes para garantir que a lógica de relatorio não quebre
            for col in EXPECTED_COLUMNS:
                if col not in df.columns:
                    df[col] = ''
        return df
    except gspread.exceptions.WorksheetNotFound:
        print(f"ERRO: Aba '{ABA_OCORRENCIAS}' não encontrada. Verifique o nome.")
        return pd.DataFrame(columns=EXPECTED_COLUMNS)
    except Exception as e:
        print(f"Erro ao carregar dados da aba '{ABA_OCORRENCIAS}': {e}")
        return pd.DataFrame(columns=EXPECTED_COLUMNS)

def carregar_lista(nome_aba, coluna_chave):
    """Carrega uma lista de valores únicos de uma aba específica."""
    client, spreadsheet = conectar_sheets()
    if spreadsheet is None: return []

    try:
        ws_lista = spreadsheet.worksheet(nome_aba)
        data = ws_lista.get_all_records(head=1, default_blank='')
        df = pd.DataFrame(data)

        if not df.empty and coluna_chave in df.columns:
            return sorted(list(df[coluna_chave].astype(str).str.strip().unique()))
            
        return []
    except gspread.exceptions.WorksheetNotFound:
        print(f"ATENÇÃO: Aba '{nome_aba}' não encontrada na planilha. Verifique o nome.")
        return []
    except Exception as e:
        print(f"Erro ao carregar lista de {nome_aba}: {e}")
        return []

# -------------------- Funções de Geração de PDF e Gráfico --------------------

def gerar_grafico_barras(relatorio_data, nome_tutor):
    """Gera um gráfico de barras com o desempenho do tutor e retorna como BytesIO."""
    if not HAS_MATPLOTLIB:
        return None

    categorias = ['Prazo (<= 2 dias)', 'Fora do Prazo (> 2 dias)', 'Não Atendidas']
    valores = [
        relatorio_data.get('prazo', 0),
        relatorio_data.get('fora', 0),
        relatorio_data.get('nao', 0)
    ]
    
    cores = ['#4CAF50', '#FF9800', '#F44336'] # Verde, Laranja, Vermelho
    
    plt.figure(figsize=(8, 4))
    barras = plt.bar(categorias, valores, color=cores)
    
    plt.title(f'Desempenho de Atendimento - {nome_tutor}', fontsize=12)
    plt.ylabel('Número de Ocorrências', fontsize=10)
    plt.xticks(rotation=15, ha='right', fontsize=8) 
    plt.yticks(fontsize=8)
    
    # Adiciona rótulo de valor em cima de cada barra
    for bar in barras:
        yval = bar.get_height()
        if yval > 0:
            plt.text(bar.get_x() + bar.get_width()/2.0, yval + 0.1, int(yval), ha='center', va='bottom', fontsize=9)
            
    plt.tight_layout()
    
    img_buffer = BytesIO()
    plt.savefig(img_buffer, format='png')
    plt.close() # Fecha a figura para liberar memória
    img_buffer.seek(0)
    return img_buffer

def gerar_pdf_tutor(relatorio_data, nome_tutor, df_registros, img_buffer=None):
    """Gera o PDF do relatório do Tutor usando FPDF."""
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # Configuração de Fonte (Arial)
    pdf.set_font('Arial', 'B', 16)
    
    # Título
    pdf.set_fill_color(0, 123, 255) # Azul
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 10, 'SGCE - Relatório de Desempenho do Tutor', 0, 1, 'C', 1)
    
    pdf.ln(5)
    pdf.set_font('Arial', 'B', 14)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, f'Tutor: {nome_tutor}', 0, 1, 'L')
    pdf.set_font('Arial', '', 10)
    pdf.cell(0, 5, f'Gerado em: {datetime.now(TZ_SAO).strftime("%d/%m/%Y %H:%M:%S")}', 0, 1, 'L')
    pdf.ln(5)
    
    # 1. Resumo Estatístico
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 7, '1. Resumo Estatístico', 0, 1, 'L')
    pdf.set_font('Arial', '', 10)
    
    pdf.set_fill_color(220, 220, 220) # Cinza claro
    pdf.cell(60, 6, 'Total de Ocorrências Atribuídas:', 1, 0, 'L', 1)
    pdf.cell(30, 6, str(relatorio_data.get('total', 0)), 1, 1, 'C', 0)
    
    pdf.cell(60, 6, 'Atendidas no Prazo (<= 2 dias):', 1, 0, 'L', 1)
    pdf.cell(30, 6, str(relatorio_data.get('prazo', 0)), 1, 1, 'C', 0)
    
    pdf.cell(60, 6, 'Atendidas Fora do Prazo (> 2 dias):', 1, 0, 'L', 1)
    pdf.cell(30, 6, str(relatorio_data.get('fora', 0)), 1, 1, 'C', 0)
    
    pdf.cell(60, 6, 'Ocorrências Não Atendidas (Vencidas):', 1, 0, 'L', 1)
    pdf.cell(30, 6, str(relatorio_data.get('nao', 0)), 1, 1, 'C', 0)
    
    pdf.ln(5)
    
    # 2. Gráfico
    if img_buffer:
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 7, '2. Gráfico de Desempenho', 0, 1, 'L')
        # Adiciona o gráfico (largura 120, altura 60)
        pdf.image(img_buffer, x=45, w=120, h=60, type='png')
        pdf.ln(60)

    # 3. Detalhes das Ocorrências
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 7, '3. Detalhes das Ocorrências', 0, 1, 'L')
    pdf.ln(1)
    
    # Cabeçalho da Tabela
    pdf.set_font('Arial', 'B', 8)
    pdf.set_fill_color(190, 190, 190)
    col_widths = [10, 20, 20, 80, 60]
    
    headers = ['ID', 'Data', 'Aluno', 'Situação (Att. Tutor)', 'Status Atual']
    for i, header in enumerate(headers):
        pdf.cell(col_widths[i], 6, header, 1, 0, 'C', 1)
    pdf.ln()

    # Linhas da Tabela
    pdf.set_font('Arial', '', 8)
    pdf.set_text_color(0, 0, 0)
    
    for index, row in df_registros.iterrows():
        # Cor de fundo baseada no status
        status_tutor = row.get('StatusTutor', '')
        if status_tutor == 'prazo':
            pdf.set_fill_color(220, 255, 220) # Verde claro
        elif status_tutor == 'fora':
            pdf.set_fill_color(255, 255, 220) # Amarelo claro
        elif status_tutor == 'nao':
            pdf.set_fill_color(255, 220, 220) # Vermelho claro
        else:
            pdf.set_fill_color(240, 240, 240) # Padrão
            
        # Conteúdo
        pdf.cell(col_widths[0], 6, str(row.get('ID', '')), 1, 0, 'C', 1)
        pdf.cell(col_widths[1], 6, row.get('DCO', ''), 1, 0, 'C', 1)
        pdf.cell(col_widths[2], 6, row.get('Aluno', ''), 1, 0, 'L', 1)
        pdf.cell(col_widths[3], 6, row.get('StatusTutorTexto', ''), 1, 0, 'L', 1) # Texto formatado
        pdf.cell(col_widths[4], 6, row.get('Status', ''), 1, 1, 'L', 1)
        
    pdf.set_fill_color(255, 255, 255) # Volta para branco

    return BytesIO(pdf.output(dest='S').encode('latin1'))

# Função para gerar PDF de Ocorrência Individual
def gerar_pdf_ocorrencia(ocorrencia):
    """Gera o PDF de uma única ocorrência."""
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    
    # Título Principal
    pdf.set_font('Arial', 'B', 16)
    pdf.set_fill_color(0, 123, 255)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 10, f'SGCE - Ocorrência ID: {ocorrencia.get("ID", "")}', 0, 1, 'C', 1)
    
    pdf.ln(5)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 7, 'Dados Principais', 0, 1, 'L')
    pdf.set_font('Arial', '', 10)
    pdf.set_fill_color(240, 240, 240)

    def draw_field(label, value, fill=1):
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(50, 6, label, 1, 0, 'L', fill)
        pdf.set_font('Arial', '', 10)
        pdf.cell(0, 6, str(value), 1, 1, 'L', fill)
        
    draw_field('Data e Hora:', f"{ocorrencia.get('DCO', '')} - {ocorrencia.get('HCO', '')}", 1)
    draw_field('Professor:', ocorrencia.get('Professor', ''), 0)
    draw_field('Sala:', ocorrencia.get('Sala', ''), 1)
    draw_field('Aluno:', ocorrencia.get('Aluno', ''), 0)
    draw_field('Tutor:', ocorrencia.get('Tutor', ''), 1)
    draw_field('Status:', ocorrencia.get('Status', ''), 0)
    pdf.ln(5)
    
    # Descrição da Ocorrência
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 7, 'Descrição da Ocorrência', 0, 1, 'L')
    pdf.set_font('Arial', '', 10)
    pdf.multi_cell(0, 5, ocorrencia.get('Descrição da Ocorrência', 'N/A'), 1, 'L', 0)
    pdf.ln(5)

    # Atendimentos
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 7, 'Atendimentos Registrados', 0, 1, 'L')
    
    def draw_atendimento(label, texto, data_hora, fill):
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 6, label, 1, 1, 'L', fill)
        pdf.set_font('Arial', '', 8)
        # Tenta formatar a data, mas usa a string original se der erro
        data_formatada = data_hora
        try:
            if data_hora:
                dt_obj = pd.to_datetime(data_hora, errors='coerce')
                if not pd.isna(dt_obj):
                    data_formatada = dt_obj.strftime('%d/%m/%Y %H:%M:%S')
        except Exception:
            pass # Usa a string original
            
        pdf.cell(0, 4, f"Registro: {data_formatada}", 0, 1, 'R')
        pdf.set_font('Arial', '', 10)
        pdf.multi_cell(0, 5, texto if texto else 'Nenhum atendimento registrado.', 1, 'L', 0)
        pdf.ln(2)

    draw_atendimento('Atendimento do Professor (Registro Inicial)', ocorrencia.get('Atendimento Professor', ''), f"{ocorrencia.get('DCO', '')} {ocorrencia.get('HCO', '')}", 1)
    draw_atendimento('Atendimento do Tutor (ATT)', ocorrencia.get('ATT', ''), ocorrencia.get('DT', ''), 0)
    draw_atendimento('Atendimento da Coordenação (ATC)', ocorrencia.get('ATC', ''), ocorrencia.get('DC', ''), 1)
    draw_atendimento('Atendimento da Gestão (ATG)', ocorrencia.get('ATG', ''), ocorrencia.get('DG', ''), 0)

    return BytesIO(pdf.output(dest='S').encode('latin1'))

from io import BytesIO
from fpdf import FPDF

def gerar_pdf_ocorrencias_aluno(aluno, sala, tutor, ocorrencias):
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Cabeçalho
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "RELATÓRIO DE REGISTRO DE OCORRÊNCIAS", 0, 1, "C")
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 8, "E.E. PEI PROFESSOR IRENE DIAS RIBEIRO", 0, 1, "C")
    pdf.ln(5)

    # Dados principais do aluno
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 7, f"Aluno: {aluno}    Sala: {sala}", 0, 1, "L")
    pdf.cell(0, 7, f"Tutor: {tutor}", 0, 1, "L")
    pdf.ln(3)

    for oc in ocorrencias:
        pdf.set_font("Arial", "B", 11)
        pdf.cell(0, 7, f"Ocorrência nº: {oc.get('ID', '')}", 0, 1, "L")

        pdf.set_font("Arial", "", 10)
        pdf.cell(0, 6, f"Data: {oc.get('DCO', '')}    Hora: {oc.get('HCO', '')}", 0, 1, "L")
        pdf.cell(0, 6, f"Professor: {oc.get('Professor', '')}", 0, 1, "L")

        pdf.ln(2)
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 6, "Descrição:", 0, 1, "L")
        pdf.set_font("Arial", "", 10)
        pdf.multi_cell(0, 6, oc.get("Descrição da Ocorrência", ""))
        pdf.ln(2)

        # Atendimento Professor
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 6, "Atendimento Professor:", 0, 1, "L")
        pdf.set_font("Arial", "", 10)
        texto = oc.get("Atendimento Professor", "NÃO APLICÁVEL")
        pdf.multi_cell(0, 6, texto if texto else "NÃO APLICÁVEL")
        pdf.ln(1)

        # Atendimento Tutor
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 6, "Atendimento Tutor (Se solicitado):", 0, 1, "L")
        pdf.set_font("Arial", "", 10)
        texto = oc.get("ATT", "")
        if texto:
            pdf.multi_cell(0, 6, texto)
            data_resp = oc.get("DT", "")
            if data_resp:
                pdf.cell(0, 6, f"Data da Resposta: {data_resp}", 0, 1, "L")
        else:
            pdf.multi_cell(0, 6, "NÃO APLICÁVEL")
        pdf.ln(1)

        # Atendimento Coordenação
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 6, "Atendimento Coordenação (Se solicitado):", 0, 1, "L")
        pdf.set_font("Arial", "", 10)
        texto = oc.get("ATC", "")
        if texto:
            pdf.multi_cell(0, 6, texto)
            data_resp = oc.get("DC", "")
            if data_resp:
                pdf.cell(0, 6, f"Data da Resposta: {data_resp}", 0, 1, "L")
        else:
            pdf.multi_cell(0, 6, "NÃO APLICÁVEL")
        pdf.ln(1)

        # Atendimento Gestão
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 6, "Atendimento Gestão (Se solicitado):", 0, 1, "L")
        pdf.set_font("Arial", "", 10)
        texto = oc.get("ATG", "")
        if texto:
            pdf.multi_cell(0, 6, texto)
            data_resp = oc.get("DG", "")
            if data_resp:
                pdf.cell(0, 6, f"Data da Resposta: {data_resp}", 0, 1, "L")
        else:
            pdf.multi_cell(0, 6, "NÃO APLICÁVEL")
        pdf.ln(2)

        # Linha divisória
        pdf.set_draw_color(0, 0, 0)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)

    # Assinatura final
    pdf.ln(10)
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 8, "Assinatura Responsável: __________________________________", 0, 1, "L")
    pdf.cell(0, 8, "Data: __ / __ / ______", 0, 1, "L")

    return BytesIO(pdf.output(dest="S").encode("latin1"))

# -------------------- Rotas do Flask --------------------

@app.route("/")
def home():
    """Renderiza a página inicial (Home)."""
    return render_template("home.html")

# ROTA /ocorrencias removida pois é redundante com /index e usava conexão rígida.

@app.route("/index")
def index():
    """Lista todas as ocorrências com filtros."""
    df = carregar_dados()
    
    tutor_filtro = request.args.get('tutor', '')
    status_filtro = request.args.get('status', '')

    df_filtrado = df.copy()
    
    if tutor_filtro:
        # Garante que a coluna 'Tutor' existe antes de filtrar
        if 'Tutor' in df_filtrado.columns:
            df_filtrado = df_filtrado[df_filtrado['Tutor'] == tutor_filtro]
        else:
            flash("Coluna 'Tutor' não encontrada nos dados.", 'warning')

    if status_filtro:
        # Garante que a coluna 'Status' existe antes de filtrar
        if 'Status' in df_filtrado.columns:
            df_filtrado = df_filtrado[df_filtrado['Status'] == status_filtro]
        else:
             flash("Coluna 'Status' não encontrada nos dados.", 'warning')


    tutores = sorted(list(df['Tutor'].unique())) if not df.empty and 'Tutor' in df.columns else carregar_lista(ABA_TUTORES, 'Tutor')
    # Status padrão na listagem
    status_list = sorted(list(df['Status'].unique())) if not df.empty and 'Status' in df.columns else ['Em Aberto', 'Assinada', 'Finalizada', 'ATENDIMENTO']
    
    df_filtrado = df_filtrado.sort_values(by='ID', ascending=False)
    registros = df_filtrado.to_dict('records')

    return render_template("index.html", 
                            registros=registros,
                            tutores=tutores,
                            status_list=status_list,
                            tutor_sel=tutor_filtro,
                            status_sel=status_filtro)


@app.route("/nova", methods=['GET'])
def nova():
    """Exibe o formulário de nova ocorrência."""
    
    professores = carregar_lista(ABA_PROFESSORES, 'Professor') 
    salas = carregar_lista(ABA_SALAS, 'Sala')
    alunos_json = "[]" 
    
    try:
        # Carregar a lista completa de alunos para o JavaScript (para preenchimento automático)
        client, spreadsheet = conectar_sheets()
        if spreadsheet:
            ws_alunos = spreadsheet.worksheet(ABA_ALUNOS)
            data_alunos = ws_alunos.get_all_records(head=1, default_blank='')
            df_alunos = pd.DataFrame(data_alunos)
            
            if all(col in df_alunos.columns for col in ['Sala', 'Aluno', 'Tutor']):
                # Garante que os valores são strings para JSON
                df_alunos['Sala'] = df_alunos['Sala'].astype(str)
                df_alunos['Aluno'] = df_alunos['Aluno'].astype(str)
                df_alunos['Tutor'] = df_alunos['Tutor'].astype(str)
                alunos_json = df_alunos[['Sala', 'Aluno', 'Tutor']].to_json(orient='records')
        
    except Exception as e:
        print(f"Erro ao carregar lista de alunos completa: {e}")
        
    return render_template("nova.html", 
                            professores=professores, 
                            salas=salas,
                            alunos_json=alunos_json)

@app.route("/api/alunos_sala/<sala>", methods=['GET'])
def api_alunos_sala(sala):
    """Retorna a lista de alunos e tutores para uma determinada sala via AJAX."""
    client, spreadsheet = conectar_sheets()
    if spreadsheet is None: return jsonify([])

    try:
        ws_alunos = spreadsheet.worksheet(ABA_ALUNOS)
        data_alunos = ws_alunos.get_all_records(head=1, default_blank='')
        df_alunos = pd.DataFrame(data_alunos)

        if not df_alunos.empty and 'Sala' in df_alunos.columns:
            df_filtrado = df_alunos[df_alunos['Sala'].astype(str) == sala]
            # Garante que as colunas 'Aluno' e 'Tutor' existem antes de retornar
            if 'Aluno' in df_filtrado.columns and 'Tutor' in df_filtrado.columns:
                alunos = df_filtrado[['Aluno', 'Tutor']].to_dict('records')
                return jsonify(alunos)
        
        return jsonify([])
        
    except gspread.exceptions.WorksheetNotFound:
        print(f"ATENÇÃO: Aba '{ABA_ALUNOS}' não encontrada.")
        return jsonify([]), 404
    except Exception as e:
        print(f"Erro na API de alunos: {e}")
        return jsonify([]), 500

@app.route("/salvar_edicao/<int:oid>", methods=['POST'])
def salvar_edicao(oid):
    try:
        client, spreadsheet = conectar_sheets()
        if spreadsheet is None:
            flash("Erro de conexão com a planilha!", 'danger')
            return redirect(url_for('editar', oid=oid))
        ws = spreadsheet.worksheet(ABA_OCORRENCIAS)

        # 1. Localiza a linha pelo ID
        # Busca todas as ocorrências e localiza o ID
        df_temp = carregar_dados()
        row_data = df_temp[df_temp['ID'] == oid].iloc[0] if not df_temp[df_temp['ID'] == oid].empty else None

        if row_data is None:
            flash(f"Ocorrência ID {oid} não encontrada.", 'danger')
            return redirect(url_for('editar', oid=oid))
        
        # O gspread.find() é melhor para localizar a linha real para atualização
        cell = ws.find(str(oid))
        if cell is None:
             flash(f"Ocorrência ID {oid} não encontrada no Sheets (find).", 'danger')
             return redirect(url_for('editar', oid=oid))
        row_index = cell.row


        # Mapeamento de coluna (necessário para atualizar pelo índice)
        header = ws.row_values(1)
        col_map = {col: i for i, col in enumerate(header) if col}


        # 2. Recebe os dados do formulário
        form_data = request.form
        updates = {}
        agora_iso = datetime.now(TZ_SAO).strftime('%Y-%m-%d %H:%M:%S') 

        # ATT
        att_texto = form_data.get('att_texto')
        if att_texto is not None and att_texto.strip() and row_data.get('FT') == 'SIM' and not row_data.get('ATT'):
            updates['ATT'] = att_texto
            updates['DT'] = agora_iso
            updates['FT'] = 'NÃO'

        # ATC
        atc_texto = form_data.get('atc_texto')
        if atc_texto is not None and atc_texto.strip() and row_data.get('FC') == 'SIM' and not row_data.get('ATC'):
            updates['ATC'] = atc_texto
            updates['DC'] = agora_iso
            updates['FC'] = 'NÃO'

        # ATG
        atg_texto = form_data.get('atg_texto')
        if atg_texto is not None and atg_texto.strip() and row_data.get('FG') == 'SIM' and not row_data.get('ATG'):
            updates['ATG'] = atg_texto
            updates['DG'] = agora_iso
            updates['FG'] = 'NÃO'

        # Atualiza descrição
        nova_desc = form_data.get('descricao')
        if nova_desc is not None and nova_desc.strip():
            updates['Descrição da Ocorrência'] = nova_desc

        # Atendimento Professor
        at_prof = form_data.get('at_professor')
        if at_prof is not None and at_prof.strip():
            updates['Atendimento Professor'] = at_prof

        # Lógica de Status: Se todos os FT/FC/FG forem 'NÃO', o status é 'FINALIZADA'.
        # Assume que os valores na linha_atual refletem o estado ANTERIOR, 
        # mas verifica o que será o novo estado após esta atualização.
        
        # Pega os valores atuais (ou os novos valores de 'updates')
        current_ft = updates.get('FT') or str(row_data.get('FT', 'SIM')).strip()
        current_fc = updates.get('FC') or str(row_data.get('FC', 'SIM')).strip()
        current_fg = updates.get('FG') or str(row_data.get('FG', 'SIM')).strip()
        
        # Define o status final
        if current_ft == 'SIM' or current_fc == 'SIM' or current_fg == 'SIM':
            updates['Status'] = 'ATENDIMENTO'
        else:
            updates['Status'] = 'FINALIZADA'
            # O status 'ASSINADA' é definido apenas via rota /gerar_pdf_aluno

        # 3. Enviar atualizações para o Sheets
        cells_to_update = []
        for col_name, value in updates.items():
            if col_name in col_map:
                col_index = col_map[col_name] + 1
                cells_to_update.append(gspread.Cell(row_index, col_index, value))

        if cells_to_update:
            ws.update_cells(cells_to_update)

        flash(f"Ocorrência ID {oid} atualizada com sucesso! Novo Status: {updates['Status']}", 'success')
        return redirect(url_for('editar', oid=oid))

    except Exception as e:
        print(f"Erro ao salvar edição: {e}")
        flash(f"Erro ao salvar edição: {e}", 'danger')
        return redirect(url_for('editar', oid=oid))

@app.route("/salvar", methods=['POST'])
def salvar():
    """Processa o formulário de nova ocorrência e salva os dados no Google Sheets."""
    try:
        client, spreadsheet = conectar_sheets()
        if spreadsheet is None:
            flash("Erro de conexão com a planilha!", 'danger')
            return redirect(url_for('nova'))
            
        ws_ocorrencias = spreadsheet.worksheet(ABA_OCORRENCIAS)

        # 1. Pega o próximo ID (busca a coluna A, ignorando o cabeçalho)
        last_id = 0
        try:
            # Pega todos os valores da primeira coluna (ID)
            all_ids = ws_ocorrencias.col_values(1)[1:] 
            # Filtra e converte para int para encontrar o máximo
            last_id = max([int(id_val) for id_val in all_ids if str(id_val).strip().isdigit()] or [0])
        except Exception as e:
            print(f"Aviso: Não foi possível obter o último ID, usando 0. Erro: {e}")
            last_id = 0
            
        novo_id = last_id + 1
        
        # 2. Pega os dados do formulário
        form_data = request.form
        
        # 3. Prepara a nova linha
        agora = datetime.now(TZ_SAO).strftime('%H:%M:%S')
        hoje = datetime.now(TZ_SAO).strftime('%Y-%m-%d')
        
        # O status inicial é "Em Aberto" na criação.
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
            'ATT': '', 
            'ATC': '',
            'ATG': '',
            # Se for solicitado, a flag é SIM. Se não for solicitado (checkbox desmarcado), NÃO.
            'FT': 'SIM' if form_data.get('req_ft') == 'on' else 'NÃO', 
            'FC': 'SIM' if form_data.get('req_fc') == 'on' else 'NÃO',
            'FG': 'SIM' if form_data.get('req_fg') == 'on' else 'NÃO',
            'DT': '',
            'DC': '',
            'DG': '',
            'Status': 'Em Aberto' 
        }
        
        # 4. Envia a linha para o Sheets
        row_to_insert = [nova_ocorrencia.get(col, '') for col in EXPECTED_COLUMNS]
        ws_ocorrencias.append_row(row_to_insert, value_input_option='USER_ENTERED')
        
        flash(f"Ocorrência ID {novo_id} salva com sucesso!", 'success')
        return redirect(url_for('index'))

    except Exception as e:
        print(f"Erro ao salvar ocorrência: {e}")
        flash(f"Erro ao salvar: {e}", 'danger')
        return redirect(url_for('nova'))

@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    ids = request.form.getlist("ocorrencias")
    sala = request.form.get("sala")
    aluno = request.form.get("aluno")

    try:
        client, spreadsheet = conectar_sheets()
        if spreadsheet is None:
            flash("Erro de conexão com a planilha!", "danger")
            # Redireciona de volta para onde a requisição veio, usando os parâmetros
            encoded_params = urlencode({'sala': sala, 'aluno': aluno})
            return redirect(url_for("relatorio_aluno") + '?' + encoded_params)

        ws = spreadsheet.worksheet(ABA_OCORRENCIAS)
        df = carregar_dados()

        # Localiza o cabeçalho
        header = ws.row_values(1)
        col_status = -1
        try:
            col_status = header.index("Status") + 1
        except ValueError:
            flash("Coluna 'Status' não encontrada na planilha. Não foi possível atualizar.", "danger")
            col_status = -1

        # Atualiza status no Sheets
        for oid_str in ids:
            try:
                oid = int(oid_str)
            except ValueError:
                continue # Pula IDs inválidos

            mask = df["ID"] == oid
            if mask.any() and col_status != -1:
                 # Localiza a célula para atualização
                cell = ws.find(str(oid))
                if cell:
                    ws.update_cell(cell.row, col_status, "ASSINADA")

        # Seleciona ocorrências e gera PDF
        selecionadas = df[df["ID"].astype(str).isin(ids)].to_dict("records")
        tutor = selecionadas[0].get("Tutor", "") if selecionadas else ""

        pdf_output = gerar_pdf_ocorrencias_aluno(aluno, sala, tutor, selecionadas)

        flash("PDF gerado e ocorrências atualizadas para ASSINADA!", "success")
        return send_file(
            pdf_output,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"Relatorio_{aluno}.pdf"
        )

    except Exception as e:
        print(f"Erro ao gerar PDF aluno: {e}")
        flash(f"Erro ao gerar PDF: {e}", "danger")
        encoded_params = urlencode({'sala': sala, 'aluno': aluno})
        return redirect(url_for("relatorio_aluno") + '?' + encoded_params)


# Rota que precisa ser implementada/referenciada (relatorio_aluno)
@app.route("/relatorio_aluno")
def relatorio_aluno():
    """Exibe o formulário de seleção de ocorrências para gerar PDF de aluno."""
    sala = request.args.get('sala', '')
    aluno = request.args.get('aluno', '')
    
    df = carregar_dados()

    # Filtra as ocorrências pelo aluno
    df_filtrado = df[(df['Aluno'].astype(str) == aluno) & (df['Sala'].astype(str) == sala)]
    
    ocorrencias_aluno = df_filtrado.sort_values(by='ID', ascending=False).to_dict('records')
    
    # Carrega listas para filtros (opcionalmente)
    salas_list = carregar_lista(ABA_SALAS, 'Sala')
    
    alunos_list = []
    if sala:
        # Carrega a lista de alunos da sala (função auxiliar da API)
        client, spreadsheet = conectar_sheets()
        if spreadsheet:
            try:
                ws_alunos = spreadsheet.worksheet(ABA_ALUNOS)
                data_alunos = ws_alunos.get_all_records(head=1, default_blank='')
                df_alunos = pd.DataFrame(data_alunos)
                df_filtrado_alunos = df_alunos[df_alunos['Sala'].astype(str) == sala]
                alunos_list = sorted(list(df_filtrado_alunos['Aluno'].unique()))
            except Exception:
                # Se der erro, mantém lista vazia
                pass


    return render_template("relatorio_aluno.html",
                           salas=salas_list,
                           alunos_list=alunos_list,
                           sala_sel=sala,
                           aluno_sel=aluno,
                           ocorrencias=ocorrencias_aluno)


@app.route("/detalhes/<int:oid>", methods=['GET'])
def detalhes(oid):
    """Exibe os detalhes de uma ocorrência específica."""
    df = carregar_dados()
    ocorrencia = df[df['ID'] == oid].iloc[0].to_dict() if not df[df['ID'] == oid].empty else None

    if ocorrencia is None:
        flash("Ocorrência não encontrada.", 'warning')
        return redirect(url_for('index'))

    tutores = carregar_lista(ABA_TUTORES, 'Tutor')
    return render_template("detalhes.html", ocorrencia=ocorrencia, tutores=tutores)


@app.route("/detalhes/<int:oid>/pdf", methods=['GET'])
def pdf_ocorrencia(oid):
    """Gera o PDF de uma única ocorrência."""
    df = carregar_dados()
    ocorrencia = df[df['ID'] == oid].iloc[0].to_dict() if not df[df['ID'] == oid].empty else None

    if ocorrencia is None:
        return "Ocorrência não encontrada", 404
    
    pdf_output = gerar_pdf_ocorrencia(ocorrencia)
    
    return send_file(
        pdf_output, 
        mimetype='application/pdf', 
        as_attachment=True, 
        download_name=f'SGCE_Ocorrencia_{oid}.pdf'
    )

@app.route("/editar/<int:oid>", methods=['GET'])
def editar(oid):
    """Permite visualização e edição (POST) de ocorrência."""
    try:
        df = carregar_dados()
        ocorrencia = df[df['ID'] == oid].iloc[0].to_dict() if not df[df['ID'] == oid].empty else None
    except Exception as e:
        flash(f"Erro ao carregar dados: {e}", 'danger')
        return redirect(url_for('index'))

    if ocorrencia is None:
        flash("Ocorrência não encontrada.", 'warning')
        return redirect(url_for('index'))

    # Permissões de edição por campo (se a flag é SIM e o campo ATT/ATC/ATG está vazio)
    permissoes = {
        "att": ocorrencia.get('FT') == 'SIM' and not ocorrencia.get('ATT'),
        "atc": ocorrencia.get('FC') == 'SIM' and not ocorrencia.get('ATC'),
        "atg": ocorrencia.get('FG') == 'SIM' and not ocorrencia.get('ATG')
    }

    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes)

# A rota POST para /editar/<int:oid> foi consolidada na rota /salvar_edicao/<int:oid>

if __name__ == '__main__':
    # Apenas para teste local. No Render, use gunicorn ou equivalente.
    app.run(debug=True)
