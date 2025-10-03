import os
import json
import re
import base64
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import urlencode
from dateutil import parser as date_parser

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
    

@app.route("/gerar_pdf_aluno", methods=['POST'])
def gerar_pdf_aluno():
    # Verifica se o FPDF real foi importado (usando o mock para evitar crash)
    if FPDF.__name__ == 'FPDF' and 'fpdf.FPDF' not in str(FPDF): 
        # Esta é a checagem de mock, se estiver em produção com fpdf2, remova.
        flash("Funcionalidade de PDF indisponível. Instale a biblioteca fpdf2.", "danger")
        # Retorna para o relatório do aluno ou para a home se a rota falhar
        return redirect(url_for('relatorio_aluno') if 'relatorio_aluno' in app.view_functions else url_for('home'))

    aluno_selecionado = request.form.get('aluno')
    # Recebe a lista de IDs de ocorrências selecionadas
    ocorrencias_ids = request.form.getlist('ocorrencias') 
    
    if not aluno_selecionado or not ocorrencias_ids:
        flash("Nenhuma ocorrência selecionada ou aluno não especificado.", "warning")
        return redirect(url_for('relatorio_aluno'))

    # 1. Carregar e Filtrar dados
    df_ocorrencias = carregar_dados() 
    
    # Filtrar ocorrências selecionadas (usando 'Nº Ocorrência' como a chave correta)
    df_selecionado = df_ocorrencias[
        (df_ocorrencias['Aluno'] == aluno_selecionado) & 
        (df_ocorrencias['Nº Ocorrência'].astype(str).isin(ocorrencias_ids))
    ].sort_values(by='Nº Ocorrência', ascending=True)

    if df_selecionado.empty:
        flash("As ocorrências selecionadas não foram encontradas.", "warning")
        return redirect(url_for('relatorio_aluno'))
        
    ocorrencias_lista = df_selecionado.to_dict(orient='records')

    # 2. Gerar PDF
    pdf = PDF('P', 'mm', 'A4')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.alias_nb_pages()
    
    # Adicionar fontes para suportar caracteres UTF-8 (acentos, cedilha)
    # É fundamental que os arquivos 'arial.ttf' e 'arialbd.ttf' estejam disponíveis no servidor.
    try:
        pdf.add_font('Arial', '', 'arial.ttf', uni=True)
        pdf.add_font('Arial', 'B', 'arialbd.ttf', uni=True)
    except Exception:
        pass # Fallback para fonte padrão

    for ocorrencia in ocorrencias_lista:
        pdf.add_page()
        _adicionar_ocorrencia_ao_pdf(pdf, ocorrencia)

    # 3. Saída do PDF
    buffer = BytesIO()
    pdf.output(buffer, 'S')
    buffer.seek(0)

    # Geração do nome do arquivo
    safe_aluno = re.sub(r'[^\w\s-]', '', aluno_selecionado).strip().replace(' ', '_')
    data_geracao = datetime.now(TZ_SAO).strftime('%Y%m%d')
    download_name = f"Relatorio_{safe_aluno}_{data_geracao}.pdf"
    
    return send_file(
        buffer,
        download_name=download_name,
        as_attachment=True,
        mimetype='application/pdf'
    )

# ... (restante do seu código app.py) ...
@app.route('/editar/<int:oid>', methods=['GET', 'POST'])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        return redirect(url_for("index"))

    try:
        response = supabase.table("ocorrencias").select("*").eq("ID", oid).execute()
    except Exception as e:
        flash(f"Erro ao buscar ocorrência: {e}", "danger")
        return redirect(url_for("index"))

    if not response.data:
        flash("Ocorrência não encontrada!", "danger")
        return redirect(url_for("index"))

    ocorrencia_raw = response.data[0]
    
    # Padroniza chaves para MAIÚSCULAS
    ocorrencia = {k.upper(): v for k, v in ocorrencia_raw.items()}
    ocorrencia['ID'] = ocorrencia.get('ID', oid)
    ocorrencia['STATUS'] = ocorrencia.get('STATUS', 'Aberta')
    
    # Lógica de formatação de data/hora para exibição (mantida)
    for col in ['DCO', 'DT', 'DC', 'DG', 'HCO']:
        val = ocorrencia.get(col)
        if val:
            try:
                dt_obj = date_parser.parse(str(val))
                if col == 'DCO':
                    ocorrencia[col] = dt_obj.strftime('%d/%m/%Y')
                elif col == 'HCO':
                    ocorrencia[col] = dt_obj.strftime('%H:%M')
                else: # DT, DC, DG
                    # Garante que a data seja exibida no formato DD/MM/AAAA no HTML
                    ocorrencia[col] = dt_obj.strftime('%d/%m/%Y')
            except:
                pass 

    papel = request.args.get('papel', 'lupa')
    permissoes = { 
        "visualizar": True, "editar_descricao": False, "editar_atp": False, 
        "editar_att": False, "editar_atc": False, "editar_atg": False, 
    }
    
    # LÓGICA DE PERMISSÕES (Mantida)
    if papel == "lapis": 
        permissoes.update({
            "editar_descricao": True, "editar_atp": True, 
            "editar_att": True, "editar_atc": True, "editar_atg": True,
        })
    elif papel == "ft": 
        permissoes["editar_att"] = True
    elif papel == "fc": 
        permissoes["editar_atc"] = True
    elif papel == "fg": 
        permissoes["editar_atg"] = True
    
    if papel in ["ft", "fc", "fg"]:
          permissoes["visualizar"] = True

    if request.method == "POST":
        dados_update = {}
        now_local = datetime.now(TZ_SAO)
        
        # Variável para salvar apenas a data (AAAA-MM-DD)
        now_date_str = now_local.strftime('%Y-%m-%d')
        
        # --- LÓGICA DE ATUALIZAÇÃO GERAL (Válido para o papel 'lapis') ---
        
        if permissoes["editar_descricao"] and "DESCRICAO" in request.form:
            dados_update["DESCRICAO"] = request.form["DESCRICAO"]
        
        if permissoes["editar_atp"] and "ATP" in request.form:
            dados_update["ATP"] = request.form["ATP"]
        
        # Atualização do Tutor (ATT)
        if permissoes["editar_att"] and "ATT" in request.form:
            att_val = request.form["ATT"].strip()
            dados_update["ATT"] = att_val
            # Se preenchido, marca 'NÃO' (resolvido); se vazio, 'SIM' (necessita)
            dados_update["FT"] = "NÃO" if att_val else "SIM" 
            dados_update["DT"] = now_date_str if att_val else None 
        
        # Atualização da Coordenação (ATC)
        if permissoes["editar_atc"] and "ATC" in request.form:
            atc_val = request.form["ATC"].strip()
            dados_update["ATC"] = atc_val
            # Se preenchido, marca 'NÃO' (resolvido); se vazio, 'SIM' (necessita)
            dados_update["FC"] = "NÃO" if atc_val else "SIM" 
            dados_update["DC"] = now_date_str if atc_val else None 

        # Atualização da Gestão (ATG)
        if permissoes["editar_atg"] and "ATG" in request.form:
            atg_val = request.form["ATG"].strip()
            dados_update["ATG"] = atg_val
            # Se preenchido, marca 'NÃO' (resolvido); se vazio, 'SIM' (necessita)
            dados_update["FG"] = "NÃO" if atg_val else "SIM" 
            dados_update["DG"] = now_date_str if atg_val else None 
        
        # --- LÓGICA EXPLÍCITA DE ATUALIZAÇÃO DE STATUS POR PAPEL (SE O RESPONSÁVEL CONCLUI) ---

        # Lógica para FT (Tutor):
        if papel == 'ft' and "ATT" in request.form:
            att_val = request.form["ATT"].strip()
            if att_val:
                dados_update['FT'] = 'NÃO' # Marca como concluído pelo Tutor
                dados_update['STATUS'] = 'ATENDIMENTO' # Status após 1º atendimento
                dados_update['DT'] = now_date_str 
                flash("Atendimento do Tutor registrado e status atualizado para 'ATENDIMENTO'.", "success")

        # Lógica para FC (Coordenação):
        elif papel == 'fc' and "ATC" in request.form:
            atc_val = request.form["ATC"].strip()
            if atc_val:
                dados_update['FC'] = 'NÃO' # Marca como concluído pela Coordenação
                dados_update['STATUS'] = 'COORDENAÇÃO' # Status após atendimento da Coordenação
                dados_update['DC'] = now_date_str
                flash("Atendimento da Coordenação registrado e status atualizado para 'COORDENAÇÃO'.", "success")

        # Lógica para FG (Gestão):
        elif papel == 'fg' and "ATG" in request.form:
            atg_val = request.form["ATG"].strip()
            if atg_val:
                dados_update['FG'] = 'NÃO' # Marca como concluído pela Gestão
                dados_update['STATUS'] = 'ASSINADA' # Status final
                dados_update['DG'] = now_date_str
                flash("Atendimento da Gestão (Assinatura) registrado e ocorrência finalizada.", "success")
        
        # Lógica de Status para o papel 'lapis' (que pode editar tudo e precisa de reavaliação)
        # NOTA: O 'lapis' precisa do status calculado se não houver um papel específico.
        if papel == 'lapis':
            # Obtém o status dos flags, considerando a atualização de dados_update
            ft = dados_update.get("FT", ocorrencia_raw.get("ft", 'SIM')).upper()
            fc = dados_update.get("FC", ocorrencia_raw.get("fc", 'SIM')).upper()
            fg = dados_update.get("FG", ocorrencia_raw.get("fg", 'SIM')).upper()
            
            if ft == "NÃO" and fc == "NÃO" and fg == "NÃO":
                 dados_update["STATUS"] = "ASSINADA" 
            elif fc == "NÃO": # Coordenador atendeu (independente do Tutor)
                 dados_update["STATUS"] = "COORDENAÇÃO"
            elif ft == "NÃO": # Tutor atendeu
                 dados_update["STATUS"] = "ATENDIMENTO"
            else: # Todos 'SIM' (necessita de atendimento)
                 dados_update["STATUS"] = "ABERTA" 


        try:
            supabase.table("ocorrencias").update(dados_update).eq("ID", oid).execute()
            
            limpar_caches() 
            # Se não for um dos papéis que já deu flash, mostra o flash genérico
            if not any(p == papel for p in ['ft', 'fc', 'fg']):
                 flash("Ocorrência atualizada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao atualizar: {e}", "danger")
            print(f"Erro no POST /editar: {e}")
            
        return redirect(url_for('index'))

    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes, papel=papel)
""Rota para carregar o modal de senha antes de acessar o menu de relatórios."""
    return render_template("relatorio_inicial.html")

@app.route("/relatorios")
def relatorios():
    # Rota que carrega o menu principal de relatórios
    return render_template("relatorios.html")

# ... (restante do app.py)

@app.route("/relatorio_aluno", methods=['GET', 'POST'])
def relatorio_aluno():
    df = carregar_dados() # DataFrame com todas as ocorrências
    
    # 1. Filtro Sala: Salas que possuem ocorrências.
    salas = sorted(df['Sala'].unique().tolist())
    
    alunos = []
    ocorrencias = []
    sala_sel = request.args.get('sala')
    aluno_sel = request.args.get('aluno')
    
    # DataFrame filtrado pela Sala selecionada
    df_sala = df[df['Sala'] == sala_sel] if sala_sel else df
    
    if sala_sel:
        # 2. Filtro Aluno: Apenas alunos DA SALA SELECIONADA que possuem ocorrências.
        # Usa o dataframe filtrado df_sala para obter apenas os alunos relevantes
        alunos = sorted(df_sala['Aluno'].unique().tolist())
        
    if aluno_sel and sala_sel:
        # Ocorrências específicas do aluno
        ocorrencias = df_sala[df_sala['Aluno'] == aluno_sel]
        
        # Formatação para o template (Garantindo que a coluna 'Descrição da Ocorrência' seja referenciada corretamente)
        ocorrencias = ocorrencias.rename(columns={'Nº Ocorrência': 'ID', 'Descrição da Ocorrência': 'Descrição'}).to_dict('records')

    return render_template("relatorio_aluno.html", 
                           salas=salas, 
                           alunos=alunos, 
                           sala_sel=sala_sel, 
                           aluno_sel=aluno_sel, 
                           ocorrencias=ocorrencias)

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
@app.route("/relatorio_geral")
def relatorio_geral():
    # Pega as datas do formulário ou usa um valor padrão (ex: 30 dias atrás)
    now_str = datetime.now(TZ_SAO).strftime('%Y-%m-%d')
    data_fim = request.args.get('data_fim', now_str)
    data_inicio = request.args.get('data_inicio', (datetime.now(TZ_SAO) - timedelta(days=30)).strftime('%Y-%m-%d'))

    try:
        dados_relatorio = gerar_relatorio_geral_data(data_inicio, data_fim)
    except Exception as e:
        flash(f"Erro ao gerar relatório: {e}", "danger")
        dados_relatorio = {'por_sala': [], 'por_setor': []}

    return render_template("relatorio_geral.html", 
                           data_inicio=data_inicio,
                           data_fim=data_fim,
                           relatorio_sala=dados_relatorio['por_sala'],
                           relatorio_setor=dados_relatorio['por_setor']
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

@app.route("/relatorio_tutoraluno")
def relatorio_tutoraluno():
    df_alunos = carregar_dados_alunos()
    
    dados_agrupados = df_alunos.groupby('Tutor').apply(lambda x: x[['Aluno', 'Sala']].to_dict('records')).to_dict()
    
    return render_template("relatorio_tutoraluno.html", dados=dados_agrupados)


@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))









