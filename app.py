import os
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
import base64
from urllib.parse import urlencode

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash, abort

# =======================================================
# Imports Necessários
# =======================================================
import gspread
from google.oauth2.service_account import Credentials # JÁ ESTAVA CORRETO

# =======================================================
# Função de Conexão (CORRIGIDA)
# =======================================================
def conectar_sheets():
    """
    Autentica no Google Sheets usando credenciais JSON
    armazenadas na variável de ambiente.
    """
    try:
        # 1. Obter JSON da Variável de Ambiente
        creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
        if not creds_json:
            # Imprime o erro no log e retorna
            print("ERRO: Variável de ambiente GOOGLE_SHEETS_CREDENTIALS não configurada.")
            return None, None # Retorna client e spreadsheet como None

        # 2. Converter String JSON em Dicionário Python
        creds_dict = json.loads(creds_json)
        
        # 3. Definir Scopes
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets', 
            'https://www.googleapis.com/auth/drive'
        ]

        # 4. Criar Objeto de Credenciais (CORRIGIDO PARA O NOME DA CLASSE IMPORTADA)
        creds = Credentials.from_service_account_info(
            creds_dict, 
            scopes=scopes
        )
        
        # 5. Autorizar gspread
        gc = gspread.authorize(creds)

        # 6. Abrir a Planilha
        SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1Jyle_LCRCKQfbDShoIj-9MPNIkVSkYxWaCwQrhmxSoE') 
        spreadsheet = gc.open_by_key(SHEET_ID)
        
        # Retorna o cliente e a planilha (necessário para as funções auxiliares)
        return gc, spreadsheet 
        
    except json.JSONDecodeError:
        print("ERRO: O conteúdo da variável GOOGLE_SHEETS_CREDENTIALS não é um JSON válido.")
        return None, None
    except Exception as e:
        print(f"Erro ao conectar com Google Sheets: {e}")
        return None, None
        
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


# ROTA /ocorrencias REMOVIDA. A rota /index já faz o trabalho de listar os dados.


# -------------------- Funções Auxiliares (Conexão e Carga) --------------------

# SEGUNDA DEFINIÇÃO DE conectar_sheets() E O CÓDIGO DE AUTENTICAÇÃO BASEADO EM ARQUIVO FORAM REMOVIDOS.

def carregar_dados():
    """Carrega os dados da aba 'Dados' no Sheets para um DataFrame do Pandas."""
    client, spreadsheet = conectar_sheets() # Usa a função corrigida
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
    """Lista todas as ocorrências com filtros."""
    df = carregar_dados()
    
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

        # Verifica se linha_atual é grande o suficiente para evitar IndexError
        if len(linha_atual) > col_map['FT']:
            ft_val = updates.get('FT', linha_atual[col_map['FT']])
        else:
            ft_val = updates.get('FT', 'NÃO')
        
        if len(linha_atual) > col_map['FC']:
            fc_val = updates.get('FC', linha_atual[col_map['FC']])
        else:
            fc_val = updates.get('FC', 'NÃO')

        if len(linha_atual) > col_map['FG']:
            fg_val = updates.get('FG', linha_atual[col_map['FG']])
        else:
            fg_val = updates.get('FG', 'NÃO')


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
    
    return render_template("editar.html", ocorrencia=ocorrencia, expected_columns=EXPECTED_COLUMNS)

@app.route("/relatorio_aluno", methods=['GET', 'POST'])
def relatorio_aluno():
    """Gera o relatório de ocorrências de um aluno, permitindo seleção para geração de PDF."""
    
    # Se a conexão falhar, as listas virão vazias e o df de ocorrências será vazio.
    salas = carregar_lista(ABA_SALAS, 'Sala')
    
    # Coleta filtros
    sala_filtro = request.args.get('sala')
    aluno_filtro = request.args.get('aluno')
    
    df = carregar_dados()
    df_filtrado = pd.DataFrame(columns=EXPECTED_COLUMNS)
    aluno_info = {'aluno': '', 'tutor': '', 'sala': ''}
    
    if sala_filtro and aluno_filtro and not df.empty:
        # Garante que as colunas existem antes de filtrar
        if 'Sala' in df.columns and 'Aluno' in df.columns:
            # Filtra por sala e aluno
            df_filtrado = df[(df['Sala'] == sala_filtro) & (df['Aluno'] == aluno_filtro)]
            df_filtrado = df_filtrado.sort_values(by='DCO', ascending=False)
            
            # Pega as informações de Tutor e Sala
            if not df_filtrado.empty:
                aluno_info = {
                    'aluno': aluno_filtro,
                    'tutor': df_filtrado['Tutor'].iloc[0],
                    'sala': sala_filtro
                }
            elif sala_filtro:
                # Se não houver ocorrências, tenta pegar o tutor da lista de alunos
                client, spreadsheet = conectar_sheets()
                if spreadsheet:
                    try:
                        ws_alunos = spreadsheet.worksheet(ABA_ALUNOS)
                        data_alunos = ws_alunos.get_all_records(head=1, default_blank='')
                        df_alunos = pd.DataFrame(data_alunos)
                        aluno_row = df_alunos[(df_alunos['Sala'] == sala_filtro) & (df_alunos['Aluno'] == aluno_filtro)]
                        if not aluno_row.empty and 'Tutor' in aluno_row.columns:
                            aluno_info['tutor'] = aluno_row['Tutor'].iloc[0]
                    except Exception as e:
                        print(f"Aviso: Erro ao buscar info de tutor para aluno sem ocorrência: {e}")
                        
    # Carrega a lista de alunos para o dropdown
    alunos = []
    if sala_filtro:
        client, spreadsheet = conectar_sheets()
        if spreadsheet:
            try:
                ws_alunos = spreadsheet.worksheet(ABA_ALUNOS)
                data_alunos = ws_alunos.get_all_records(head=1, default_blank='')
                df_alunos = pd.DataFrame(data_alunos)
                if not df_alunos.empty and 'Sala' in df_alunos.columns and 'Aluno' in df_alunos.columns:
                    alunos = sorted(list(df_alunos[df_alunos['Sala'] == sala_filtro]['Aluno'].unique()))
            except Exception as e:
                print(f"Aviso: Erro ao carregar lista de alunos: {e}")

    
    registros = df_filtrado.to_dict('records')

    return render_template("relatorio_aluno.html",
                           salas=salas,
                           alunos=alunos,
                           registros=registros,
                           aluno_info=aluno_info,
                           sala_sel=sala_filtro,
                           aluno_sel=aluno_filtro)

@app.route("/relatorio_tutor", methods=['GET'])
def relatorio_tutor():
    """Gera o relatório de desempenho de um tutor."""
    
    tutor_filtro = request.args.get('tutor', '')
    
    # Tenta carregar a lista de tutores mesmo se a conexão falhar
    tutores = carregar_lista(ABA_TUTORES, 'Tutor')
    
    relatorio_data = {}
    df_registros = pd.DataFrame(columns=EXPECTED_COLUMNS)
    
    if tutor_filtro:
        df = carregar_dados()
        df_tutor = df[df['Tutor'] == tutor_filtro].copy()
        
        if not df_tutor.empty:
            
            # Processamento de datas e status
            data_limite = datetime.now() - timedelta(days=2)
            
            # Adiciona colunas auxiliares para o PDF
            df_tutor['DataCriacao'] = pd.to_datetime(df_tutor['DCO'], errors='coerce')
            df_tutor['DataAtendimento'] = pd.to_datetime(df_tutor['DT'], errors='coerce')
            
            df_tutor['StatusTutor'] = 'nao' # Inicialmente 'Não Atendida'
            df_tutor['StatusTutorTexto'] = 'Não Atendida (Vencida)'
            
            # 1. Atendidas
            atendidas = ~df_tutor['DataAtendimento'].isna()
            
            # 2. No Prazo (Atendida e DT - DCO <= 2 dias)
            df_tutor.loc[atendidas, 'TempoAtendimento'] = (df_tutor['DataAtendimento'] - df_tutor['DataCriacao']).dt.days
            
            prazo_mask = atendidas & (df_tutor['TempoAtendimento'] <= 2)
            df_tutor.loc[prazo_mask, 'StatusTutor'] = 'prazo'
            df_tutor.loc[prazo_mask, 'StatusTutorTexto'] = 'Atendida no Prazo (<= 2 dias)'
            
            # 3. Fora do Prazo (Atendida e DT - DCO > 2 dias)
            fora_mask = atendidas & (df_tutor['TempoAtendimento'] > 2)
            df_tutor.loc[fora_mask, 'StatusTutor'] = 'fora'
            df_tutor.loc[fora_mask, 'StatusTutorTexto'] = 'Atendida Fora do Prazo (> 2 dias)'
            
            # 4. Não Atendidas (Original 'nao' - Se não foi atendida e a data de criação + 2 dias já passou)
            # Consideramos 'nao' se não foi atendida E AINDA está em 'Em Aberto' ou 'ATENDIMENTO'
            # E a data limite já passou.
            
            df_registros = df_tutor.sort_values(by='DCO', ascending=False)
            
            relatorio_data = {
                'total': len(df_tutor),
                'prazo': len(df_tutor[df_tutor['StatusTutor'] == 'prazo']),
                'fora': len(df_tutor[df_tutor['StatusTutor'] == 'fora']),
                # Contabiliza como "Não Atendida" se o status for 'nao' (o default)
                'nao': len(df_tutor[df_tutor['StatusTutor'] == 'nao'])
            }
            
    return render_template("relatorio_tutor.html",
                           tutores=tutores,
                           tutor_sel=tutor_filtro,
                           relatorio_data=relatorio_data,
                           registros=df_registros.to_dict('records'))

@app.route("/gerar_pdf_tutor", methods=['POST'])
def gerar_pdf_tutor_action():
    """Rota que gera e envia o PDF de desempenho do tutor."""
    nome_tutor = request.form.get('tutor', '')
    
    if not nome_tutor:
        flash("Tutor não especificado.", 'danger')
        return redirect(url_for('relatorio_tutor'))

    # Re-executa a lógica do relatório
    df = carregar_dados()
    df_tutor = df[df['Tutor'] == nome_tutor].copy()
    
    relatorio_data = {}
    df_registros = pd.DataFrame(columns=EXPECTED_COLUMNS)
    img_buffer = None
    
    if not df_tutor.empty:
        # Lógica de processamento de status (repetindo a lógica do relatorio_tutor)
        df_tutor['DataCriacao'] = pd.to_datetime(df_tutor['DCO'], errors='coerce')
        df_tutor['DataAtendimento'] = pd.to_datetime(df_tutor['DT'], errors='coerce')
        df_tutor['StatusTutor'] = 'nao' 
        df_tutor['StatusTutorTexto'] = 'Não Atendida (Vencida)'
        
        atendidas = ~df_tutor['DataAtendimento'].isna()
        
        df_tutor.loc[atendidas, 'TempoAtendimento'] = (df_tutor['DataAtendimento'] - df_tutor['DataCriacao']).dt.days
        
        prazo_mask = atendidas & (df_tutor['TempoAtendimento'] <= 2)
        df_tutor.loc[prazo_mask, 'StatusTutor'] = 'prazo'
        df_tutor.loc[prazo_mask, 'StatusTutorTexto'] = 'Atendida no Prazo (<= 2 dias)'
        
        fora_mask = atendidas & (df_tutor['TempoAtendimento'] > 2)
        df_tutor.loc[fora_mask, 'StatusTutor'] = 'fora'
        df_tutor.loc[fora_mask, 'StatusTutorTexto'] = 'Atendida Fora do Prazo (> 2 dias)'
        
        df_registros = df_tutor.sort_values(by='DCO', ascending=False)
        
        relatorio_data = {
            'total': len(df_tutor),
            'prazo': len(df_tutor[df_tutor['StatusTutor'] == 'prazo']),
            'fora': len(df_tutor[df_tutor['StatusTutor'] == 'fora']),
            'nao': len(df_tutor[df_tutor['StatusTutor'] == 'nao'])
        }
        
        # Geração do gráfico
        if HAS_MATPLOTLIB:
            try:
                img_buffer = gerar_grafico_barras(relatorio_data, nome_tutor)
            except Exception as e:
                print(f"Erro ao gerar gráfico: {e}")
                
    # Gera o PDF
    try:
        pdf_output = gerar_pdf_tutor(relatorio_data, nome_tutor, df_registros, img_buffer)
    except Exception as e:
        flash(f"Erro ao gerar PDF: {e}", 'danger')
        return redirect(url_for('relatorio_tutor', tutor=nome_tutor))

    return send_file(
        pdf_output, 
        mimetype='application/pdf', 
        as_attachment=True, 
        download_name=f'Relatorio_Tutor_{nome_tutor}_{datetime.now().strftime("%Y%m%d")}.pdf'
    )
