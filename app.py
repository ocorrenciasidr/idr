import os
import json
import re
import base64
from io import BytesIO
from flask import send_file
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from dateutil import parser as date_parser
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, abort
import pandas as pd
from supabase import create_client, Client 
from fpdf import FPDF # Importado novamente para garantir escopo

# --- PLACEHOLDERS (NECESSÁRIOS PARA O CÓDIGO RODAR SEM ERROS) ---
# Se você tiver o código dessas funções/variáveis, substitua os placeholders.
PRAZO_DIAS = 7
SETORES_ATENDIMENTO = ['PROFESSOR', 'TUTOR', 'COORDENAÇÃO', 'GESTÃO']
FORMATO_ENTRADA = None # Permite que o Pandas infira o formato da data do Supabase
def calcular_relatorio_estatistico_tutor(df, start, end): return {}
def calcular_status_prazo(row):
    # Simula o status baseado nas colunas DT, DC, DG
    status = {}
    if row.get('DT') not in ('', 'None'):
        status['TUTOR'] = 'No Prazo'
    return status
def calcular_relatorio_estatistico(): return {}
def calcular_relatorio_por_sala(): return []
# ------------------------------------------------------------------

# --- Configuração de Fuso Horário ---
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except ImportError:
    # Fallback para ambientes sem zoneinfo (como Render mais antigos ou Py < 3.9) [cite: 1]
    TZ_SAO = timezone(timedelta(hours=-3))

# --- Imports para FPDF e Matplotlib ---
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

# Mapeamento: Coluna do DB (MAIÚSCULO) para o nome interno do Pandas/App [cite: 8]
FINAL_COLUMNS_MAP = {
    'ID': 'Nº Ocorrência', 
    'PROFESSOR': 'PROFESSOR',
    'SALA': 'Sala',
    'ALUNO': 'Aluno',
    'DCO': 'DCO',
    'HCO': 'HCO',
    'DESCRICAO': 'Descrição da Ocorrência',
    'ATP': 'Atendimento Professor', 
    'ATT': 'ATT', 'ATC': 'ATC', 'ATG': 'ATG', 
    'FT': 'FT', 'FC': 'FC', 'FG': 'FG', 
    'DT': 'DT', 'DC': 'DC', 'DG': 'DG', 
    'STATUS': 'Status',
    'TUTOR': 'Tutor' 
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
        
        # Mapeamento: Renomeia as colunas do DB (MAIÚSCULO) para as chaves do App/Pandas [cite: 14]
        rename_map = {db_col: app_col for db_col, app_col in FINAL_COLUMNS_MAP.items() if db_col in df.columns}
        df = df.rename(columns=rename_map)

    # 1. Garante todas as colunas restantes e o tipo de valor padrão
    for col in expected_cols_app:
        if col not in df.columns: 
            df[col] = 0 if col == 'Nº Ocorrência' else ''
    
    # 2. Processamento de datas e tipos 
    if 'Nº Ocorrência' in df.columns:
        df['Nº Ocorrência'] = pd.to_numeric(df['Nº Ocorrência'], errors='coerce').fillna(0).astype(int)

    # CORREÇÃO CRÍTICA DE INDENTAÇÃO E U+00A0
    for col in ['DCO', 'DT', 'DC', 'DG', 'HCO']:
        if col in df.columns:
            df[col] = pd.to_datetime(
                df[col], 
                format=FORMATO_ENTRADA, 
                errors='coerce', 
                utc=True
            ).dt.tz_convert(TZ_SAO)

            
            # Coluna DCO é formatada para o display no HTML (DD/MM/AAAA) [cite: 15, 16]
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
            df[col] = df[col].astype(str).str.strip().str.upper().fillna('')

    _df_cache = df
    return df

# -------------------- Lógica de Relatórios (Funções Auxiliares) --------------------

def calcular_relatorio_tutor_ocorrencias():
    """Calcula a quantidade de ocorrências por aluno, agrupando o resultado por Tutor."""
    try:
        df_alunos = carregar_dados_alunos()
    except Exception:
        df_completo = carregar_dados()
        df_alunos = df_completo[['Tutor', 'Aluno', 'Sala']].drop_duplicates().dropna(subset=['Tutor', 'Aluno'])
        
    df_ocorrencias = carregar_dados()
    
    if df_alunos.empty: return {}

    df_ocorrencias['Aluno'] = df_ocorrencias['Aluno'].str.strip()
    ocorrencias_por_aluno = df_ocorrencias.groupby('Aluno').size().reset_index(name='Quantidade Ocorrências')

    alunos_e_tutores = df_alunos[['Tutor', 'Aluno', 'Sala']].drop_duplicates(subset=['Aluno']).dropna(subset=['Tutor', 'Aluno'])
    
    relatorio_df = pd.merge(alunos_e_tutores, ocorrencias_por_aluno, on='Aluno', how='left')
    relatorio_df['Quantidade Ocorrências'] = relatorio_df['Quantidade Ocorrências'].fillna(0).astype(int)
    
    relatorio_final = {}
    for tutor, group in relatorio_df.groupby('Tutor'):
        alunos_data = group.sort_values(by='Aluno').to_dict('records')
        relatorio_final[tutor] = [
            {'Aluno': row['Aluno'], 'Sala': row['Sala'], 'Quantidade Ocorrências': row['Quantidade Ocorrências']}
            for row in alunos_data
        ]
        
    return relatorio_final


def calculate_display_status_and_color(row):
    """Calcula o status de exibição e a cor baseados nos flags FT/FC/FG."""
    status_db = row['Status']
    ft_done = row['FT'].upper() == 'SIM'
    fc_done = row['FC'].upper() == 'SIM'
    fg_done = row['FG'].upper() == 'SIM'

    # 1. ASSINADA
    if status_db == 'ASSINADA':
        row['DisplayStatus'] = 'ASSINADA'
        row['DisplayColor'] = 'success' 
        return row
    
    # 2. ATENDIMENTO (Requer ação) [cite: 19]
    if not ft_done or not fc_done or not fg_done:
        row['DisplayStatus'] = 'ATENDIMENTO'
        row['DisplayColor'] = 'danger' 
        return row
    
    # 3. FINALIZADA (Todos os atendimentos feitos, mas não assinada) [cite: 20]
    if ft_done and fc_done and fg_done:
        row['DisplayStatus'] = 'FINALIZADA'
        row['DisplayColor'] = 'warning' 
        return row
        
    # Fallback
    row['DisplayStatus'] = status_db
    row['DisplayColor'] = 'secondary'
    return row

# -------------------- Rotas do Flask --------------------

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
   
    tutores_disp = sorted(df['Tutor'].unique().tolist()) if not df.empty and 'Tutor' in df.columns else []
    status_disp = ['ATENDIMENTO', 'FINALIZADA', 'ASSINADA', 'ABERTA']

    filtro_tutor = request.args.get('tutor')
    filtro_status = request.args.get('status')
    
    ocorrencias_filtradas = df.copy()
    
    ocorrencias_filtradas = ocorrencias_filtradas.apply(calculate_display_status_and_color, axis=1)

    if filtro_tutor:
        ocorrencias_filtradas = ocorrencias_filtradas[ocorrencias_filtradas['Tutor'] == filtro_tutor]
    
    if filtro_status:
        ocorrencias_filtradas = ocorrencias_filtradas[ocorrencias_filtradas['DisplayStatus'] == filtro_status]
    
    if 'Nº Ocorrência' in ocorrencias_filtradas.columns:
        ocorrencias_filtradas = ocorrencias_filtradas.sort_values(by='Nº Ocorrência', ascending=False)


    ocorrencias_lista = ocorrencias_filtradas.to_dict('records')

    return render_template("index.html",
                           registros=ocorrencias_lista,
                           tutores_disp=tutores_disp,
                           tutor_sel=filtro_tutor,
                           status_disp=status_disp,
                           status_sel=filtro_status)

# -------------------- API para Nova Ocorrência --------------------

@app.route("/api/alunos_por_sala/<sala>")
def alunos_por_sala(sala):
    """Retorna lista de alunos e seus tutores para uma sala específica."""
    df_alunos = carregar_dados_alunos()
    alunos_filtrados = df_alunos[df_alunos['Sala'].str.upper() == sala.upper()]
    resultado = alunos_filtrados[['Aluno', 'Tutor']].to_dict('records')
    return jsonify(resultado)

# -------------------- Rota de Nova Ocorrência (Corrigida) --------------------

# app.py (dentro da função nova)

@app.route("/nova", methods=["GET", "POST"])
def nova():
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro ao conectar ao banco de dados.", "danger")
        return redirect(url_for("index"))

    if request.method == "POST":
        try:
            # Captura dos campos do formulário
            professor = request.form.get("professor") # Este é o nome do Professor
            sala = request.form.get("sala")
            aluno = request.form.get("aluno")
            tutor = request.form.get("tutor")
            descricao = request.form.get("descricao")
            atp = request.form.get("atp") 

            # CORREÇÃO CRÍTICA: O usuário logado é o Professor.
            usuario = professor 

            # Captura dos flags FT, FC, FG
            flag_ft = request.form.get("ft")
            flag_fc = request.form.get("fc")
            flag_fg = request.form.get("fg")
            
            # Data e hora local SP
            # Certifique-se de que datetime e TZ_SAO estão corretamente importados
            now_local = datetime.now(TZ_SAO) 
            dco_str = now_local.strftime("%Y-%m-%d %H:%M:%S")
            hco_str = now_local.strftime("%H:%M:%S")

            dados_insercao = {
                "PROFESSOR": professor,
                "SALA": sala,                   
                "ALUNO": aluno,                 
                "TUTOR": tutor,                 
                "DESCRICAO": descricao,
                "ATP": atp,                         
                "ATT": "",                         
                "ATC": "",                  
                "ATG": "",                  
                
                "FT": flag_ft, 
                "FC": flag_fc, 
                "FG": flag_fg,
                
                # CORREÇÃO FINAL: Chave minúscula (usuario) com valor do Professor
                "usuario": usuario, 
                
                "DCO": dco_str,
                "HCO": hco_str,
                "STATUS": "ATENDIMENTO" 
            }

            # Insere no Supabase e limpa o cache
            supabase.table("ocorrencias").insert(dados_insercao).execute()
            limpar_caches()
            flash("Ocorrência registrada com sucesso!", "success")
            return redirect(url_for("index"))

        except Exception as e:
            flash(f"Erro ao registrar ocorrência: {e}", "danger")
            # Este print ajuda a debugar no console do Render se necessário
            print(f"Erro ao registrar ocorrência: {e}") 
            return redirect(url_for("index"))
    
    # RENDERIZA O FORMULÁRIO (GET)
    return render_template("nova.html",
                           professores=carregar_professores(),
                           salas=carregar_salas())    
    # RENDERIZA O FORMULÁRIO (GET)
    return render_template("nova.html",
                           professores=carregar_professores(),
                           salas=carregar_salas())
    
    # RENDERIZA O FORMULÁRIO (GET)
    return render_template("nova.html",
                           professores=carregar_professores(),
                           salas=carregar_salas())

# -------------------- Rota de Geração de PDF do Aluno --------------------
# O código do PDF é extenso, garantindo que todas as funções auxiliares e rotas estejam inclusas
# e que a função gerar_pdf_ocorrencias (não usada diretamente na rota) e a classe PDF estejam presentes.

# PDF GENERATION CLASS
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 12)
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
    """Adiciona os detalhes de uma única ocorrência ao objeto PDF."""
    w_label = 45
    w_value = 145
    
    pdf.set_font('Arial', 'B', 10)
    pdf.set_fill_color(240, 240, 240)
    
    def add_meta_row(label, value):
        value_display = str(value).split(' ')[0] if label == 'Data:' and value else str(value)

        pdf.set_font('Arial', 'B', 10)
        pdf.cell(w_label, 7, label, 'LR', 0, 'L', 1) 
        pdf.set_font('Arial', '', 10)
        pdf.cell(w_value, 7, value_display, 'LR', 1, 'L', 0) 

    pdf.set_draw_color(0, 0, 0)
    pdf.cell(w_label + w_value, 0, '', 'T', 1, 'L') 
    
    add_meta_row('Aluno:', ocorrencia.get('Aluno', 'N/D'))
    add_meta_row('Tutor:', ocorrencia.get('Tutor', 'N/D'))
    add_meta_row('Data:', ocorrencia.get('DCO', 'N/D'))
    add_meta_row('Professor:', ocorrencia.get('PROFESSOR', 'N/D'))
    
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Sala:', 'LBR', 0, 'L', 1) 
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value, 7, ocorrencia.get('Sala', 'N/D'), 'RBT', 1, 'L', 0) 
    
    pdf.ln(2)
    
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label, 7, 'Ocorrência nº:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value / 2, 7, str(ocorrencia.get('Nº Ocorrência', 'N/D')), 1, 0, 'L') 

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(w_label / 2, 7, 'Hora:', 1, 0, 'L', 1)
    pdf.set_font('Arial', '', 10)
    pdf.cell(w_value / 2 - w_label / 2, 7, ocorrencia.get('HCO', 'N/D'), 1, 1, 'L')
    
    pdf.ln(5)

    def adicionar_bloco_texto(label, campo_db):
        pdf.set_font('Arial', 'B', 10)
        pdf.cell(0, 7, label, 1, 1, 'L', 1)
        pdf.set_font('Arial', '', 10)
        conteudo = ocorrencia.get(campo_db, '').strip()
        if not conteudo:
             conteudo = 'NÃO APLICÁVEL'
        pdf.multi_cell(0, 6, conteudo, 1, 'L', 0) 
        pdf.ln(2)

    adicionar_bloco_texto('Descrição:', 'Descrição da Ocorrência') 
    adicionar_bloco_texto('Atendimento Professor:', 'Atendimento Professor') # Usando nome mapeado
    adicionar_bloco_texto('Atendimento Tutor (Se solicitado):', 'ATT')
    adicionar_bloco_texto('Atendimento Coordenação (Se solicitado):', 'ATC')
    adicionar_bloco_texto('Atendimento Gestão (Se solicitado):', 'ATG')
    
    pdf.ln(10)
    
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(100, 7, 'Assinatura Responsável:', 0, 0, 'L')
    pdf.cell(0, 7, 'Data:       /       /       ', 0, 1, 'L')
    
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

    supabase = conectar_supabase()
    if not supabase:
        flash("Erro ao conectar ao banco de dados.", "danger")
        return redirect(url_for("relatorio_aluno", sala=sala, aluno=aluno))

    selecionadas = [int(x) for x in selecionadas]

    # Busca no Supabase as ocorrências selecionadas
    response = supabase.table("ocorrencias").select("*").in_("ID", selecionadas).execute()
    ocorrencias_db = response.data
    
    # Busca dados no DF para ter os nomes mapeados (Ex: 'Nº Ocorrência' em vez de 'ID')
    df = carregar_dados() 
    df_selecionadas = df[df['Nº Ocorrência'].isin(selecionadas)].to_dict('records')


    pdf = PDF('P', 'mm', 'A4')
    pdf.alias_nb_pages() # Habilita o contador de páginas {{nb}}
    pdf.add_page()
    
    # CORREÇÃO: Usar a função auxiliar para adicionar o conteúdo formatado
    for row in df_selecionadas:
        pdf.add_page()
        _adicionar_ocorrencia_ao_pdf(pdf, row)

        # Atualizar status no Supabase (no DB, não no DF)
        supabase.table("ocorrencias").update({"STATUS": "ASSINADA"}) \
            .eq("ID", row["Nº Ocorrência"]).execute()
        
    limpar_caches() # Limpa o cache para refletir o status atualizado

    pdf_output = BytesIO(pdf.output(dest='S').encode('latin-1'))
    pdf_output.seek(0)

    return send_file(pdf_output, as_attachment=True, download_name=f"Relatorio_{aluno}.pdf", mimetype="application/pdf")

# -------------------- Rotas de Relatórios --------------------

@app.route("/relatorio_estatistica_tutor", methods=["GET"])
def relatorio_estatistica_tutor():
    """Rota para gerar a estatística de atendimento por tutor."""
    
    data_inicio_str = request.args.get("start")
    data_fim_str = request.args.get("end")
    
    df_completo = carregar_dados()
    
    relatorio_dados = calcular_relatorio_estatistico_tutor(
        df_completo, 
        data_inicio_str, 
        data_fim_str
    )
    
    return render_template(
        "relatorio_estatistica_tutor.html",
        relatorio=relatorio_dados,
        start=data_inicio_str,
        end=data_fim_str
    )

@app.route("/relatorio_alunos_tutor")
def relatorio_alunos_tutor():
    """Rota para gerar o relatório de alunos e suas ocorrências agrupado por tutor."""
    dados_relatorio = calcular_relatorio_tutor_ocorrencias()
    
    return render_template(
        "relatorio_tutor_ocorrencias.html",
        dados=dados_relatorio
    )

@app.route("/relatorio_geral")
def relatorio_geral():
    estatisticas_resumo = calcular_relatorio_estatistico()
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
    relatorio = {'TUTOR A': {'total': 10, 'prazo': 8, 'fora': 1, 'nao': 1}}
    return render_template("relatorio_tutor.html", relatorio=relatorio, start=start_date_str, end=end_date_str)

@app.route("/relatorios")
def relatorios():
    return render_template("relatorios.html")

@app.route("/tutoria")
def tutoria(): 
    return render_template("tutoria.html")

# -------------------- Rota de Edição --------------------

@app.route("/editar/<int:oid>", methods=["GET", "POST"])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro ao conectar ao banco de dados.", "danger")
        return redirect(url_for("index"))

    response = supabase.table('ocorrencias').select("*").eq("ID", oid).execute()
    if not response.data or len(response.data) == 0:
        flash(f"Ocorrência Nº {oid} não encontrada.", "danger")
        return redirect(url_for("index"))

    ocorrencia = response.data[0]

    if request.method == "POST":
        data = request.form

        update_data = {}
        now_local = datetime.now(TZ_SAO)

        # FT → ATT, FC → ATC, FG → ATG
        # Se o campo "Feito" ainda era SIM, significa que o atendimento está sendo inserido
        if ocorrencia["FT"] == "SIM":
            update_data["ATT"] = data.get("ATT", ocorrencia.get("ATT", ""))
            update_data["FT"] = "NÃO" # Marca como atendido/editado
            update_data["DT"] = now_local.isoformat()
        if ocorrencia["FC"] == "SIM":
            update_data["ATC"] = data.get("ATC", ocorrencia.get("ATC", ""))
            update_data["FC"] = "NÃO"
            update_data["DC"] = now_local.isoformat()
        if ocorrencia["FG"] == "SIM":
            update_data["ATG"] = data.get("ATG", ocorrencia.get("ATG", ""))
            update_data["FG"] = "NÃO"
            update_data["DG"] = now_local.isoformat()

        # Campos comuns liberados para edição
        update_data["DESCRICAO"] = data.get("DESCRICAO", ocorrencia.get("DESCRICAO", ""))
        update_data["ATP"] = data.get("ATP", ocorrencia.get("ATP", ""))

        # Atualiza status automaticamente
        # A lógica aqui no código original estava um pouco confusa. Simplificando:
        # Se algum flag de atendimento ainda for 'SIM' (não foi editado), mantém ATENDIMENTO. 
        # Caso contrário, se todos foram alterados para 'NÃO', considera FINALIZADA.
        if update_data.get("FT", ocorrencia["FT"]) == "SIM" or \
           update_data.get("FC", ocorrencia["FC"]) == "SIM" or \
           update_data.get("FG", ocorrencia["FG"]) == "SIM":
            update_data["STATUS"] = "ATENDIMENTO"
        else:
            update_data["STATUS"] = "FINALIZADA"

        try:
            supabase.table('ocorrencias').update(update_data).eq("ID", oid).execute()
            limpar_caches() # Limpa o cache após a atualização
            flash(f"Ocorrência Nº {oid} atualizada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao atualizar ocorrência: {e}", "danger")
        return redirect(url_for("index"))

    # Define quais campos serão apenas visualizados ou editáveis
    campos_editaveis = {
        "DESCRICAO": True,
        "ATP": True,
        "ATT": ocorrencia["FT"] == "SIM", # Editável se FT for SIM (pendente)
        "ATC": ocorrencia["FC"] == "SIM", # Editável se FC for SIM (pendente)
        "ATG": ocorrencia["FG"] == "SIM"  # Editável se FG for SIM (pendente)
    } 
    
    papel = request.args.get("papel", "ver")
    if papel == "ver":
        for key in campos_editaveis: campos_editaveis[key] = False
        modo = "view"
    else:
        modo = "edit"
        
    return render_template("editar.html", ocorrencia=ocorrencia, campos_editaveis=campos_editaveis, modo=modo)


# -------------------- Rota de Relatório de Aluno --------------------

@app.route("/relatorio_aluno", methods=["GET", "POST"])
def relatorio_aluno():
    sala_sel = request.args.get("sala", "")
    aluno_sel = request.args.get("aluno", "")
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro ao conectar ao banco de dados.", "danger")
        return redirect(url_for("relatorio_inicial"))

    try:
        response = supabase.table("ocorrencias").select("*").execute()
        df = pd.DataFrame(response.data)

        # Normalizar colunas de data/hora para exibição BR
        if not df.empty:
            # Note: Estas colunas vêm do DB, logo são uppercase. [cite: 34]
            if "DCO" in df.columns:
                df["DCO"] = pd.to_datetime(df["DCO"], errors="coerce").dt.strftime("%d/%m/%Y")
            if "HCO" in df.columns:
                df["HCO"] = pd.to_datetime(df["HCO"], errors="coerce").dt.strftime("%H:%M")
            # Adicionar a coluna de nome mapeado para a descrição (DESCRICAO -> Descrição da Ocorrência)
            df = df.rename(columns={'DESCRICAO': 'Descrição da Ocorrência', 'ID': 'Nº Ocorrência'})

        if sala_sel:
            df = df[df["SALA"] == sala_sel]
        if aluno_sel:
            df = df[df["ALUNO"] == aluno_sel]

        salas = sorted(df["SALA"].dropna().unique().tolist()) if "SALA" in df.columns else []
        alunos = sorted(df["ALUNO"].dropna().unique().tolist()) if "ALUNO" in df.columns else []
        registros = df.to_dict(orient="records")

    except Exception as e:
        flash(f"Erro ao carregar relatório de alunos: {e}", "danger")
        registros, salas, alunos, sala_sel, aluno_sel = [], [], [], "", ""
        
    return render_template(
        "relatorio_aluno.html",
        registros=registros,
        salas=salas,
        alunos=alunos,
        sala_sel=sala_sel,
        aluno_sel=aluno_sel
    )


if __name__ == "__main__":
    # Comando de execução para Render
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, port=port)





