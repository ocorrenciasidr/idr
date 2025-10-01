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
import os
import json
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

# Imports para Geração de PDF e Gráficos (necessita de `fpdf` e `matplotlib`)
from fpdf import FPDF
# Você precisará instalar o matplotlib: pip install matplotlib fpdf
try:
    import matplotlib.pyplot as plt
    # Usado para formatar datas, mas pode ser removido se não for essencial e gerar erro
    # import matplotlib.dates as mdates 
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

@app.route("/ocorrencias")
def index_ocorrencias():
    # Configuração do Google Sheets
    scope = ["https://spreadsheets.google.com/feeds", 
             "https://www.googleapis.com/auth/drive"]
    
    creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
    client = gspread.authorize(creds)

    # Abre a planilha pelo URL ou ID
    planilha = client.open_by_url("COLE_AQUI_O_LINK_DA_PLANILHA")
    sheet = planilha.worksheet("Dados")  # nome da aba

    # Carrega todos os dados em um DataFrame
    dados_lista = sheet.get_all_records()  # pega tudo como lista de dicionários
    df = pd.DataFrame(dados_lista)

    # Garante que todas as colunas esperadas existem
    expected_columns = [
        "ID","DCO","HCO","Professor","Sala","Aluno","Tutor",
        "Descrição da Ocorrência","Atendimento Professor","ATT","ATC",
        "ATG","FT","FC","FG","DT","DC","DG","Status"
    ]
    for col in expected_columns:
        if col not in df.columns:
            df[col] = None

    # Ordena pelo ID apenas se houver dados
    if not df.empty:
        df = df.sort_values(by="ID", ascending=False)

    dados = df.to_dict(orient="records")
    return render_template("index.html", dados=dados)

# -------------------- Funções Auxiliares (Conexão e Carga) --------------------

def conectar_sheets():
    try:
        creds_json = os.getenv("GOOGLE_CREDS")
        creds_dict = json.loads(creds_json)

        # Corrige a formatação da chave privada
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")

        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )

        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key("SEU_SPREADSHEET_ID")

        return client, spreadsheet

    except Exception as e:
        print("Erro ao conectar com Google Sheets:", repr(e))  # 🔹 usar repr para ver detalhes
        return None, None
  
# ---------------------------
# Função para carregar dados
# ---------------------------
def carregar_dados():
    client, spreadsheet = conectar_sheets()
    if not client or not spreadsheet:
        raise Exception("Falha ao conectar com Google Sheets")

    try:
        worksheet = spreadsheet.sheet1   # primeira aba
        dados = worksheet.get_all_records()
        df = pd.DataFrame(dados)
        return df
    except Exception as e:
        print("Erro ao carregar dados da planilha:", e)
        return pd.DataFrame()  # retorna vazio se falhar
        
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


@app.route("/index")
def index():
    try:
        df = carregar_dados()
        return render_template("index.html", tabela=df.to_dict(orient="records"))
    except Exception as e:
        print("Erro na rota /index:", e)
        return "Erro ao carregar dados da planilha.", 500
    
    tutor_filtro = request.args.get('tutor', '')
    status_filtro = request.args.get('status', '')

    df_filtrado = df.copy()
    
    if tutor_filtro:
        df_filtrado = df_filtrado[df_filtrado['Tutor'] == tutor_filtro]
    if status_filtro:
        df_filtrado = df_filtrado[df_filtrado['Status'] == status_filtro]

    tutores = sorted(list(df['Tutor'].unique())) if not df.empty and 'Tutor' in df.columns else carregar_lista(ABA_TUTORES, 'Tutor')
    # Status padrão na listagem
    status_list = sorted(list(df['Status'].unique())) if not df.empty and 'Status' in df.columns else ['Em Aberto', 'Assinada', 'Finalizada']
    
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
            df_filtrado = df_alunos[df_alunos['Sala'] == sala]
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
        cell = ws.find(str(oid))
        if cell is None:
            flash(f"Ocorrência ID {oid} não encontrada.", 'danger')
            return redirect(url_for('editar', oid=oid))
        row_index = cell.row

        # 2. Recebe os dados do formulário
        form_data = request.form
        updates = {}
        hoje = datetime.now().strftime('%Y-%m-%d')  # só data YYYY-MM-DD

        # ATT
        att_texto = form_data.get('att_texto')
        if att_texto is not None and att_texto.strip():
            updates['ATT'] = att_texto
            updates['DT'] = hoje
            updates['FT'] = 'NÃO'

        # ATC
        atc_texto = form_data.get('atc_texto')
        if atc_texto is not None and atc_texto.strip():
            updates['ATC'] = atc_texto
            updates['DC'] = hoje
            updates['FC'] = 'NÃO'

        # ATG
        atg_texto = form_data.get('atg_texto')
        if atg_texto is not None and atg_texto.strip():
            updates['ATG'] = atg_texto
            updates['DG'] = hoje
            updates['FG'] = 'NÃO'

        # Atualiza descrição
        nova_desc = form_data.get('descricao')
        if nova_desc is not None:
            updates['Descrição da Ocorrência'] = nova_desc

        # Atendimento Professor
        at_prof = form_data.get('at_professor')
        if at_prof is not None:
            updates['Atendimento Professor'] = at_prof

        # Pega a linha atual para decidir status
        linha_atual = ws.row_values(row_index)
        col_map = {col: i for i, col in enumerate(EXPECTED_COLUMNS)}

        ft_val = updates.get('FT', linha_atual[col_map['FT']])
        fc_val = updates.get('FC', linha_atual[col_map['FC']])
        fg_val = updates.get('FG', linha_atual[col_map['FG']])

        # Atualiza status
        if ft_val == 'SIM' or fc_val == 'SIM' or fg_val == 'SIM':
            updates['Status'] = 'ATENDIMENTO'
        else:
            updates['Status'] = 'FINALIZADA'

        # Enviar atualizações para o Sheets
        cells_to_update = []
        for col_name, value in updates.items():
            if col_name in col_map:
                col_index = col_map[col_name] + 1
                cells_to_update.append(gspread.Cell(row_index, col_index, value))

        if cells_to_update:
            ws.update_cells(cells_to_update)

        flash(f"Ocorrência ID {oid} atualizada com sucesso!", 'success')
        return redirect(url_for('editar', oid=oid))

    except Exception as e:
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
            last_id = max([int(id_val) for id_val in all_ids if id_val.isdigit()] or [0])
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
        # Os novos status (ATENDIMENTO, ASSINADA, FINALIZADA) são usados no fluxo de EDIÇÃO.
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
        ws_ocorrencias.append_row(row_to_insert)
        
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
            return redirect(url_for("relatorio_aluno", sala=sala, aluno=aluno))

        ws = spreadsheet.worksheet(ABA_OCORRENCIAS)
        df = carregar_dados()

        # Atualiza status no DataFrame
        for oid in ids:
            mask = df["ID"].astype(str) == str(oid)
            if mask.any():
                df.loc[mask, "Status"] = "ASSINADA"

        # Atualiza no Google Sheets
        for oid in ids:
            cell = ws.find(str(oid))
            if cell:
                col_status = EXPECTED_COLUMNS.index("Status") + 1
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
        return redirect(url_for("relatorio_aluno", sala=sala, aluno=aluno))



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

@app.route("/editar/<int:oid>", methods=['GET', 'POST'])
def editar(oid):
    """Permite edição de ocorrência com lógica de atendimento por FT/FC/FG e status automático."""
    try:
        df = carregar_dados()
        ocorrencia = df[df['ID'] == oid].iloc[0].to_dict() if not df[df['ID'] == oid].empty else None
    except Exception as e:
        flash(f"Erro ao carregar dados: {e}", 'danger')
        return redirect(url_for('index'))

    if ocorrencia is None:
        flash("Ocorrência não encontrada.", 'warning')
        return redirect(url_for('index'))

    # Permissões de edição por campo (evento independente)
    permissoes = {
        "att": ocorrencia.get('FT') == 'SIM' and not ocorrencia.get('ATT'),
        "atc": ocorrencia.get('FC') == 'SIM' and not ocorrencia.get('ATC'),
        "atg": ocorrencia.get('FG') == 'SIM' and not ocorrencia.get('ATG')
    }

    agora = datetime.now(TZ_SAO).strftime('%Y-%m-%d %H:%M:%S')

    if request.method == 'POST':
        try:
            client, spreadsheet = conectar_sheets()
            if spreadsheet is None:
                flash("Erro de conexão com a planilha!", 'danger')
                return redirect(url_for('editar', oid=oid))

            ws_ocorrencias = spreadsheet.worksheet(ABA_OCORRENCIAS)
            cell = ws_ocorrencias.find(str(oid))
            if cell is None:
                raise gspread.exceptions.CellNotFound(f"ID {oid} não encontrado.")
            row_index = cell.row

            form_data = request.form
            updates = {}

            # Atualiza ATT se habilitado
            if permissoes['att']:
                att_texto = form_data.get('att_texto')
                if att_texto and att_texto.strip():
                    updates['ATT'] = att_texto
                    updates['DT'] = agora
                    updates['FT'] = 'NÃO'

            # Atualiza ATC se habilitado
            if permissoes['atc']:
                atc_texto = form_data.get('atc_texto')
                if atc_texto and atc_texto.strip():
                    updates['ATC'] = atc_texto
                    updates['DC'] = agora
                    updates['FC'] = 'NÃO'

            # Atualiza ATG se habilitado
            if permissoes['atg']:
                atg_texto = form_data.get('atg_texto')
                if atg_texto and atg_texto.strip():
                    updates['ATG'] = atg_texto
                    updates['DG'] = agora
                    updates['FG'] = 'NÃO'

            # Atualiza Status, Descrição e Atendimento Professor normalmente
            novo_status = form_data.get('novo_status')
            if novo_status and novo_status != ocorrencia.get('Status'):
                updates['Status'] = novo_status

            nova_desc = form_data.get('descricao')
            if nova_desc is not None and nova_desc != ocorrencia.get('Descrição da Ocorrência'):
                updates['Descrição da Ocorrência'] = nova_desc

            at_professor = form_data.get('at_professor')
            if at_professor is not None and at_professor != ocorrencia.get('Atendimento Professor'):
                updates['Atendimento Professor'] = at_professor

            # Atualiza status automaticamente de acordo com FT/FC/FG
            ft = updates.get('FT', ocorrencia.get('FT'))
            fc = updates.get('FC', ocorrencia.get('FC'))
            fg = updates.get('FG', ocorrencia.get('FG'))

            if ft == 'SIM' or fc == 'SIM' or fg == 'SIM':
                updates['Status'] = 'ATENDIMENTO'
            elif ft == 'NÃO' and fc == 'NÃO' and fg == 'NÃO':
                updates['Status'] = 'FINALIZADA'

            # Envia atualizações para o Sheets
            col_map = {col: i + 1 for i, col in enumerate(EXPECTED_COLUMNS)}
            cells_to_update = [gspread.Cell(row_index, col_map[col], val)
                               for col, val in updates.items() if col in col_map]

            if cells_to_update:
                ws_ocorrencias.update_cells(cells_to_update)
                flash(f"Ocorrência ID {oid} atualizada com sucesso!", 'success')
            else:
                flash("Nenhuma alteração detectada.", 'info')

            return redirect(url_for('editar', oid=oid))

        except gspread.exceptions.CellNotFound:
            flash("Erro: ID da ocorrência não encontrado na planilha.", 'danger')
        except Exception as e:
            print(f"Erro ao editar ocorrência: {e}")
            flash(f"Erro ao editar: {e}", 'danger')
            return redirect(url_for('editar', oid=oid))

    # GET: renderiza template com permissões por campo
    return render_template(
        'editar.html',
        ocorrencia=ocorrencia,
        permissoes=permissoes
    )


@app.route('/relatorio_geral')
def relatorio_geral():
    # lógica para gerar estatísticas gerais
    return render_template('relatorio_geral.html')

@app.route('/relatorio_tutor')
def relatorio_tutor():
    # lógica para gerar estatísticas gerais
    return render_template('relatorio_tutor.html')

@app.route('/relatorio_tutoraluno')
def relatorio_tutoraluno():
    # lógica para gerar estatísticas gerais
    return render_template('relatorio_tutoraluno.html')

    # --- PERMISSÕES (GET) ---
    is_lapis = (papel == 'lapis')

    permissoes = {
        'edicao': is_lapis,
        'descricao': is_lapis,
        'professor': is_lapis,
        # ATT / ATC / ATG vêm travados; liberação acontece pelo link "SIM"
        'tutor': False,
        'coord': False,
        'gestao': False,
    }
    # -------------------------

    tutores = carregar_lista(ABA_TUTORES, 'Tutor')
    status_list = ['Em Aberto', 'ATENDIMENTO', 'ASSINADA', 'FINALIZADA']

    return render_template(
        "editar.html",
        ocorrencia=ocorrencia,
        tutores=tutores,
        papel=papel,
        status_list=status_list,
        permissoes=permissoes
    )

@app.route("/relatorio_inicial")
def relatorio_inicial():
    """Exibe a lista de tutores para seleção de relatório."""
    df = carregar_dados()
    tutores = sorted(list(df['Tutor'].unique())) if not df.empty and 'Tutor' in df.columns else carregar_lista(ABA_TUTORES, 'Tutor')
    
    return render_template("relatorio_inicial.html", tutores=tutores)

@app.route("/relatorio_tutor/<tutor>", methods=['GET'])
def relatorio_tutor_pdf():
    """Gera um relatório detalhado do desempenho de um tutor."""
    df = carregar_dados()
    
    if df.empty or 'Tutor' not in df.columns:
        flash("Dados insuficientes para gerar relatório.", 'warning')
        return redirect(url_for('relatorio_inicial'))
        
    df_filtrado = df[df['Tutor'] == tutor].copy()
    
    if df_filtrado.empty:
        flash(f"Nenhuma ocorrência encontrada para o tutor {tutor}.", 'info')
        return redirect(url_for('relatorio_inicial'))

    # Função auxiliar para calcular o status do atendimento do tutor
    def calcular_status_tutor(row):
        # DCO é a data de Criação da Ocorrência
        try:
            # Tenta converter para datetime e localizar o fuso horário
            dco = pd.to_datetime(row['DCO'], errors='coerce').tz_localize(TZ_SAO)
            if pd.isna(dco):
                return 'aberto'
        except Exception:
            return 'aberto' # Se a data for inválida, considera como aberto (melhor que quebrar)

        # Requisitado Follow-up do Tutor?
        if row['FT'] != 'SIM':
            return 'nao_req' # Não solicitado (não contabiliza como prazo/fora/nao)
            
        # 1. Verificar se o atendimento já foi realizado (DT preenchida)
        if row['DT']:
            # Se já está fechado, verifica se a resposta foi dada em até 2 dias
            try:
                dt_str = str(row['DT']).split(' ')[0] # Pega só a data se houver hora
                dt = pd.to_datetime(dt_str, errors='coerce').tz_localize(TZ_SAO)
                # Se a conversão falhar, tenta usar a data de criação + 3 dias
                if pd.isna(dt):
                    dt = dco + timedelta(days=3) 

            except Exception:
                # Se DT não é uma data válida, trata como 'fora' se for antigo
                dt = dco + timedelta(days=3)

            # Calcula a diferença de dias
            # Nota: Isso compara as datas sem o componente de hora se DCO for YYYY-MM-DD
            if (dt - dco).days <= 2:
                return 'prazo'
            else:
                return 'fora'
        else:
            # 2. Se não está fechado, verifica se passou o prazo de 2 dias (contando até hoje)
            # Compara apenas a data
            if (datetime.now(TZ_SAO).date() - dco.date()).days > 2:
                return 'nao' # Vencida e Não Atendida
            else:
                return 'aberto' # Ainda está no prazo para ser respondida

    df_filtrado['StatusTutor'] = df_filtrado.apply(calcular_status_tutor, axis=1)
    
    # Traduz o status para exibição
    status_map = {
        'prazo': 'Atendido no Prazo (<= 2 dias)',
        'fora': 'Atendido Fora do Prazo (> 2 dias)',
        'nao': 'Não Atendido (Vencido)',
        'aberto': 'Em Aberto (No Prazo)',
        'nao_req': 'Não Requisitado'
    }
    df_filtrado['StatusTutorTexto'] = df_filtrado['StatusTutor'].map(status_map)

    
    # 3. Agrupa e contabiliza (apenas os que foram requisitados)
    relatorio = df_filtrado[df_filtrado['StatusTutor'] != 'nao_req'].groupby('Tutor')['StatusTutor'].value_counts().unstack(fill_value=0)
    
    relatorio_final = {}
    if tutor in relatorio.index:
        counts = relatorio.loc[tutor]
        total_requisitado = counts.sum()
        relatorio_final = {
            'total': total_requisitado,
            'prazo': counts.get('prazo', 0),
            'fora': counts.get('fora', 0),
            'nao': counts.get('nao', 0),
            'aberto': counts.get('aberto', 0)
        }
    else:
        # Se não houver ocorrências requisitadas para o tutor, inicializa com zero.
        relatorio_final = {'total': 0, 'prazo': 0, 'fora': 0, 'nao': 0, 'aberto': 0}

    # Gera o gráfico (se Matplotlib estiver disponível)
    grafico_b64 = None
    if HAS_MATPLOTLIB and relatorio_final['total'] > 0:
        img_buffer = gerar_grafico_barras(relatorio_final, tutor)
        grafico_b64 = base64.b64encode(img_buffer.read()).decode('utf-8')
    
    registros_relatorio = df_filtrado[df_filtrado['StatusTutor'] != 'nao_req'].sort_values(by='DCO', ascending=False).to_dict('records')
    
    return render_template("relatorio.html",
                            tutor=tutor,
                            relatorio=relatorio_final,
                            registros=registros_relatorio,
                            grafico_b64=grafico_b64,
                            has_matplotlib=HAS_MATPLOTLIB)


@app.route("/relatorio_tutor/<tutor>/pdf", methods=['GET'])
def download_relatorio_tutor(tutor):
    """Gera e envia o PDF do relatório do Tutor para download."""
    df = carregar_dados()
    
    if df.empty or 'Tutor' not in df.columns:
        return "Dados insuficientes para gerar relatório.", 404
        
    df_filtrado = df[df['Tutor'] == tutor].copy()
    
    if df_filtrado.empty:
        return f"Nenhuma ocorrência encontrada para o tutor {tutor}.", 404

    # Recalcula a lógica do status (duplicado, mas necessário para consistência no PDF)
    def calcular_status_tutor_pdf(row):
        # DCO é a data de Criação da Ocorrência
        try:
            dco = pd.to_datetime(row['DCO'], errors='coerce').tz_localize(TZ_SAO)
            if pd.isna(dco):
                return 'aberto'
        except Exception:
            return 'aberto'

        if row['FT'] != 'SIM':
            return 'nao_req'
            
        if row['DT']:
            try:
                dt_str = str(row['DT']).split(' ')[0] # Pega só a data se houver hora
                dt = pd.to_datetime(dt_str, errors='coerce').tz_localize(TZ_SAO)
                if pd.isna(dt):
                    dt = dco + timedelta(days=3) # Fallback

            except Exception:
                dt = dco + timedelta(days=3) # Fallback

            if (dt - dco).days <= 2:
                return 'prazo'
            else:
                return 'fora'
        else:
            if (datetime.now(TZ_SAO).date() - dco.date()).days > 2:
                return 'nao'
            else:
                return 'aberto'

    df_filtrado['StatusTutor'] = df_filtrado.apply(calcular_status_tutor_pdf, axis=1)
    
    # Traduz o status para exibição no PDF
    status_map = {
        'prazo': 'Atendido no Prazo',
        'fora': 'Atendido Fora do Prazo',
        'nao': 'Não Atendido (Vencido)',
        'aberto': 'Em Aberto (No Prazo)',
        'nao_req': 'Não Requisitado'
    }
    df_filtrado['StatusTutorTexto'] = df_filtrado['StatusTutor'].map(status_map)
    
    # 3. Agrupa e contabiliza (apenas os que foram requisitados)
    relatorio = df_filtrado[df_filtrado['StatusTutor'] != 'nao_req'].groupby('Tutor')['StatusTutor'].value_counts().unstack(fill_value=0)
    
    relatorio_final = {}
    if tutor in relatorio.index:
        counts = relatorio.loc[tutor]
        total_requisitado = counts.sum()
        relatorio_final = {
            'total': total_requisitado,
            'prazo': counts.get('prazo', 0),
            'fora': counts.get('fora', 0),
            'nao': counts.get('nao', 0),
            'aberto': counts.get('aberto', 0)
        }
    else:
        relatorio_final = {'total': 0, 'prazo': 0, 'fora': 0, 'nao': 0, 'aberto': 0}

    # Gera o gráfico para o PDF
    img_buffer = None
    if HAS_MATPLOTLIB and relatorio_final['total'] > 0:
        img_buffer = gerar_grafico_barras(relatorio_final, tutor)
        
    registros_relatorio = df_filtrado[df_filtrado['StatusTutor'] != 'nao_req'].sort_values(by='DCO', ascending=False)
    
    # Gera o PDF
    pdf_output = gerar_pdf_tutor(relatorio_final, tutor, registros_relatorio, img_buffer)
    
    return send_file(
        pdf_output, 
        mimetype='application/pdf', 
        as_attachment=True, 
        download_name=f'SGCE_Relatorio_{tutor}.pdf'
    )


# ---------------------------
# Rodar o Flask
# ---------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)

    # Configuração de fallback para desenvolvimento local
    if 'SECRET_KEY' not in os.environ:
        print("AVISO: Usando SECRET_KEY de fallback.")
    if 'SHEET_ID' not in os.environ:
        print("AVISO: Usando SHEET_ID de fallback.")
        
    app.run(debug=True)





