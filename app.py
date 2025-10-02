import os
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
import re
import base64
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import supabase

app = Flask(__name__)
app.secret_key = "idrgestao"

import os
url = os.environ.get("https://rimuhgulxliduugenxro.supabase.co")
key = os.environ.get("sb_secret_k543j2NP_ErAi9Yoyn1Keg_aMyUG4F-")
supabase: Client = create_client(https://rimuhgulxliduugenxro.supabase.co, sb_secret_k543j2NP_ErAi9Yoyn1Keg_aMyUG4F-)

import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, flash, abort

from supabase import create_client, Client

# Tenta importar ZoneInfo (Python 3.9+) ou usa timezone fallback
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ_SAO = timezone(timedelta(hours=-3))

# Imports para FPDF e Matplotlib
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
        def output(self, *args, **kwargs): return BytesIO()
    
try:
    import matplotlib.pyplot as plt
    plt.switch_backend('Agg') 
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'sua_chave_secreta_padrao') 

# -------------------- Configuração e Cache do Supabase --------------------

def conectar_supabase() -> Client | None:
    try:
        url: str | None = os.environ.get("SUPABASE_URL")
        key: str | None = os.environ.get("SUPABASE_KEY")
        
        if not url or not key:
            print("ERRO: Variáveis de ambiente SUPABASE_URL ou SUPABASE_KEY não configuradas.")
            return None

        supabase: Client = create_client(url, key)
        return supabase
    except Exception as e:
        print(f"Erro ao conectar com Supabase: {e}")
        return None

# Variáveis globais para caches
_df_cache = None
_alunos_cache = None
_professores_cache = None 
_salas_cache = None      

# -------------------- Funções de Carregamento de Dados --------------------

def carregar_professores():
    global _professores_cache
    if _professores_cache is not None:
        return _professores_cache

    supabase = conectar_supabase()
    if not supabase: return []

    try:
        # Tabela: Professores (Title Case) | Coluna: Professor (Title Case)
        response = supabase.table('Professores').select('Professor').order('Professor').execute()
        # Nota: O acesso abaixo deve ser 'Professor' (Title Case) se foi assim que você criou a coluna no DB.
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
        # Tabela: Salas (Title Case) | Coluna: Sala (Title Case)
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
        # Tabela: Alunos (Title Case) | Colunas: Sala, Aluno, Tutor (Title Case)
        response = supabase.table('Alunos').select('Sala, Aluno, Tutor').execute() 
        df_alunos = pd.DataFrame(response.data)
    except Exception as e:
        print(f"Erro ao ler a tabela 'Alunos' no Supabase: {e}") 
        return pd.DataFrame({'Sala': [], 'Aluno': [], 'Tutor': []})

    # Renomeação e limpeza (mantida)
    df_alunos['Tutor'] = df_alunos['Tutor'].fillna('SEM TUTOR').str.strip().str.upper()
    df_alunos['Aluno'] = df_alunos['Aluno'].str.strip()
    df_alunos['Sala'] = df_alunos['Sala'].str.strip()
    
    _alunos_cache = df_alunos
    return df_alunos


# -------------------- Funções de Carregamento de Dados --------------------
# ... (manter carregar_professores, carregar_salas, carregar_dados_alunos) ...

def carregar_dados():
    """Carrega dados da tabela 'ocorrencias' e formata como DataFrame."""
    global _df_cache
    if _df_cache is not None:
        return _df_cache

    supabase = conectar_supabase()
    if not supabase: return pd.DataFrame()

    try:
        # Tabela: ocorrencias (minúsculo)
        response = supabase.table('ocorrencias').select('*').execute()
        data = response.data
    except Exception as e:
        print(f"Erro ao ler a tabela 'ocorrencias' no Supabase: {e}")
        return pd.DataFrame()

    # Mapeamento de Colunas do BD para os nomes do DataFrame esperado
    FINAL_COLUMNS_MAP = {
        'id': 'Nº Ocorrência', # CORREÇÃO: Mapeando 'id' (do DB) para 'Nº Ocorrência' (da App)
        'descricao': 'Descrição da Ocorrência',
        'at_professor': 'Atendimento Professor', 'att': 'ATT', 'atc': 'ATC', 'atg': 'ATG', 
        'ft': 'FT', 'fc': 'FC', 'fg': 'FG', 'dco': 'DCO', 'dt': 'DT', 
        'dc': 'DC', 'dg': 'DG', 'professor': 'PROFESSOR', 'sala': 'Sala', 
        'aluno': 'Aluno', 'tutor': 'Tutor', 'status': 'Status'
    }
    expected_cols_app = list(FINAL_COLUMNS_MAP.values())
    APP_KEY_PROFESSOR = 'PROFESSOR'
    APP_KEY_N_OCORRENCIA = 'Nº Ocorrência' # Chave padrão para a aplicação

    if not data:
        df = pd.DataFrame([], columns=expected_cols_app)
    else:
        df = pd.DataFrame(data)
        
        # 1. Mapeamento padrão
        rename_map = {db_col: app_col for db_col, app_col in FINAL_COLUMNS_MAP.items() if db_col in df.columns}
        df = df.rename(columns=rename_map)

        # 2. Correção de Capitalização para 'PROFESSOR' (mantida)
        if APP_KEY_PROFESSOR not in df.columns:
            professor_db_key = next((col for col in df.columns if col.lower() == 'professor'), None)
            if professor_db_key:
                df = df.rename(columns={professor_db_key: APP_KEY_PROFESSOR})
            elif APP_KEY_PROFESSOR not in df.columns:
                 df[APP_KEY_PROFESSOR] = '' 

        # 3. CORREÇÃO DE CAPITALIZAÇÃO para 'Nº Ocorrência' (ID)
        if APP_KEY_N_OCORRENCIA not in df.columns:
            # Tenta encontrar a chave original 'id' com qualquer capitalização
            id_db_key = next((col for col in df.columns if col.lower() == 'id'), None)
            
            if id_db_key:
                # Renomeia a coluna que o Supabase retornou para o nome esperado pela App
                df = df.rename(columns={id_db_key: APP_KEY_N_OCORRENCIA})
            elif APP_KEY_N_OCORRENCIA not in df.columns:
                 # Cria a coluna com valor padrão 0 (para evitar erro no sort_values)
                 df[APP_KEY_N_OCORRENCIA] = 0 


    # 4. Garante todas as colunas restantes e o tipo de valor padrão
    for col in expected_cols_app:
        if col not in df.columns: 
            if col == 'Nº Ocorrência':
                df[col] = 0 
            else:
                df[col] = ''
    
    # Processamento de datas e tipos (mantido)

    # Garante que a coluna está no formato numérico
    if APP_KEY_N_OCORRENCIA in df.columns:
        df[APP_KEY_N_OCORRENCIA] = pd.to_numeric(df[APP_KEY_N_OCORRENCIA], errors='coerce').fillna(0).astype(int)

    for date_col in ['DT', 'DC', 'DG']:
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce')

    # Limpeza de colunas de texto (mantido)
    text_cols = [APP_KEY_PROFESSOR, 'Sala', 'Aluno', 'Tutor', 'Descrição da Ocorrência', 
                 'Atendimento Professor', 'ATT', 'ATC', 'ATG', 'Status']
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().fillna('')

    _df_cache = df
    return df

def get_proximo_id_supabase(supabase: Client):
    try:
        # CORREÇÃO: Selecionando e ordenando pela coluna 'id'
        response = supabase.table('ocorrencias').select('id').order('id', desc=True).limit(1).execute()
        
        if response.data and response.data[0].get('id') is not None:
            max_id = response.data[0]['id']
            return max_id + 1
        return 1
    except Exception as e:
        print(f"Erro ao obter próximo ID (Supabase): {e}")
        return 9999

# Funções de Geração de PDF/Gráficos (Esqueletos mantidos para evitar BuildError)
def gerar_grafico_barras(relatorio, tutor):
    if not HAS_MATPLOTLIB: return None
    labels = ['No Prazo', 'Fora do Prazo', 'Não Respondido', 'Aberto']
    values = [relatorio['prazo'], relatorio['fora'], relatorio['nao'], relatorio.get('aberto', 0)] 
    filtered_labels = [labels[i] for i, v in enumerate(values) if v > 0]
    filtered_values = [v for v in values if v > 0]
    if not filtered_values: return None
    colors = ['#4CAF50', '#FF9800', '#F44336', '#2196F3'] 
    filtered_colors = [colors[i] for i, v in enumerate(values) if v > 0]
    plt.figure(figsize=(8, 6))
    plt.bar(filtered_labels, filtered_values, color=filtered_colors)
    plt.title(f'Desempenho do Tutor: {tutor}', fontsize=16)
    plt.ylabel('Número de Ocorrências', fontsize=12)
    plt.xlabel('Status de Resposta', fontsize=12)
    for i, v in enumerate(filtered_values):
        plt.text(i, v + 0.1, str(v), ha='center', fontsize=10, fontweight='bold')
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    plt.xticks(rotation=15, ha='right')
    plt.tight_layout()
    buffer = BytesIO()
    plt.savefig(buffer, format='png')
    plt.close()
    buffer.seek(0)
    return buffer

def gerar_pdf_tutor(relatorio, tutor, registros, grafico_buffer):
    if not hasattr(FPDF, 'add_page'): return BytesIO()
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=16, style='B')
    pdf.cell(0, 10, txt=f"Relatório de Atendimento do Tutor: {tutor}", ln=True, align='C')
    # Adicione a lógica de PDF aqui
    pdf_output = BytesIO()
    pdf_bytes = pdf.output(dest='S').encode('latin1') 
    pdf_output.write(pdf_bytes)
    pdf_output.seek(0)
    return pdf_output

# Função para buscar todas as ocorrências
def buscar_ocorrencias():
    data = client.table("Ocorrencias").select("*").execute()
    return data.data if data.data else []

# Função para buscar uma ocorrência específica
def buscar_ocorrencia(oid):
    data = client.table("Ocorrencias").select("*").eq("ID", oid).execute()
    return data.data[0] if data.data else None

# Função para definir permissões de edição conforme papel
def definir_permissoes(papel):
    permissoes = {
        "tutor": False,
        "coord": False,
        "gestao": False,
        "modo_edicao": False
    }
    if papel == "lapis":
        permissoes["tutor"] = True
        permissoes["coord"] = True
        permissoes["gestao"] = True
        permissoes["modo_edicao"] = True
    elif papel == "tutor":
        permissoes["tutor"] = True
    elif papel == "coord":
        permissoes["coord"] = True
    elif papel == "gestao":
        permissoes["gestao"] = True
    return permissoes

# Função que carrega os dados da tabela ocorrencias
def carregar_ocorrencias():
    df = pd.read_excel("ocorrencias.xlsx")  # ou o caminho correto do seu arquivo
    return df

# Função que salva uma ocorrência (exemplo)
def salvar_ocorrencia(dados):
    df = carregar_ocorrencias()
    df = pd.concat([df, pd.DataFrame([dados])], ignore_index=True)
    df.to_excel("ocorrencias.xlsx", index=False)

# -------------------- Rotas Principais --------------------

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/index")
def index():
    df = carregar_dados()
    df_alunos = carregar_dados_alunos()
    
    # ESTA LINHA AGORA ESTÁ SEGURA, POIS 'df' SEMPRE TERÁ A COLUNA 'Professor'
    salas_disp = sorted(df_alunos['Sala'].unique().tolist())
    tutores_disp = sorted(df_alunos['Tutor'].unique().tolist())
    professores_disp = sorted(df['PROFESSOR'].unique().tolist())

    filtro_tutor = request.args.get('tutor')
    filtro_sala = request.args.get('sala')
    filtro_status = request.args.get('status')
    
    ocorrencias_filtradas = df.copy()

    if filtro_tutor:
        ocorrencias_filtradas = ocorrencias_filtradas[ocorrencias_filtradas['Tutor'] == filtro_tutor]
    
    if filtro_sala:
        ocorrencias_filtradas = ocorrencias_filtradas[ocorrencias_filtradas['Sala'] == filtro_sala]

    if filtro_status and filtro_status != 'Todos':
        ocorrencias_filtradas = ocorrencias_filtradas[ocorrencias_filtradas['Status'] == filtro_status]
    
    ocorrencias_lista = ocorrencias_filtradas.sort_values(by='Nº Ocorrência', ascending=False).to_dict('records')
    status_opcoes = ['Todos'] + sorted(df['Status'].unique().tolist()) 

    return render_template("Index.html",
                           registros=ocorrencias_lista,
                           tutores=tutores_disp,
                           salas=salas_disp,
                           status_list=status_opcoes,
                           tutor_sel=filtro_tutor,
                           sala_sel=filtro_sala,
                           status_sel=filtro_status)

@app.route("/")
def lista_ocorrencias():
    registros = buscar_ocorrencias()
    tutores = sorted(list({r['TUTOR'] for r in registros}))
    status_list = sorted(list({r['STATUS'] for r in registros}))
    return render_template("index.html",
                           registros=registros,
                           tutores=tutores,
                           status_list=status_list)

@app.route("/nova", methods=["GET", "POST"])
def nova():
    salas_unicas = carregar_salas()
    professores_unicos = carregar_professores()

    if request.method == "POST":
        data = request.form
        
        supabase = conectar_supabase()
        if not supabase:
            flash("Erro ao conectar com Supabase. Tente novamente.", "danger")
            return redirect(url_for("nova"))
            
        try:
            next_id = get_proximo_id_supabase(supabase)
            now_local = datetime.now(TZ_SAO)
            dco_iso = now_local.isoformat() 

            dados_insercao = {
                "n_ocorrencia": next_id, "dco": dco_iso,          
                "professor": data.get('professor', ''), "sala": data.get('sala', ''),
                "aluno": data.get('aluno', ''), "tutor": data.get('tutor', ''),
                "descricao": data.get('descricao', ''),
                "at_professor": '', "att": '', "atc": '', "atg": '', 
                "ft": 'NÃO', "fc": 'NÃO', "fg": 'NÃO', 
                "dt": None, "dc": None, "dg": None, "status": 'Aberta'
            }

            supabase.table('Ocorrencias').insert(dados_insercao).execute()
            
            global _df_cache, _alunos_cache, _professores_cache, _salas_cache
            _df_cache = None
            _alunos_cache = None
            _professores_cache = None
            _salas_cache = None
            
            flash(f"Ocorrência Nº {next_id} registrada com sucesso!", "success")

        except Exception as e:
            flash(f"Erro ao salvar: {e}", "danger")
            print(f"Erro no POST /nova: {e}")

        return redirect(url_for("index"))

    return render_template("nova.html", salas=salas_unicas, professores=professores_unicos)

@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    df_alunos = carregar_dados_alunos()
    
    lista_alunos = df_alunos[df_alunos['Sala'].str.lower() == sala.lower()] \
                        .sort_values(by='Aluno') \
                        [['Aluno', 'Tutor']] \
                        .to_dict('records')
    
    return jsonify(lista_alunos)

@app.route('/editar/<int:oid>', methods=['GET', 'POST'])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        flash("Erro ao conectar com Supabase.", "danger")
        return redirect(url_for("index"))

    # Buscar ocorrência no Supabase
    response = supabase.table("OCORRENCIAS").select("*").eq("ID", oid).execute()
    if not response.data:
        flash("Ocorrência não encontrada!", "danger")
        return redirect(url_for("index"))

    ocorrencia = response.data[0]

    # Determinar papel (ft, fc, fg, lapis, lupa)
    papel = request.args.get('papel', 'lupa')

    # Definição de permissões
    permissoes = {
        "visualizar": True,
        "editar_descricao": False,
        "editar_atp": False,
        "editar_att": False,
        "editar_atc": False,
        "editar_atg": False,
    }

    if papel == "lapis":
        permissoes.update({
            "editar_descricao": True,
            "editar_atp": True,
            "editar_att": True,
            "editar_atc": True,
            "editar_atg": True,
        })
    elif papel == "ft":
        permissoes["editar_att"] = True
    elif papel == "fc":
        permissoes["editar_atc"] = True
    elif papel == "fg":
        permissoes["editar_atg"] = True
    # papel == "lupa" => somente visualização

    # Se for POST, atualizar os dados
    if request.method == "POST":
        dados_update = {}

        if permissoes["editar_descricao"] and "DESCRICAO" in request.form:
            dados_update["DESCRICAO"] = request.form["DESCRICAO"]

        if permissoes["editar_atp"] and "ATP" in request.form:
            dados_update["ATP"] = request.form["ATP"]

        if permissoes["editar_att"] and "ATT" in request.form:
            dados_update["ATT"] = request.form["ATT"]
            dados_update["FT"] = "NÃO"
            dados_update["DT"] = datetime.now(TZ_SAO).isoformat()

        if permissoes["editar_atc"] and "ATC" in request.form:
            dados_update["ATC"] = request.form["ATC"]
            dados_update["FC"] = "NÃO"
            dados_update["DC"] = datetime.now(TZ_SAO).isoformat()

        if permissoes["editar_atg"] and "ATG" in request.form:
            dados_update["ATG"] = request.form["ATG"]
            dados_update["FG"] = "NÃO"
            dados_update["DG"] = datetime.now(TZ_SAO).isoformat()

        # Atualizar STATUS conforme regras
        ft = dados_update.get("FT", ocorrencia.get("FT"))
        fc = dados_update.get("FC", ocorrencia.get("FC"))
        fg = dados_update.get("FG", ocorrencia.get("FG"))

        att = dados_update.get("ATT", ocorrencia.get("ATT", "")).strip()
        atc = dados_update.get("ATC", ocorrencia.get("ATC", "")).strip()
        atg = dados_update.get("ATG", ocorrencia.get("ATG", "")).strip()

        # Regra 1: se algum FT/FC/FG == "SIM" => ATENDIMENTO
        if ft == "SIM" or fc == "SIM" or fg == "SIM":
            dados_update["STATUS"] = "ATENDIMENTO"

        # Regra 2: se todos FT/FC/FG == "NÃO" e todos ATT/ATC/ATG preenchidos => ASSINADA
        elif ft == "NÃO" and fc == "NÃO" and fg == "NÃO" and att and atc and atg:
            dados_update["STATUS"] = "ASSINADA"

        # Regra 3: se todos FT/FC/FG == "NÃO" mas algum ATT/ATC/ATG vazio => FINALIZADA
        elif ft == "NÃO" and fc == "NÃO" and fg == "NÃO":
            dados_update["STATUS"] = "FINALIZADA"

        try:
            supabase.table("OCORRENCIAS").update(dados_update).eq("ID", oid).execute()
            flash("Ocorrência atualizada com sucesso!", "success")

            # Limpar cache
            global _df_cache
            _df_cache = None

        except Exception as e:
            flash(f"Erro ao atualizar ocorrência: {e}", "danger")

        return redirect(url_for("index"))

    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes, papel=papel)


# -------------------- ROTAS DE RELATÓRIO --------------------

@app.route("/relatorio_inicial")
def relatorio_inicial():
    return render_template("relatorio_inicial.html")

# Rotas de relatórios /relatorio_aluno, /gerar_pdf_aluno, /relatorio_geral, /gerar_pdf_geral, 
# /relatorio_tutor, /gerar_pdf_relatorio_tutor, /relatorio_tutoraluno, /tutoria e /abrir_pendencia
# (Mantenha o código dessas rotas exatamente como o fornecido na etapa anterior)
# ...

# --- ROTAS DE RELATÓRIOS (Mantidas) ---

@app.route("/relatorio_aluno")
def relatorio_aluno():
    df = carregar_dados()
    df_alunos = carregar_dados_alunos()
    salas = sorted(df_alunos['Sala'].unique().tolist())
    
    sala_sel = request.args.get('sala')
    aluno_sel = request.args.get('aluno')
    ocorrencias_aluno = None
    
    if sala_sel and aluno_sel:
        ocorrencias_aluno = df[
            (df["Sala"].str.strip().str.lower() == sala_sel.strip().lower()) &
            (df["Aluno"].str.strip().str.lower() == aluno_sel.strip().lower())
        ].sort_values(by='Nº Ocorrência', ascending=False).to_dict('records')

    alunos_na_sala = []
    if sala_sel:
        alunos_na_sala = df_alunos[df_alunos['Sala'].str.lower() == sala_sel.lower()] \
                            .sort_values(by='Aluno')['Aluno'].tolist()

    return render_template("relatorio_aluno.html", 
                           salas=salas, 
                           sala_sel=sala_sel, 
                           alunos_na_sala=alunos_na_sala, 
                           aluno_sel=aluno_sel,
                           ocorrencias=ocorrencias_aluno)

@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    aluno = request.form.get("aluno")
    ocorrencias_ids = request.form.getlist("ocorrencias")
    
    if not aluno or not ocorrencias_ids:
        flash("Selecione um aluno e pelo menos uma ocorrência para gerar o PDF.", "warning")
        return redirect(url_for('relatorio_aluno'))
        
    df = carregar_dados()

    try:
        ocorrencias = df[df['Nº Ocorrência'].astype(str).isin(ocorrencias_ids)].sort_values(by='Nº Ocorrência', ascending=True)
    except Exception:
        flash("Erro ao filtrar ocorrências. Verifique os dados.", "danger")
        return redirect(url_for('relatorio_aluno'))
    
    if ocorrencias.empty:
        flash(f"Nenhuma ocorrência encontrada para os IDs selecionados do aluno {aluno}.", "danger")
        return redirect(url_for('relatorio_aluno'))

    if not hasattr(FPDF, 'add_page'):
        flash("A biblioteca FPDF não está disponível para gerar o PDF.", "danger")
        return redirect(url_for('relatorio_aluno'))

    pdf = FPDF('P', 'mm', 'A4')
    pdf.add_page()
    pdf.set_font("Arial", size=16, style='B')
    pdf.cell(0, 10, txt=f"Relatório de Ocorrências: {aluno}", ln=True, align='C')
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, txt=f"Sala: {ocorrencias['Sala'].iloc[0]} | Tutor: {ocorrencias['Tutor'].iloc[0]}", ln=True, align='C')
    pdf.ln(5)

    for _, row in ocorrencias.iterrows():
        pdf.set_font("Arial", size=12, style='B')
        pdf.cell(0, 7, txt=f"Ocorrência Nº {row['Nº Ocorrência']} - Data: {row['DCO'].strftime('%d/%m/%Y')}", ln=True, border=1)
        
        pdf.set_font("Arial", size=10, style='I')
        pdf.multi_cell(0, 5, txt=f"Professor: {row['Professor']} | Status: {row['Status']}")
        
        pdf.set_font("Arial", size=10)
        pdf.multi_cell(0, 5, txt=f"Descrição: {row['Descrição da Ocorrência']}", border=0)
        
        pdf.set_font("Arial", size=10, style='B')
        pdf.cell(0, 5, "Atendimento Professor:", ln=True)
        pdf.set_font("Arial", size=10)
        pdf.multi_cell(0, 5, txt=row['Atendimento Professor'] if row['Atendimento Professor'] else 'N/D', border=0)

        pdf.set_font("Arial", size=10, style='B')
        pdf.cell(0, 5, "Atendimento Tutor (ATT):", ln=True)
        pdf.set_font("Arial", size=10)
        pdf.multi_cell(0, 5, txt=row['ATT'] if row['ATT'] else 'N/D', border=0)
        
        pdf.set_font("Arial", size=10, style='B')
        pdf.cell(0, 5, "Atendimento Coordenação (ATC):", ln=True)
        pdf.set_font("Arial", size=10)
        pdf.multi_cell(0, 5, txt=row['ATC'] if row['ATC'] else 'N/D', border=0)
        
        pdf.set_font("Arial", size=10, style='B')
        pdf.cell(0, 5, "Atendimento Gestão (ATG):", ln=True)
        pdf.set_font("Arial", size=10)
        pdf.multi_cell(0, 5, txt=row['ATG'] if row['ATG'] else 'N/D', border=0)
        
        pdf.ln(5)

    output = BytesIO()
    pdf.output(output)
    output.seek(0)
    return send_file(output, download_name=f"relatorio_{aluno}.pdf", as_attachment=True)


@app.route("/relatorio_geral")
def relatorio_geral():
    df = carregar_dados()
    
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    
    data_inicio = None
    data_fim = None
    
    try:
        if data_inicio_str:
            data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO)
        if data_fim_str:
            data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO) + timedelta(days=1)
    except ValueError:
        flash("Formato de data inválido.", "danger")
        return render_template("relatorio_geral.html", ocorrencias=None, data_inicio=data_inicio_str, data_fim=data_fim_str, grafico_base64=None)

    df_filtrado = df.copy()

    if data_inicio:
        df_filtrado = df_filtrado[df_filtrado['DCO'] >= data_inicio.replace(tzinfo=None)]
    if data_fim:
        df_filtrado = df_filtrado[df_filtrado['DCO'] < data_fim.replace(tzinfo=None)]

    def calcular_prazo(row):
        if row['Status'] == 'Aberta':
            return 'Pendente'
        
        data_criacao = row['DCO']
        data_resposta = row['DT'] if pd.notna(row['DT']) else row['DC'] if pd.notna(row['DC']) else row['DG']
        
        if pd.isna(data_resposta):
            return 'Não Respondido' 

        prazo_limite = data_criacao + timedelta(days=7)
        
        if data_resposta.date() <= prazo_limite.date():
            return 'No Prazo'
        else:
            return 'Fora do Prazo'

    df_filtrado['Prazo'] = df_filtrado.apply(calcular_prazo, axis=1)

    grafico_base64 = None
    if HAS_MATPLOTLIB and not df_filtrado.empty:
        status_counts = df_filtrado['Status'].value_counts()
        
        plt.figure(figsize=(10, 6))
        status_counts.plot(kind='bar', color=['#2196F3', '#FFC107', '#4CAF50', '#F44336'])
        plt.title('Distribuição de Status de Ocorrências', fontsize=16)
        plt.ylabel('Contagem', fontsize=12)
        plt.xlabel('Status', fontsize=12)
        plt.xticks(rotation=45, ha='right')
        plt.grid(axis='y', linestyle='--', alpha=0.7)
        plt.tight_layout()

        buffer = BytesIO()
        plt.savefig(buffer, format='png')
        plt.close()
        buffer.seek(0)
        grafico_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')


    ocorrencias_lista = df_filtrado[['Nº Ocorrência', 'Aluno', 'Sala', 'Status', 'Prazo']].rename(columns={'Nº Ocorrência': 'ID'}).to_dict('records')

    return render_template("relatorio_geral.html", 
                           ocorrencias=ocorrencias_lista,
                           data_inicio=data_inicio_str,
                           data_fim=data_fim_str,
                           grafico_base64=grafico_base64)

@app.route("/gerar_pdf_geral", methods=["POST"])
def gerar_pdf_geral():
    if not hasattr(FPDF, 'add_page'):
        flash("A biblioteca FPDF não está disponível para gerar o PDF.", "danger")
        return redirect(url_for('relatorio_geral'))
        
    data_inicio_str = request.form.get('data_inicio')
    data_fim_str = request.form.get('data_fim')
    
    df = carregar_dados()
    df_filtrado = df.copy()

    # Lógica de filtragem e cálculo de prazo (repetida para garantir o contexto do PDF)
    try:
        if data_inicio_str:
             start = datetime.strptime(data_inicio_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO)
             df_filtrado = df_filtrado[df_filtrado['DCO'] >= start.replace(tzinfo=None)]
        if data_fim_str:
            end = datetime.strptime(data_fim_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO) + timedelta(days=1)
            df_filtrado = df_filtrado[df_filtrado['DCO'] < end.replace(tzinfo=None)]
    except ValueError:
        pass # Ignora, usa o DF não filtrado se a data for inválida

    def calcular_prazo_pdf(row):
        # Apenas para o PDF, simplificado se necessário
        return row.get('Prazo', 'N/A')

    if 'Prazo' not in df_filtrado.columns:
         df_filtrado['Prazo'] = df_filtrado.apply(lambda row: calcular_prazo_pdf(row), axis=1) # Recalcula Prazo, se não existir

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=16, style='B')
    pdf.cell(0, 10, txt="Estatística Geral de Ocorrências", ln=True, align='C')
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, txt=f"Período: {data_inicio_str if data_inicio_str else 'Início'} a {data_fim_str if data_fim_str else 'Fim'}", ln=True, align='C')
    
    if not df_filtrado.empty:
        pdf.ln(5)
        pdf.set_font("Arial", size=10, style='B')
        pdf.cell(20, 7, "ID", 1, 0, 'C')
        pdf.cell(50, 7, "Aluno", 1, 0, 'C')
        pdf.cell(30, 7, "Sala", 1, 0, 'C')
        pdf.cell(40, 7, "Status", 1, 0, 'C')
        pdf.cell(40, 7, "Prazo", 1, 1, 'C')

        pdf.set_font("Arial", size=10)
        for _, row in df_filtrado[['Nº Ocorrência', 'Aluno', 'Sala', 'Status', 'Prazo']].iterrows():
            pdf.cell(20, 6, str(row['Nº Ocorrência']), 1, 0, 'C')
            pdf.cell(50, 6, row['Aluno'], 1, 0, 'L')
            pdf.cell(30, 6, row['Sala'], 1, 0, 'C')
            pdf.cell(40, 6, row['Status'], 1, 0, 'C')
            pdf.cell(40, 6, row['Prazo'], 1, 1, 'C')
            
    output = BytesIO()
    pdf.output(output)
    output.seek(0)
    return send_file(output, download_name="relatorio_geral.pdf", as_attachment=True)


@app.route("/relatorio_tutor")
def relatorio_tutor():
    df = carregar_dados()
    df_alunos = carregar_dados_alunos()
    
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    
    relatorio_tutor_dados = {}
    grafico_base64 = None

    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO)
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO) + timedelta(days=1)
        except ValueError:
            flash("Formato de data inválido.", "danger")
            return render_template("relatorio_tutor.html", relatorio=relatorio_tutor_dados, grafico_base64=None)
            
        df_filtrado = df.copy()
        
        df_filtrado = df_filtrado[
            (df_filtrado['DCO'] >= start_date.replace(tzinfo=None)) & 
            (df_filtrado['DCO'] < end_date.replace(tzinfo=None))
        ]
        
        df_merge = pd.merge(df_alunos[['Tutor']].drop_duplicates(), 
                            df_filtrado, 
                            on='Tutor', 
                            how='left', 
                            indicator=True)
        
        if 'DT' not in df_merge.columns: df_merge['DT'] = pd.NaT
        if 'ATT' not in df_merge.columns: df_merge['ATT'] = ''

        def classificar_status_tutor(row):
            if pd.isna(row.get('Nº Ocorrência')):
                 return 'nao_req' 
            
            if not row.get('ATT') or pd.isna(row['DT']):
                return 'nao' 

            prazo_limite = row['DCO'] + timedelta(days=7)
            
            if row['DT'] <= prazo_limite:
                return 'prazo'
            else:
                return 'fora'

        df_merge['StatusTutor'] = df_merge.apply(classificar_status_tutor, axis=1)

        relatorio = df_merge[df_merge['StatusTutor'] != 'nao_req'].groupby('Tutor')['StatusTutor'].value_counts().unstack(fill_value=0)
        
        relatorio_tutor_dados = {}
        for tutor in df_merge['Tutor'].unique():
            counts = relatorio.loc[tutor] if tutor in relatorio.index else pd.Series(0, index=['prazo', 'fora', 'nao'])
            total_requisitado = counts.sum()
            relatorio_tutor_dados[tutor] = {
                'total': int(total_requisitado),
                'prazo': int(counts.get('prazo', 0)),
                'fora': int(counts.get('fora', 0)),
                'nao': int(counts.get('nao', 0)),
            }

        if HAS_MATPLOTLIB and not relatorio.empty:
            total_por_tutor = relatorio[['prazo', 'fora', 'nao']].sum(axis=1)
            
            plt.figure(figsize=(12, 6))
            total_por_tutor.sort_values(ascending=False).plot(kind='bar', color='#4fc3f7')
            plt.title('Total de Ocorrências Atendidas por Tutor (no Período)', fontsize=16)
            plt.ylabel('Total de Ocorrências Atendidas', fontsize=12)
            plt.xlabel('Tutor', fontsize=12)
            plt.xticks(rotation=45, ha='right')
            plt.grid(axis='y', linestyle='--', alpha=0.7)
            plt.tight_layout()

            buffer = BytesIO()
            plt.savefig(buffer, format='png')
            plt.close()
            buffer.seek(0)
            grafico_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
    return render_template("relatorio_tutor.html", 
                           relatorio=relatorio_tutor_dados, 
                           grafico_base64=grafico_base64,
                           start_date=start_date_str,
                           end_date=end_date_str)


@app.route("/gerar_pdf_relatorio_tutor", methods=["POST"])
def gerar_pdf_relatorio_tutor():
    tutor = request.form.get('tutor')
    start_date_str = request.form.get('start_date')
    end_date_str = request.form.get('end_date')

    if not tutor or not start_date_str or not end_date_str:
        flash("Parâmetros de relatório insuficientes para gerar o PDF.", "danger")
        return redirect(url_for('relatorio_tutor'))
    
    if not HAS_MATPLOTLIB or not hasattr(FPDF, 'add_page'):
        flash("Bibliotecas (Matplotlib/FPDF) não instaladas. Não é possível gerar o PDF.", "danger")
        return redirect(url_for('relatorio_tutor'))

    df = carregar_dados()
    
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO)
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO) + timedelta(days=1)
    except ValueError:
        flash("Formato de data inválido.", "danger")
        return redirect(url_for('relatorio_tutor'))

    df_filtrado = df.copy()
    df_filtrado = df_filtrado[
        (df_filtrado['DCO'] >= start_date.replace(tzinfo=None)) & 
        (df_filtrado['DCO'] < end_date.replace(tzinfo=None))
    ]

    def classificar_status_tutor_pdf(row):
        if not row.get('ATT') or pd.isna(row['DT']):
            return 'nao'
        prazo_limite = row['DCO'] + timedelta(days=7)
        return 'prazo' if row['DT'] <= prazo_limite else 'fora'

    df_filtrado['StatusTutor'] = df_filtrado.apply(classificar_status_tutor_pdf, axis=1)
    
    df_tutor = df_filtrado[df_filtrado['Tutor'] == tutor]
    
    relatorio = df_tutor.groupby('Tutor')['StatusTutor'].value_counts().unstack(fill_value=0)
    
    relatorio_final = {}
    if tutor in relatorio.index:
        counts = relatorio.loc[tutor]
        total_requisitado = counts.sum()
        relatorio_final = {
            'total': int(total_requisitado),
            'prazo': int(counts.get('prazo', 0)),
            'fora': int(counts.get('fora', 0)),
            'nao': int(counts.get('nao', 0)),
        }
    else:
        relatorio_final = {'total': 0, 'prazo': 0, 'fora': 0, 'nao': 0}

    img_buffer = None
    if relatorio_final['total'] > 0:
        img_buffer = gerar_grafico_barras(relatorio_final, tutor)
        
    registros_relatorio = df_tutor.sort_values(by='DCO', ascending=False)
    
    pdf_output = gerar_pdf_tutor(relatorio_final, tutor, registros_relatorio, img_buffer)
    
    return send_file(
        pdf_output, 
        download_name=f"relatorio_tutor_{tutor}_{start_date_str}_a_{end_date_str}.pdf", 
        as_attachment=True, 
        mimetype='application/pdf'
    )


@app.route("/relatorio_tutoraluno")
def relatorio_tutoraluno():
    df_alunos = carregar_dados_alunos()
    
    dados_agrupados = df_alunos.groupby('Tutor').apply(lambda x: x[['Aluno', 'Sala']].to_dict('records')).to_dict()
    
    return render_template("relatorio_tutoraluno.html", dados=dados_agrupados)

@app.route("/relatorios")
def relatorios():
    df_ocorrencias = carregar_ocorrencias()
    dados = df_ocorrencias.to_dict(orient="records")

    professores_disp = sorted(df_ocorrencias['PROFESSOR'].unique().tolist())
    tutores_disp = sorted(df_ocorrencias['TUTOR'].unique().tolist())
    
    return render_template("relatorios.html", dados=dados,
                           professores_disp=professores_disp,
                           tutores_disp=tutores_disp)


@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")

@app.route("/abrir_pendencia/<oid>/<papel>")
def abrir_pendencia(oid, papel):
    return redirect(url_for('editar', oid=oid, papel=papel))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000)) 
    app.run(host='0.0.0.0', port=port, debug=True)