import os
import json
import re
import base64
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import urlencode

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, abort
import pandas as pd
from supabase import create_client, Client
from dateutil import parser as date_parser

# --- Configuração de Fuso Horário ---
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except ImportError:
    TZ_SAO = timezone(timedelta(hours=-3))

# --- Imports para FPDF e Matplotlib ---
# Importe o FPDF se estiver usando
try:
    from fpdf import FPDF
except ImportError:
    # Classe Mock para evitar falha se o FPDF não estiver instalado
    class FPDF:
        def __init__(self, *args, **kwargs): pass
        def add_page(self): pass
        def set_font(self, *args, **kwargs): pass
        def cell(self, *args, **kwargs): pass
        def ln(self): pass
        def multi_cell(self, *args, **kwargs): pass
        def output(self, *args, **kwargs): 
            pdf_mock = BytesIO()
            pdf_mock.write(b"PDF Library Missing")
            pdf_mock.seek(0)
            return pdf_mock
    
try:
    import matplotlib.pyplot as plt
    plt.switch_backend('Agg') # Necessário para rodar no Render (sem tela)
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# --- Configuração da Aplicação Flask ---
app = Flask(__name__)
# Use a Variável de Ambiente SECRET_KEY no Render!
app.secret_key = os.environ.get('SECRET_KEY', 'default_key_insegura_para_teste_local') 

# --- Variáveis globais para cache (melhora performance) ---
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
        
        # ESSENCIAL: Se as chaves não existirem, não tentamos usar valores locais.
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
        # Busca o maior 'id' (minúsculo) na tabela 'ocorrencias'
        response = supabase.table('ocorrencias').select('id').order('id', desc=True).limit(1).execute()
        
        if response.data and response.data[0].get('id') is not None:
            max_id = response.data[0]['id']
            return max_id + 1
        return 1
    except Exception as e:
        print(f"Erro ao obter próximo ID (Supabase): {e}")
        return 9999 # Retorna um ID grande para evitar colisões

# -------------------- Funções de Carregamento de Dados --------------------

def carregar_professores():
    global _professores_cache
    if _professores_cache is not None:
        return _professores_cache

    supabase = conectar_supabase()
    if not supabase: return []

    try:
        # A tabela 'Professores' e coluna 'Professor' são esperadas com Title Case
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
        # A tabela 'Salas' e coluna 'Sala' são esperadas com Title Case
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
        # Tabela 'Alunos' é esperada com Title Case (colunas Sala, Aluno, Tutor)
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
        # Acessa a tabela 'ocorrencias' (esperada em minúsculo)
        response = supabase.table('ocorrencias').select('*').order('id', desc=True).execute()
        data = response.data
    except Exception as e:
        print(f"Erro ao ler a tabela 'ocorrencias' no Supabase: {e}")
        return pd.DataFrame()

    # Mapeamento de Colunas do Supabase (snake_case/minúsculo) para o DataFrame (UPPER CASE/Title Case)
    FINAL_COLUMNS_MAP = {
        'id': 'Nº Ocorrência', 
        'descricao': 'Descrição da Ocorrência',
        'at_professor': 'Atendimento Professor', 'att': 'ATT', 'atc': 'ATC', 'atg': 'ATG', 
        'ft': 'FT', 'fc': 'FC', 'fg': 'FG', 'dco': 'DCO', 'dt': 'DT', 
        'dc': 'DC', 'dg': 'DG', 'professor': 'PROFESSOR', 'sala': 'Sala', 
        'aluno': 'Aluno', 'tutor': 'Tutor', 'status': 'Status'
    }
    
    expected_cols_app = list(FINAL_COLUMNS_MAP.values())

    if not data:
        df = pd.DataFrame([], columns=expected_cols_app)
    else:
        df = pd.DataFrame(data)
        
        # Mapeamento: Renomeia as colunas do DB para as chaves do App
        rename_map = {db_col: app_col for db_col, app_col in FINAL_COLUMNS_MAP.items() if db_col in df.columns}
        df = df.rename(columns=rename_map)

    # 1. Garante todas as colunas restantes e o tipo de valor padrão
    for col in expected_cols_app:
        if col not in df.columns: 
            df[col] = 0 if col == 'Nº Ocorrência' else ''
    
    # 2. Processamento de datas e tipos
    if 'Nº Ocorrência' in df.columns:
        df['Nº Ocorrência'] = pd.to_numeric(df['Nº Ocorrência'], errors='coerce').fillna(0).astype(int)

    for date_col in ['DCO', 'DT', 'DC', 'DG']:
        if date_col in df.columns:
            # Converte para datetime (a hora não é importante para o display, mas é para o filtro)
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce') 
            # Coluna DCO é formatada para o display no HTML
            if date_col == 'DCO':
                df['DCO'] = df['DCO'].dt.strftime('%d/%m/%Y')
                
    # 3. Limpeza de colunas de texto
    text_cols = ['PROFESSOR', 'Sala', 'Aluno', 'Tutor', 'Descrição da Ocorrência', 
                 'Atendimento Professor', 'ATT', 'ATC', 'ATG', 'Status']
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().fillna('')

    _df_cache = df
    return df

# -------------------- Rotas do Flask --------------------

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/index")
def index():
    df = carregar_dados()
    df_alunos = carregar_dados_alunos()
    
    # Prepara listas para filtros
    salas_disp = sorted(df_alunos['Sala'].unique().tolist())
    tutores_disp = sorted(df_alunos['Tutor'].unique().tolist())
    professores_disp = sorted(df['PROFESSOR'].unique().tolist()) 

    # Lógica de Filtragem
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

    return render_template("index.html",
                           registros=ocorrencias_lista,
                           tutores_disp=tutores_disp,
                           salas_disp=salas_disp,
                           professores_disp=professores_disp,
                           status_list=status_opcoes,
                           tutor_sel=filtro_tutor,
                           sala_sel=filtro_sala,
                           status_sel=filtro_status)

@app.route("/nova", methods=["GET", "POST"])
def nova():
    salas_unicas = carregar_salas()
    professores_unicos = carregar_professores()
    df_alunos = carregar_dados_alunos()
    tutores_unicos = sorted(df_alunos['Tutor'].unique().tolist())

    if request.method == "POST":
        data = request.form
        
        supabase = conectar_supabase()
        if not supabase:
            return redirect(url_for("nova"))
            
        try:
            next_id = get_proximo_id_supabase(supabase)
            now_local = datetime.now(TZ_SAO)
            dco_iso = now_local.isoformat() 

            # Mapeamento do Form (UPPER CASE) para o DB (snake_case)
            dados_insercao = {
                "id": next_id, "dco": dco_iso,          
                "professor": data.get('PROFESSOR', '').strip(),
                "sala": data.get('SALA', '').strip(),
                "aluno": data.get('ALUNO', '').strip(),
                "tutor": data.get('TUTOR', '').strip(),
                "descricao": data.get('DESCRICAO', '').strip(),
                
                # Valores padrão
                "at_professor": '', "att": '', "atc": '', "atg": '', 
                "ft": 'NÃO', "fc": 'NÃO', "fg": 'NÃO', 
                "dt": None, "dc": None, "dg": None, "status": 'Aberta'
            }

            supabase.table('ocorrencias').insert(dados_insercao).execute()
            
            limpar_caches() # Limpa o cache após a inserção
            flash(f"Ocorrência Nº {next_id} registrada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao salvar a ocorrência: {e}", "danger")
            print(f"Erro no POST /nova: {e}")
        
        return redirect(url_for("index"))

    return render_template("nova.html", salas_disp=salas_unicas, professores_disp=professores_unicos, tutores_disp=tutores_unicos)

@app.route('/editar/<int:oid>', methods=['GET', 'POST'])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        return redirect(url_for("index"))

    # Buscar ocorrência no Supabase - coluna 'id' em minúsculo
    try:
        response = supabase.table("ocorrencias").select("*").eq("id", oid).execute()
    except Exception as e:
        flash(f"Erro ao buscar ocorrência: {e}", "danger")
        return redirect(url_for("index"))

    if not response.data:
        flash("Ocorrência não encontrada!", "danger")
        return redirect(url_for("index"))

    ocorrencia_raw = response.data[0]
    
    # Mapeia chaves para UPPER CASE para compatibilidade com editar.html
    ocorrencia = {k.upper(): v for k, v in ocorrencia_raw.items()}
    ocorrencia['ID'] = ocorrencia.get('ID', oid)
    ocorrencia['STATUS'] = ocorrencia.get('STATUS', 'Aberta')
    
    # Processamento de datas para o template
    for col in ['DCO', 'DT', 'DC', 'DG']:
        val = ocorrencia.get(col)
        if val:
            try:
                dt_obj = date_parser.parse(str(val))
                ocorrencia[col] = dt_obj.strftime('%d/%m/%Y')
            except:
                pass # Mantém o valor original se o parse falhar

    # Lógica de Permissões (baseada no parâmetro 'papel')
    papel = request.args.get('papel', 'lupa')
    permissoes = { "visualizar": True, "editar_descricao": False, "editar_atp": False, "editar_att": False, "editar_atc": False, "editar_atg": False, }
    
    if papel == "lapis": 
        permissoes.update({"editar_descricao": True, "editar_atp": True, "editar_att": True, "editar_atc": True, "editar_atg": True,})
    elif papel == "ft": 
        permissoes["editar_att"] = True
    elif papel == "fc": 
        permissoes["editar_atc"] = True
    elif papel == "fg": 
        permissoes["editar_atg"] = True

    if request.method == "POST":
        dados_update = {}
        now_iso = datetime.now(TZ_SAO).isoformat()
        
        # Mapeamento do Form (UPPER CASE) para o DB (snake_case)
        if permissoes["editar_descricao"] and "DESCRICAO" in request.form:
            dados_update["descricao"] = request.form["DESCRICAO"]
        
        if permissoes["editar_atp"] and "ATP" in request.form:
            dados_update["at_professor"] = request.form["ATP"] # Mapeado para 'at_professor'
        
        # Atualização do Tutor (ATT)
        if permissoes["editar_att"] and "ATT" in request.form:
            dados_update["att"] = request.form["ATT"]
            dados_update["ft"] = "SIM" if dados_update["att"].strip() else "NÃO" # Flag Feito Tutor
            dados_update["dt"] = now_iso if dados_update["att"].strip() else None # Data Tutor
        
        # Atualização da Coordenação (ATC)
        if permissoes["editar_atc"] and "ATC" in request.form:
            dados_update["atc"] = request.form["ATC"]
            dados_update["fc"] = "SIM" if dados_update["atc"].strip() else "NÃO" # Flag Feito Coord
            dados_update["dc"] = now_iso if dados_update["atc"].strip() else None # Data Coord

        # Atualização da Gestão (ATG)
        if permissoes["editar_atg"] and "ATG" in request.form:
            dados_update["atg"] = request.form["ATG"]
            dados_update["fg"] = "SIM" if dados_update["atg"].strip() else "NÃO" # Flag Feito Gestão
            dados_update["dg"] = now_iso if dados_update["atg"].strip() else None # Data Gestão

        # Lógica de Atualização de Status (Baseada no estado das flags)
        ft = dados_update.get("ft", ocorrencia_raw.get("ft"))
        fc = dados_update.get("fc", ocorrencia_raw.get("fc"))
        fg = dados_update.get("fg", ocorrencia_raw.get("fg"))

        if ft == "SIM" and fc == "SIM" and fg == "SIM":
            dados_update["status"] = "ASSINADA" # Todos os atendimentos feitos
        elif ft == "SIM" or fc == "SIM" or fg == "SIM":
             # Se pelo menos um atendimento foi feito, o status é ATENDIMENTO
            dados_update["status"] = "ATENDIMENTO" 
        else:
            dados_update["status"] = "Aberta" # Nenhum atendimento foi feito

        try:
            # Atualiza no Supabase, filtrando pelo 'id' em minúsculo
            supabase.table("ocorrencias").update(dados_update).eq("id", oid).execute()
            
            limpar_caches() # Limpa o cache após a edição
            flash("Ocorrência atualizada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao atualizar: {e}", "danger")
            print(f"Erro no POST /editar: {e}")
            
        return redirect(url_for('editar', oid=oid, papel=papel)) # Redireciona para o GET da mesma página

    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes, papel=papel)

# --- Rotas de API ---

@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    df_alunos = carregar_dados_alunos()
    # Filtra e retorna apenas as colunas 'Aluno' e 'Tutor' (Title Case)
    lista_alunos = df_alunos[df_alunos['Sala'].str.lower() == sala.lower()] \
        .sort_values(by='Aluno') \
        [['Aluno', 'Tutor']] \
        .to_dict('records')
    return jsonify(lista_alunos)

# --- Rotas de Relatórios ---

@app.route("/relatorio_inicial")
def relatorio_inicial():
    # Renderiza o menu de relatórios (relatorio_inicial.html)
    return render_template("relatorio_inicial.html")

@app.route("/relatorio_geral")
def relatorio_geral():
    # Lógica de Filtro por Data
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    
    df = carregar_dados()
    ocorrencias_filtradas = df.copy()
    
    # ... Lógica de filtragem, estatísticas, e Matplotlib aqui ...
    
    # Exemplo de lógica para prazos e filtragem de datas (mantido o esqueleto)
    if 'DCO' in df.columns:
        ocorrencias_filtradas['DCO_DT'] = pd.to_datetime(ocorrencias_filtradas['DCO'], format='%d/%m/%Y', errors='coerce')
        if data_inicio_str:
            data_inicio = pd.to_datetime(data_inicio_str)
            ocorrencias_filtradas = ocorrencias_filtradas[ocorrencias_filtradas['DCO_DT'] >= data_inicio]
        if data_fim_str:
            data_fim = pd.to_datetime(data_fim_str)
            ocorrencias_filtradas = ocorrencias_filtradas[ocorrencias_filtradas['DCO_DT'] <= data_fim]

    # ... O restante da lógica de Matplotlib e contagem deve ser mantida ...
    
    # Exemplo de variáveis de retorno:
    ocorrencias_lista = ocorrencias_filtradas.to_dict('records')
    # ... outras variáveis como estatisticas, grafico_base64
    
    return render_template("relatorio_geral.html", 
                           ocorrencias=ocorrencias_lista,
                           data_inicio=data_inicio_str, 
                           data_fim=data_fim_str, 
                           HAS_MATPLOTLIB=HAS_MATPLOTLIB)

@app.route("/relatorio_aluno", methods=['GET'])
def relatorio_aluno():
    df_alunos = carregar_dados_alunos()
    salas_disp = sorted(df_alunos['Sala'].unique().tolist()) if not df_alunos.empty else []
    
    sala_sel = request.args.get('sala')
    aluno_sel = request.args.get('aluno')
    ocorrencias = []
    alunos_da_sala = []
    
    if sala_sel and not df_alunos.empty:
        alunos_da_sala = sorted(df_alunos[df_alunos['Sala'] == sala_sel]['Aluno'].unique().tolist())
    
    if aluno_sel:
        df_ocorrencias = carregar_dados()
        if not df_ocorrencias.empty:
            df_filtrado = df_ocorrencias[df_ocorrencias['Aluno'] == aluno_sel]
            ocorrencias = df_filtrado.sort_values(by='Nº Ocorrência').to_dict('records')

    return render_template("relatorio_aluno.html", 
                           salas=salas_disp, 
                           alunos_sala=alunos_da_sala, 
                           sala_sel=sala_sel, 
                           aluno_sel=aluno_sel, 
                           ocorrencias=ocorrencias)

@app.route("/relatorio_tutor")
def relatorio_tutor():
    # Rota para gerar estatística de atendimento por tutor
    # ... Lógica aqui (filtragem por data e cálculo de prazos)
    
    return render_template("relatorio_tutor.html", relatorio={})

@app.route("/relatorio_tutoraluno")
def relatorio_tutoraluno():
    # Rota para listar tutores e seus alunos (usa o df_alunos)
    df_alunos = carregar_dados_alunos()
    
    if df_alunos.empty:
        dados_agrupados = {}
    else:
        # Agrupa por 'Tutor' e coleta os dados de 'Aluno' e 'Sala'
        dados_agrupados = df_alunos.groupby('Tutor').apply(lambda x: x[['Aluno', 'Sala']].to_dict('records')).to_dict()
    
    return render_template("relatorio_tutoraluno.html", dados=dados_agrupados)

# Rotas de Geração de PDF (Exemplo para gerar_pdf_aluno)
@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    # O restante da sua lógica de FPDF deve ser incluída aqui, usando o DataFrame carregado
    # ... (Seu código de PDF aqui, usando FPDF e o df = carregar_dados())
    
    return abort(501, description="Função de Geração de PDF não está completa neste exemplo.")

# Outras Rotas (relatorios, tutoria)
@app.route("/relatorios")
def relatorios():
    # Esta rota foi simplificada para apenas renderizar o template de menu.
    # O relatorio_inicial.html é o novo menu.
    return redirect(url_for('relatorio_inicial'))

@app.route("/tutoria")
def tutoria():
    return render_template("tutoria.html")


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))
