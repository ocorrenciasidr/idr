import os
import json
import re
import base64
from io import BytesIO
from flask import send_file
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import urlencode
from dateutil import parser as date_parser
from flask import request, render_template, redirect, url_for, send_file, flash
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from fpdf import FPDF

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, abort
import pandas as pd
from supabase import create_client, Client 

# --- Configuração de Fuso Horário ---
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except ImportError:
    # Fallback para ambientes sem zoneinfo (como Render mais antigos ou Py < 3.9)
    TZ_SAO = timezone(timedelta(hours=-3))

# --- Imports para FPDF e Matplotlib ---
try:
    from fpdf import FPDF
except ImportError:
    class FPDF:
        def __init__(self, *args, **kwargs): pass
        def add_page(self): pass
        def set_font(self, *args, **kwargs): pass
        def cell(self, *args, **kwargs): pass
        def ln(self): pass
        def multi_cell(self, *args, **kwargs): pass
        def image(self, *args, **kwargs): pass
        def output(self, *args, **kwargs): 
            pdf_mock = BytesIO()
            pdf_mock.write(b"PDF Library Missing")
            pdf_mock.seek(0)
            return pdf_mock
    
try:
    import matplotlib.pyplot as plt
    plt.switch_backend('Agg')
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# --- Configuração da Aplicação Flask ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_key_insegura_para_teste_local') 

# --- Variáveis globais para cache ---
_df_cache = None
_alunos_cache = None
_professores_cache = None 
_salas_cache = None      

# -------------------- Conexão Supabase --------------------

def conectar_supabase() -> Client | None:
    """Configura e retorna o cliente Supabase. Prioriza Variáveis de Ambiente."""
    try:
        url: str | None = os.environ.get("SUPABASE_URL")
        key: str | None = os.environ.get("SUPABASE_KEY")
        
        if not url or not key:
            print("ERRO: Variáveis de ambiente SUPABASE_URL ou SUPABASE_KEY não configuradas.")
            flash("Erro de configuração. Chaves do Supabase ausentes.", "danger")
            return None

        supabase_client: Client = create_client(url, key)
        return supabase_client
    except Exception as e:
        print(f"Erro ao conectar com Supabase: {e}")
        flash(f"Erro ao conectar com Supabase: {e}", "danger")
        return None

def limpar_caches():
    """Limpa o cache após operações de escrita (POST)."""
    global _df_cache, _alunos_cache, _professores_cache, _salas_cache
    _df_cache = None
    _alunos_cache = None
    _professores_cache = None
    _salas_cache = None

def get_proximo_id_supabase(supabase: Client):
    """Busca o maior ID e retorna o próximo (id + 1)."""
    try:
        # Busca o maior 'ID' (MAIÚSCULO)
        response = supabase.table('ocorrencias').select('ID').order('ID', desc=True).limit(1).execute()
        
        if response.data and response.data[0].get('ID') is not None:
            max_id = response.data[0]['ID']
            return max_id + 1
        return 1
    except Exception as e:
        print(f"Erro ao obter próximo ID (Supabase): {e}")
        return 9999

# -------------------- Funções de Carregamento de Dados --------------------

# Mapeamento: Coluna do DB (MAIÚSCULO) para o nome interno do Pandas/App
FINAL_COLUMNS_MAP = {
    'ID': 'Nº Ocorrência', 
    'PROFESSOR': 'PROFESSOR',
    'SALA': 'Sala',
    'ALUNO': 'Aluno',
    'DCO': 'DCO',
    'HCO': 'HCO',
    'DESCRICAO': 'Descrição da Ocorrência',
    'ATP': 'Atendimento Professor', # <-- CORRIGIDO: Usando ATP no DB
    'ATT': 'ATT', 'ATC': 'ATC', 'ATG': 'ATG', 
    'FT': 'FT', 'FC': 'FC', 'FG': 'FG', 
    'DT': 'DT', 'DC': 'DC', 'DG': 'DG', 
    'STATUS': 'Status',
    'TUTOR': 'Tutor' # Adicionando o tutor
}

def carregar_professores():
    global _professores_cache
    if _professores_cache is not None:
        return _professores_cache

    supabase = conectar_supabase()
    if not supabase: return []

    try:
        response = supabase.table('Professores').select('Professor').order('Professor').execute()
        professores = sorted([d['Professor'].strip() for d in response.data if d.get('Professor')])
        _professores_cache = professores
        return professores
    except Exception as e:
        print(f"Erro ao ler a tabela 'Professores' no Supabase: {e}")
        return []

import pandas as pd
from flask import request, render_template

# --- Funções de carregamento de dados (assumidas como existentes) ---
# def carregar_dados(): ... 
# def carregar_dados_alunos(): ... 
# ----------------------------------------------------------------------

def calcular_relatorio_tutor_ocorrencias():
    """
    Calcula a quantidade de ocorrências por aluno, agrupando o resultado por Tutor.
    
    Retorna:
        dict: Um dicionário onde a chave é o nome do Tutor e o valor é uma lista 
              de dicionários de alunos [{'Aluno': '...', 'Ocorrencias': N, 'Sala': '...'}]
    """
    try:
        # Tenta carregar dados específicos de alunos (com Tutor/Sala)
        df_alunos = carregar_dados_alunos()
    except Exception:
        # Fallback: Se a função não existir, usa o DF principal e tenta extrair
        df_completo = carregar_dados()
        df_alunos = df_completo[['Tutor', 'Aluno', 'Sala']].drop_duplicates().dropna(subset=['Tutor', 'Aluno'])
        
    df_ocorrencias = carregar_dados() # DataFrame que contém as ocorrências
    
    if df_alunos.empty:
        return {}

    # 1. Limpeza e Contagem de Ocorrências
    df_ocorrencias['Aluno'] = df_ocorrencias['Aluno'].str.strip()
    
    # Conta quantas ocorrências cada aluno teve
    ocorrencias_por_aluno = df_ocorrencias.groupby('Aluno').size().reset_index(name='Quantidade Ocorrências')

    # 2. Preparação dos dados de Tutorados
    alunos_e_tutores = df_alunos[['Tutor', 'Aluno', 'Sala']].drop_duplicates(subset=['Aluno']).dropna(subset=['Tutor', 'Aluno'])
    
    # 3. Merge: Garante que TODOS os alunos sejam incluídos (left merge)
    relatorio_df = pd.merge(
        alunos_e_tutores,
        ocorrencias_por_aluno,
        on='Aluno',
        how='left'
    )
    
    # Preenche NaN (alunos sem ocorrências) com 0
    relatorio_df['Quantidade Ocorrências'] = relatorio_df['Quantidade Ocorrências'].fillna(0).astype(int)
    
    # 4. Estruturar os dados por Tutor
    relatorio_final = {}
    for tutor, group in relatorio_df.groupby('Tutor'):
        # Ordenar os alunos pelo nome para melhor visualização
        alunos_data = group.sort_values(by='Aluno').to_dict('records')
        
        relatorio_final[tutor] = [
            {'Aluno': row['Aluno'], 'Sala': row['Sala'], 'Quantidade Ocorrências': row['Quantidade Ocorrências']}
            for row in alunos_data
        ]
        
    return relatorio_final

@app.route("/relatorio_alunos_tutor")
def relatorio_alunos_tutor():
    """Rota para gerar o relatório de alunos e suas ocorrências agrupado por tutor."""
    dados_relatorio = calcular_relatorio_tutor_ocorrencias()
    
    # Você pode querer adicionar um link no seu relatorio_inicial para esta nova rota
    return render_template(
        "relatorio_tutor_ocorrencias.html",
        dados=dados_relatorio
    )


def carregar_salas():
    global _salas_cache
    if _salas_cache is not None:
        return _salas_cache

    supabase = conectar_supabase()
    if not supabase: return []

    try:
        response = supabase.table('Salas').select('Sala').order('Sala').execute()
        salas = sorted([d['Sala'].strip() for d in response.data if d.get('Sala')])
        _salas_cache = salas
        return salas
    except Exception as e:
        print(f"Erro ao ler a tabela 'Salas' no Supabase: {e}")
        return []

def carregar_dados_alunos():
    global _alunos_cache
    if _alunos_cache is not None:
        return _alunos_cache

    supabase = conectar_supabase()
    if not supabase:
        return pd.DataFrame({'Sala': [], 'Aluno': [], 'Tutor': []})

    try:
        response = supabase.table('Alunos').select('Sala, Aluno, Tutor').execute() 
        df_alunos = pd.DataFrame(response.data)
    except Exception as e:
        print(f"Erro ao ler a tabela 'Alunos' no Supabase: {e}") 
        return pd.DataFrame({'Sala': [], 'Aluno': [], 'Tutor': []})

    df_alunos['Tutor'] = df_alunos['Tutor'].fillna('SEM TUTOR').str.strip().str.upper()
    df_alunos['Aluno'] = df_alunos['Aluno'].str.strip()
    df_alunos['Sala'] = df_alunos['Sala'].str.strip()
    
    _alunos_cache = df_alunos
    return df_alunos


def carregar_dados() -> pd.DataFrame:
    """Carrega dados da tabela 'ocorrencias' e formata como DataFrame para o App."""
    global _df_cache
    if _df_cache is not None:
        return _df_cache

    supabase = conectar_supabase()
    if not supabase: return pd.DataFrame()

    try:
        # Acessa a tabela 'ocorrencias' e ordena por 'ID' (MAIÚSCULO)
        response = supabase.table('ocorrencias').select('*').order('ID', desc=True).execute()
        data = response.data
    except Exception as e:
        print(f"Erro ao ler a tabela 'ocorrencias' no Supabase: {e}")
        flash(f"Erro ao carregar dados do Supabase: {e}", "danger")
        return pd.DataFrame()

    expected_cols_app = list(FINAL_COLUMNS_MAP.values())

    if not data:
        df = pd.DataFrame([], columns=expected_cols_app)
    else:
        df = pd.DataFrame(data)
        
        # Mapeamento: Renomeia as colunas do DB (MAIÚSCULO) para as chaves do App/Pandas
        rename_map = {db_col: app_col for db_col, app_col in FINAL_COLUMNS_MAP.items() if db_col in df.columns}
        df = df.rename(columns=rename_map)

    # 1. Garante todas as colunas restantes e o tipo de valor padrão
    for col in expected_cols_app:
        if col not in df.columns: 
            df[col] = 0 if col == 'Nº Ocorrência' else ''
    
    # 2. Processamento de datas e tipos
    if 'Nº Ocorrência' in df.columns:
        df['Nº Ocorrência'] = pd.to_numeric(df['Nº Ocorrência'], errors='coerce').fillna(0).astype(int)

    for col in ['DCO', 'DT', 'DC', 'DG', 'HCO']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce', utc=True).dt.tz_convert(TZ_SAO)
            
            # Coluna DCO é formatada para o display no HTML (DD/MM/AAAA)
            if col == 'DCO':
                df['DCO'] = df['DCO'].dt.strftime('%d/%m/%Y')
            
            # Coluna HCO é formatada para o display no HTML (HH:MM)
            elif col == 'HCO':
                df['HCO'] = df['HCO'].dt.strftime('%H:%M')
                
    # 3. Limpeza de colunas de texto
    text_cols = ['PROFESSOR', 'Sala', 'Aluno', 'Tutor', 'Descrição da Ocorrência', 
                 'Atendimento Professor', 'ATT', 'ATC', 'ATG', 'Status', 'FT', 'FC', 'FG']
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.upper().fillna('') # Garante que SIM/NÃO seja maiúsculo

    _df_cache = df
    return df

# -------------------- Rotas do Flask --------------------

# --- Lógica de Status Dinâmico ---
def calculate_display_status_and_color(row):
    """Calcula o status de exibição e a cor baseados nos flags FT/FC/FG."""
    status_db = row['Status']
    # FT, FC, FG são os flags de "Feito" (SIM = Completo, NÃO = Pendente)
    ft_done = row['FT'].upper() == 'SIM'
    fc_done = row['FC'].upper() == 'SIM'
    fg_done = row['FG'].upper() == 'SIM'

    # 1. ASSINADA (Maior prioridade, definido pelo DB)
    if status_db == 'ASSINADA':
        row['DisplayStatus'] = 'ASSINADA'
        row['DisplayColor'] = 'success' # Verde
        return row
    
    # 2. ATENDIMENTO (Ação Requerida - Vermelho/Danger)
    # Se algum atendimento NÃO foi feito (NÃO = Pendente), então requer ATENDIMENTO.
    if not ft_done or not fc_done or not fg_done:
        row['DisplayStatus'] = 'ATENDIMENTO'
        row['DisplayColor'] = 'danger' # Vermelho
        return row
    
    # 3. FINALIZADA (Amarelo/Warning)
    # Se todos os atendimentos foram feitos (todos SIM), mas não está ASSINADA.
    if ft_done and fc_done and fg_done:
        row['DisplayStatus'] = 'FINALIZADA'
        row['DisplayColor'] = 'warning' # Amarelo (Conforme solicitado)
        return row
        
    # Fallback
    row['DisplayStatus'] = status_db
    row['DisplayColor'] = 'secondary'
    return row

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/relatorio_inicial", methods=["GET", "POST"])
def relatorio_inicial():
    return render_template("relatorio_inicial.html")

@app.route("/relatorio_tutoraluno", methods=["GET", "POST"])
def relatorio_tutoraluno():
    return render_template("relatorio_tutoraluno.html")

@app.route("/index")
def index():
    df = carregar_dados()
    
    # Prepara listas para filtros
    tutores_disp = sorted(df['Tutor'].unique().tolist()) if not df.empty and 'Tutor' in df.columns else []
    # Usaremos os status dinâmicos (ATENDIMENTO, FINALIZADA, ASSINADA) para o filtro
    status_disp = ['ATENDIMENTO', 'FINALIZADA', 'ASSINADA', 'ABERTA']

    # Lógica de Filtragem
    filtro_tutor = request.args.get('tutor')
    filtro_status = request.args.get('status') # NOVO FILTRO STATUS
    
    ocorrencias_filtradas = df.copy()
    
    # APLICA A LÓGICA DINÂMICA DE STATUS PARA FILTRAR CORRETAMENTE
    ocorrencias_filtradas = ocorrencias_filtradas.apply(calculate_display_status_and_color, axis=1)

    if filtro_tutor:
        ocorrencias_filtradas = ocorrencias_filtradas[ocorrencias_filtradas['Tutor'] == filtro_tutor]
    
    if filtro_status:
        # Filtra pelo DisplayStatus calculado
        ocorrencias_filtradas = ocorrencias_filtradas[ocorrencias_filtradas['DisplayStatus'] == filtro_status]
    
    # NOVO: Ordenação pela última ocorrência (ID descendente)
    # Garante que a coluna 'Nº Ocorrência' seja usada para ordenar
    if 'Nº Ocorrência' in ocorrencias_filtradas.columns:
        ocorrencias_filtradas = ocorrencias_filtradas.sort_values(by='Nº Ocorrência', ascending=False)


    ocorrencias_lista = ocorrencias_filtradas.to_dict('records')

    return render_template("index.html",
                           registros=ocorrencias_lista,
                           tutores_disp=tutores_disp,
                           tutor_sel=filtro_tutor,
                           status_disp=status_disp,
                           status_sel=filtro_status)

# ... (Rota /nova continua a mesma)

# -------------------- API para Nova Ocorrência --------------------

@app.route("/api/alunos_por_sala/<sala>")
def alunos_por_sala(sala):
    """Retorna lista de alunos e seus tutores para uma sala específica."""
    df_alunos = carregar_dados_alunos()
    alunos_filtrados = df_alunos[df_alunos['Sala'].str.upper() == sala.upper()]
    resultado = alunos_filtrados[['Aluno', 'Tutor']].to_dict('records')
    return jsonify(resultado)


# -------------------- Rota de Nova Ocorrência --------------------

# ... (Seus imports e funções auxiliares)

# -------------------- Rota de Nova Ocorrência (Ajuste Crítico de Lógica) --------------------

@app.route("/nova", methods=["GET", "POST"])
def nova():
    # ... (carregar_salas, carregar_professores, etc. - Mantido)
    salas_unicas = carregar_salas()
    professores_unicos = carregar_professores()
    df_alunos = carregar_dados_alunos()
    tutores_unicos = sorted(df_alunos['Tutor'].unique().tolist())

    if request.method == "POST":
        data = request.form
        
        supabase = conectar_supabase()
        if not supabase:
            flash("Erro ao conectar ao banco de dados.", "danger")
            return redirect(url_for("nova"))
            
        try:
            next_id = get_proximo_id_supabase(supabase)
            now_local = datetime.now(TZ_SAO)
            
            dco_iso = now_local.isoformat() 
            hco_str = now_local.strftime('%H:%M:%S')

            # NOVO: LÓGICA CORRIGIDA para FT, FC, FG (Se marcada é SIM/Pendente, se NÃO marcada é NÃO/Feito)
            # O link de "Ação Rápida" no Index é para quando a ação está PENDENTE, ou seja, FT/FC/FG é 'SIM'.
            # Se a checkbox for marcada no formulário, a ação é solicitada (SIM).
            
            # ATENÇÃO: Se a sua lógica original era que NÃO marcado = Pendente, e MARCADo = Resolvido, 
            # você deve manter o código anterior, mas a regra do link no INDEX (SIM = Pendente) exige
            # que a checkbox MARCADA gere 'SIM'.
            # Vamos seguir a regra lógica comum: Checkbox MARCADAS = Ação Solicitada (SIM)
            
            ft_solicitado = 'SIM' if data.get('FT') == 'on' else 'NÃO'
            fc_solicitado = 'SIM' if data.get('FC') == 'on' else 'NÃO'
            fg_solicitado = 'SIM' if data.get('FG') == 'on' else 'NÃO'

            dados_insercao = {
                "ID": next_id, 
                "DCO": dco_iso, 
                "HCO": hco_str,
                
                "PROFESSOR": data.get('PROFESSOR', '').strip(),
                "SALA": data.get('SALA', '').strip(),
                "ALUNO": data.get('ALUNO', '').strip(),
                "TUTOR": data.get('TUTOR', '').strip(),
                "DESCRICAO": data.get('DESCRICAO', '').strip(),
                
                "ATP": data.get('ATP', '').strip(), 
                
                "FT": ft_solicitado, # SIM se a ação do Tutor é solicitada (pendente)
                "FC": fc_solicitado, # SIM se a ação da Coordenação é solicitada (pendente)
                "FG": fg_solicitado, # SIM se a ação da Gestão é solicitada (pendente)
                
                "ATT": '', "ATC": '', "ATG": '', 
                "DT": None, "DC": None, "DG": None, 
                "STATUS": 'Aberta'
            }

            # Lógica de status para ATENDIMENTO
            # Se alguma ação for solicitada (FT='SIM' ou FC='SIM' ou FG='SIM'), o status é ATENDIMENTO
            if dados_insercao["FT"] == "SIM" or dados_insercao["FC"] == "SIM" or dados_insercao["FG"] == "SIM":
                 dados_insercao["STATUS"] = "ATENDIMENTO"
            else:
                 dados_insercao["STATUS"] = "ABERTA" # Nenhuma ação solicitada, mas ainda não é ASSINADA

            # Executa a inserção no Supabase (Mantido)
            response = supabase.table('ocorrencias').insert(dados_insercao).execute()
            
            if response.data is None or len(response.data) == 0:
                 raise Exception(f"Resposta Supabase vazia. Erro: {response.error}")
                 
            
            limpar_caches()
            flash(f"Ocorrência Nº {next_id} registrada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao salvar a ocorrência. Verifique os logs do servidor: {e}", "danger")
            print(f"Erro no POST /nova: {e}")
        
        return redirect(url_for("index"))

    return render_template("nova.html", salas_disp=salas_unicas, professores_disp=professores_unicos, tutores_disp=tutores_unicos)

# -------------------- Rota de Geração de PDF do Aluno (Ajustes de PDF e Status) --------------------

def gerar_pdf_ocorrencias(aluno, sala, ocorrencias):
    """Gera um PDF para as ocorrências de um aluno, usando FPDF."""
    from fpdf import FPDF # Importa FPDF localmente para garantir que esteja disponível
    
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    for ocorrencia in ocorrencias:
        pdf.add_page()
        pdf.set_font('Arial', 'B', 14)
        pdf.cell(0, 10, 'RELATÓRIO DE OCORRÊNCIAS', 0, 1, 'C')
        pdf.set_font('Arial', '', 10)
        
        pdf.cell(0, 6, f"Aluno: {aluno} | Sala: {sala}", 0, 1)
        pdf.cell(0, 6, f"Ocorrência Nº: {ocorrencia['ID']} | Data: {ocorrencia['DCO']}", 0, 1)
        
        pdf.ln(2)
        pdf.set_font('Arial', 'B', 10)
        pdf.multi_cell(0, 5, 'Descrição da Ocorrência:', 0, 'L')
        pdf.set_font('Arial', '', 10)
        pdf.multi_cell(0, 5, ocorrencia.get('DESCRICAO', 'N/D'), 1, 'L')
        
        pdf.ln(2)
        pdf.set_font('Arial', 'B', 10)
        pdf.multi_cell(0, 5, 'Status:', 0, 'L')
        pdf.set_font('Arial', '', 10)
        pdf.multi_cell(0, 5, ocorrencia.get('STATUS', 'N/D'), 1, 'L')
        
    pdf_output = BytesIO(pdf.output(dest='S').encode('latin-1'))
    pdf_output.seek(0)
    return pdf_output

# app.py (Código para ser inserido)

# ... (Mantenha todos os imports existentes, certificando-se de ter 'from io import BytesIO' e que FPDF está configurado) ...

# -------------------------- PDF GENERATION CLASS --------------------------
# Define a classe para a geração do PDF, herdando de FPDF
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 12)
        # Cor da borda azul escura para o cabeçalho (opcional)
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
    """Adiciona os detalhes de uma única ocorrência ao objeto PDF, replicando o layout do modelo."""
    
    # Define a largura das colunas para os metadados (30% Label, 70% Value)
    w_label = 45
    w_value = 145
    
    pdf.set_font('Arial', 'B', 10)
    pdf.set_fill_color(240, 240, 240) # Cor de fundo cinza claro para as labels
    
    # Função auxiliar para gerar linha de metadados
    def add_meta_row(label, value):
        # Quebra de linha manual para lidar com datas longas (ex: 2025-09-19 16:23:10.658000)
        value_display = str(value).split(' ')[0] if label == 'Data:' and value else str(value)

        pdf.set_font('Arial', 'B', 10)
        pdf.cell(w_label, 7, label, 'LR', 0, 'L', 1) 
        pdf.set_font('Arial', '', 10)
        pdf.cell(w_value, 7, value_display, 'LR', 1, 'L', 0) 

    # ------------------- METADADOS -------------------
    
    pdf.set_draw_color(0, 0, 0) # Borda preta
    
    # Bloco Aluno/Tutor/Data/Professor (borda superior no primeiro item)
    pdf.cell(w_label + w_value, 0, '', 'T', 1, 'L') 
    
    # Linha 1: Aluno
    add_meta_row('Aluno:', ocorrencia.get('Aluno', 'N/D'))
    
    # Linha 2: Tutor
    add_meta_row('Tutor:', ocorrencia.get('Tutor', 'N/D'))
    
    # Linha 3: Data (DCO)
    add_meta_row('Data:', ocorrencia.get('DCO', 'N/D'))
    
    # Linha 4: Professor
    add_meta_row('Professor:', ocorrencia.get('PROFESSOR', 'N/D'))
    
    # Linha 5: Sala (com borda inferior)
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Sala:', 'LBR', 0, 'L', 1) 
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value, 7, ocorrencia.get('Sala', 'N/D'), 'RBT', 1, 'L', 0) 
    
    pdf.ln(2)
    
    # Ocorrência Nº / Hora - Juntos na mesma linha
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Ocorrência nº:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    # ATENÇÃO: Usando 'Nº Ocorrência' conforme definido no seu código
    pdf.cell(w_value / 2, 7, str(ocorrencia.get('Nº Ocorrência', 'N/D')), 1, 0, 'L') 

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label / 2, 7, 'Hora:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value / 2 - w_label / 2, 7, ocorrencia.get('HCO', 'N/D'), 1, 1, 'L')
    
    pdf.ln(5)

    # ------------------- DESCRIÇÃO E ATENDIMENTOS -------------------
    
    # Função auxiliar para blocos de atendimento
    def adicionar_bloco_texto(label, campo_db):
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 7, label, 1, 1, 'L', 1)
        pdf.set_font('Arial', '', 10)
        conteudo = ocorrencia.get(campo_db, '').strip()
        if not conteudo:
             conteudo = 'NÃO APLICÁVEL'
        pdf.multi_cell(0, 6, conteudo, 1, 'L', 0) 
        pdf.ln(2)

    # Bloco Descrição
    adicionar_bloco_texto('Descrição:', 'Descrição da Ocorrência') 

    # Assumindo que os campos de atendimento são 'ATP' (Professor), 'ATT' (Tutor), 'ATC' (Coordenação), 'ATG' (Gestão)
    adicionar_bloco_texto('Atendimento Professor:', 'ATP') 
    adicionar_bloco_texto('Atendimento Tutor (Se solicitado):', 'ATT')
    adicionar_bloco_texto('Atendimento Coordenação (Se solicitado):', 'ATC')
    adicionar_bloco_texto('Atendimento Gestão (Se solicitado):', 'ATG')
    
    pdf.ln(10)
    
    # ------------------- ASSINATURA -------------------
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(100, 7, 'Assinatura Responsável:', 0, 0, 'L')
    pdf.cell(0, 7, 'Data:       /       /       ', 0, 1, 'L')
    
    # Linha tracejada de separação
    pdf.ln(5)
    pdf.set_font('Arial', '', 8)
    pdf.cell(0, 1, '-' * 125, 0, 1, 'L') 
    pdf.set_font('Arial', 'I', 8)
    pdf.cell(0, 5, 'Ocorrência registrada no SGCE.', 0, 1, 'R')
    

@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    aluno = request.form.get("aluno")
    sala = request.form.get("sala")
    selecionadas = request.form.getlist("ocorrencias[]")

    if not selecionadas:
        flash("Nenhuma ocorrência selecionada.", "warning")
        return redirect(url_for("relatorio_aluno", sala=sala, aluno=aluno))

    # Converte para inteiros (IDs do Supabase)
    selecionadas = [int(x) for x in selecionadas]

    # Busca no Supabase as ocorrências selecionadas
    response = supabase.table("ocorrencias").select("*").in_("ID", selecionadas).execute()
    ocorrencias = response.data

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "RELATÓRIO DE OCORRÊNCIAS", ln=True, align="C")
    pdf.ln(10)
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 10, f"Aluno: {aluno}  Sala: {sala}", ln=True)
    pdf.ln(5)

    for row in ocorrencias:
        pdf.set_font("Arial", "B", 11)
        pdf.cell(0, 10, f"Ocorrência Nº {row['ID']} - {row['DCO']} {row['HCO']}", ln=True)
        pdf.set_font("Arial", "", 11)
        pdf.multi_cell(0, 8, row["Descrição da Ocorrência"])
        pdf.ln(5)

        # Atualizar status no Supabase
        supabase.table("ocorrencias").update({"Status": "ASSINADA"}) \
            .eq("ID", row["ID"]).execute()

    pdf_output = BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)

    return send_file(
        pdf_output,
        as_attachment=True,
        download_name=f"Relatorio_{aluno}.pdf",
        mimetype="application/pdf"
    )

# ... (restante do seu código app.py) ...
@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro ao conectar ao banco de dados.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        data = request.form
        update_data = {}

        permissoes = {
            "editar_att": data.get("FT") == "SIM",
            "editar_atc": data.get("FC") == "SIM",
            "editar_atg": data.get("FG") == "SIM",
        }

        if permissoes["editar_att"]:
            update_data["ATT"] = data.get("ATT", "").strip()
            update_data["DT"] = datetime.now(TZ_SAO).isoformat()

        if permissoes["editar_atc"]:
            update_data["ATC"] = data.get("ATC", "").strip()
            update_data["DC"] = datetime.now(TZ_SAO).isoformat()

        if permissoes["editar_atg"]:
            update_data["ATG"] = data.get("ATG", "").strip()
            update_data["DG"] = datetime.now(TZ_SAO).isoformat()

        try:
            supabase.table("ocorrencias").update(update_data).eq("ID", oid).execute()
            limpar_caches()
            flash("Ocorrência atualizada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao salvar atualização: {e}", "danger")

        return redirect(url_for("index"))

    # GET → carregar ocorrência
    response = supabase.table("ocorrencias").select("*").eq("ID", oid).execute()
    ocorrencia = response.data[0] if response.data else None
    return render_template("editar.html", ocorrencia=ocorrencia)

@app.route("/relatorios")
def relatorios():
    # Rota que carrega o menu principal de relatórios
    return render_template("relatorios.html")

# ... (restante do app.py)

@app.route("/relatorio_aluno")
def relatorio_aluno():
    sala = request.args.get("sala")
    aluno = request.args.get("aluno")

    supabase = conectar_supabase()
    if not supabase:
        flash("Erro ao conectar ao banco de dados.", "danger")
        return redirect(url_for("index"))

    response = supabase.table("ocorrencias").select("*") \
        .eq("SALA", sala).eq("ALUNO", aluno).execute()

    ocorrencias = pd.DataFrame(response.data)

    if ocorrencias.empty:
        flash("Nenhuma ocorrência encontrada para este aluno.", "info")
        return redirect(url_for("index"))

    # 🔹 Renomear apenas para exibição no template
    ocorrencias = ocorrencias.rename(columns={"ID": "Nº Ocorrência"})

    return render_template(
        "relatorio_aluno.html",
        sala=sala,
        aluno=aluno,
        ocorrencias=ocorrencias.to_dict(orient="records")
    )

def gerar_relatorio_geral_data(start_date_str, end_date_str):
    df = carregar_dados() # Assume-se que a função de carregar dados existe e funciona
    
    if df.empty:
        return {'por_sala': [], 'por_setor': []}
        
    # Assegura que DCO é um datetime para o filtro de data
    df['DCO_DATE'] = pd.to_datetime(df['DCO'], errors='coerce').dt.normalize()
    df.dropna(subset=['DCO_DATE'], inplace=True)

    # 1. Filtro por Período
    start_date = pd.to_datetime(start_date_str)
    end_date = pd.to_datetime(end_date_str)
    
    df_filtrado = df[
        (df['DCO_DATE'] >= start_date) & 
        (df['DCO_DATE'] <= end_date)
    ].copy()
    
    if df_filtrado.empty:
        return {'por_sala': [], 'por_setor': []}
    
    # 2. Aplica a lógica de prazo (calcula o status para cada setor)
    df_filtrado['PRAZO_STATUS'] = df_filtrado.apply(calcular_status_prazo, axis=1)

    # 3. Relatório por Sala
    relatorio_sala = []
    total_geral = len(df_filtrado)
    
    for sala, grupo_sala in df_filtrado.groupby('Sala'):
        total_sala = len(grupo_sala)
        
        contagem_sala = {
            'Respondidas <7 dias': 0, 
            'Respondidas >7 dias': 0, 
            'Não Respondidas': 0
        }
        
        # Para o relatório por Sala, verificamos o prazo do PRIMEIRO atendimento registrado (Tutor/Coord/Gestão)
        for idx, row in grupo_sala.iterrows():
            
            # Pega as datas de atendimento que não são None e não são vazias
            datas_atendimento = [str(row.get(col)) for col in ['DT', 'DC', 'DG'] if str(row.get(col, 'None')) not in ('', 'None')]
            
            if datas_atendimento:
                data_ocorrencia = pd.to_datetime(row['DCO']).date()
                
                # Escolhe a data de atendimento mais antiga
                datas_parsed = [date_parser.parse(d).date() for d in datas_atendimento]
                data_atendimento_mais_rapida = min(datas_parsed)
                diferenca_dias = (data_atendimento_mais_rapida - data_ocorrencia).days
                
                if diferenca_dias <= PRAZO_DIAS:
                    contagem_sala['Respondidas <7 dias'] += 1
                else:
                    contagem_sala['Respondidas >7 dias'] += 1
            else:
                contagem_sala['Não Respondidas'] += 1

        relatorio_sala.append({
            'Sala': sala,
            'Total Ocorrências': total_sala,
            'Porcentagem': f"{((total_sala / total_geral) * 100):.1f}%" if total_geral > 0 else '0%',
            'Respondidas <7 dias': contagem_sala['Respondidas <7 dias'],
            'Respondidas >7 dias': contagem_sala['Respondidas >7 dias'],
            'Não Respondidas': contagem_sala['Não Respondidas'],
        })

    # 4. Relatório por Setor (Desempenho)
    relatorio_setor = []
    
    for setor in SETORES_ATENDIMENTO:
        total_setor = 0
        contagem_setor = {
            'No Prazo': 0, 
            'Fora do Prazo': 0, 
            'Não Respondida': 0
        }
        
        # Para o Resumo por Setor, o "Total" é o número total de ocorrências no período
        total_setor = len(df_filtrado)
        
        for idx, row in df_filtrado.iterrows():
            status = row['PRAZO_STATUS'].get(setor)
            
            if status == 'No Prazo':
                contagem_setor['No Prazo'] += 1
            elif status == 'Fora do Prazo':
                contagem_setor['Fora do Prazo'] += 1
            elif status == 'Não Respondida':
                contagem_setor['Não Respondida'] += 1
        
        relatorio_setor.append({
            'Setor': setor,
            'Total': total_setor,
            'Respondidas <7 dias': contagem_setor['No Prazo'],
            'Respondidas >7 dias': contagem_setor['Fora do Prazo'],
            'Não Respondidas': contagem_setor['Não Respondida'],
            'Porc <7 dias': f"({(contagem_setor['No Prazo'] / total_setor * 100):.0f}%)" if total_setor > 0 else '(0%)',
            'Porc >7 dias': f"({(contagem_setor['Fora do Prazo'] / total_setor * 100):.0f}%)" if total_setor > 0 else '(0%)',
            'Porc Não Resp': f"({(contagem_setor['Não Respondida'] / total_setor * 100):.0f}%)" if total_setor > 0 else '(0%)'
        })
        
    return {
        'por_sala': relatorio_sala,
        'por_setor': relatorio_setor
    }

# Rota /relatorio_geral atualizada
# Importe a função que calcula os dados estatísticos
from .app import calcular_relatorio_estatistico, calcular_relatorio_por_sala # Adapte a importação conforme a estrutura do seu projeto

@app.route("/relatorio_geral")
def relatorio_geral():
    # Calcula as estatísticas de Resposta e Não Resposta (Tabela superior)
    estatisticas_resumo = calcular_relatorio_estatistico()
    
    # Calcula a distribuição de ocorrências por sala (Tabela inferior)
    relatorio_salas = calcular_relatorio_por_sala()
    
    return render_template(
        "relatorio_geral.html",
        resumo=estatisticas_resumo,
        salas=relatorio_salas,
        data_geracao=datetime.now(TZ_SAO).strftime('%d/%m/%Y %H:%M:%S')
    )

  
@app.route("/relatorio_tutor")
def relatorio_tutor():
    df = carregar_dados()
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    
    # Placeholder: Lógica de cálculo de performance do tutor deve ser inserida aqui
    relatorio = {'TUTOR A': {'total': 10, 'prazo': 8, 'fora': 1, 'nao': 1}}

    return render_template("relatorio_tutor.html", 
                           relatorio=relatorio,
                           start=start_date_str,
                           end=end_date_str)



if __name__ == '__main__':
    app.run(debug=True)


@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))




















