noimport os
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
    'AT_PROFESSOR': 'Atendimento Professor',
    'ATT': 'ATT', 'ATC': 'ATC', 'ATG': 'ATG', 
    'FT': 'FT', 'FC': 'FC', 'FG': 'FG', 
    'DT': 'DT', 'DC': 'DC', 'DG': 'DG', 
    'STATUS': 'Status',
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
            df[col] = pd.to_datetime(df[col], errors='coerce') 
            
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

@app.route("/relatorio_inicial")
def relatorio_inicial():
    """Rota para carregar o modal de senha antes de acessar o menu de relatórios."""
    return render_template("relatorio_inicial.html")

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
    
    # APLICA A LÓGICA DINÂMICA DE STATUS AQUI
    ocorrencias_filtradas = ocorrencias_filtradas.apply(calculate_display_status_and_color, axis=1)

    ocorrencias_lista = ocorrencias_filtradas.sort_values(by='Nº Ocorrência', ascending=False).to_dict('records')
    # Gera a lista de status baseada nos status CALCULADOS
    status_opcoes = ['Todos'] + sorted(ocorrencias_filtradas['DisplayStatus'].unique().tolist()) 

    return render_template("index.html",
                           registros=ocorrencias_lista,
                           tutores_disp=tutores_disp,
                           salas_disp=salas_disp,
                           professores_disp=professores_disp,
                           status_list=status_opcoes,
                           tutor_sel=filtro_tutor,
                           sala_sel=filtro_sala,
                           status_sel=filtro_status)


@app.route('/editar/<int:oid>', methods=['GET', 'POST'])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        return redirect(url_for("index"))

    # Buscar ocorrência no Supabase - coluna 'ID' em MAIÚSCULO
    try:
        response = supabase.table("ocorrencias").select("*").eq("ID", oid).execute()
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
                pass 

    # Lógica de Permissões (papel 'ft', 'fc', 'fg' adicionados)
    papel = request.args.get('papel', 'lupa')
    permissoes = { 
        "visualizar": True, "editar_descricao": False, "editar_atp": False, 
        "editar_att": False, "editar_atc": False, "editar_atg": False, 
    }
    
    if papel == "lapis": 
        permissoes.update({
            "editar_descricao": True, "editar_atp": True, 
            "editar_att": True, "editar_atc": True, "editar_atg": True,
        })
    elif papel == "ft": 
        # Permissão especial para o Tutor (FT)
        permissoes["editar_att"] = True
    elif papel == "fc": 
        # Permissão especial para a Coordenação (FC)
        permissoes["editar_atc"] = True
    elif papel == "fg": 
        # Permissão especial para a Gestão (FG)
        permissoes["editar_atg"] = True

    if request.method == "POST":
        dados_update = {}
        now_iso = datetime.now(TZ_SAO).isoformat()
        
        # Mapeamento do Form para o DB (TUDO EM MAIÚSCULO)
        
        if permissoes["editar_descricao"] and "DESCRICAO" in request.form:
            dados_update["DESCRICAO"] = request.form["DESCRICAO"]
        
        if permissoes["editar_atp"] and "ATP" in request.form:
            dados_update["AT_PROFESSOR"] = request.form["ATP"]
        
        # Atualização do Tutor (ATT)
        if permissoes["editar_att"] and "ATT" in request.form:
            # Colunas de atendimento (ATT, ATC, ATG)
            dados_update["ATT"] = request.form["ATT"]
            # Colunas de flag e data (FT, DT, FC, DC, FG, DG)
            dados_update["FT"] = "SIM" if dados_update["ATT"].strip() else "NÃO" 
            dados_update["DT"] = now_iso if dados_update["ATT"].strip() else None 
        
        # Atualização da Coordenação (ATC)
        if permissoes["editar_atc"] and "ATC" in request.form:
            dados_update["ATC"] = request.form["ATC"]
            dados_update["FC"] = "SIM" if dados_update["ATC"].strip() else "NÃO" 
            dados_update["DC"] = now_iso if dados_update["ATC"].strip() else None 

        # Atualização da Gestão (ATG)
        if permissoes["editar_atg"] and "ATG" in request.form:
            dados_update["ATG"] = request.form["ATG"]
            dados_update["FG"] = "SIM" if dados_update["ATG"].strip() else "NÃO" 
            dados_update["DG"] = now_iso if dados_update["ATG"].strip() else None 

        # Lógica de Atualização de Status (Baseada no estado das flags)
        # Deve usar os valores mais recentes (os que estão sendo atualizados ou os originais)
        ft = dados_update.get("FT", ocorrencia_raw.get("FT", 'NÃO')).upper()
        fc = dados_update.get("FC", ocorrencia_raw.get("FC", 'NÃO')).upper()
        fg = dados_update.get("FG", ocorrencia_raw.get("FG", 'NÃO')).upper()

        if ft == "SIM" and fc == "SIM" and fg == "SIM":
            dados_update["STATUS"] = "ASSINADA" # Todos os atendimentos feitos
        elif ft == "SIM" or fc == "SIM" or fg == "SIM":
            dados_update["STATUS"] = "ATENDIMENTO" # Pelo menos um atendimento feito
        else:
            dados_update["STATUS"] = "Aberta" # Nenhum atendimento feito

        try:
            # Atualiza no Supabase, filtrando pelo 'ID' em MAIÚSCULO
            supabase.table("ocorrencias").update(dados_update).eq("ID", oid).execute()
            
            limpar_caches() 
            flash("Ocorrência atualizada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao atualizar: {e}", "danger")
            print(f"Erro no POST /editar: {e}")
            
        return redirect(url_for('editar', oid=oid, papel=papel)) 

    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes, papel=papel)

# ... (Mantenha todos os imports e funções existentes: conectar_supabase, get_proximo_id_supabase, carregar_professores, carregar_salas, carregar_dados_alunos, carregar_dados, calculate_display_status_and_color, home, index, editar) ...

# -------------------- API para Nova Ocorrência --------------------

@app.route("/api/alunos_por_sala/<sala>")
def alunos_por_sala(sala):
    """Retorna lista de alunos e seus tutores para uma sala específica."""
    df_alunos = carregar_dados_alunos()
    # Garante que a comparação de sala seja case-insensitive, mas o retorno mantém a formatação do DB
    alunos_filtrados = df_alunos[df_alunos['Sala'].str.upper() == sala.upper()]
    # Retorna apenas Aluno e Tutor
    resultado = alunos_filtrados[['Aluno', 'Tutor']].to_dict('records')
    # O resultado deve ser {'Aluno': 'Nome', 'Tutor': 'Tutor'}
    return jsonify(resultado)


# -------------------- Rota de Nova Ocorrência (Corrigida) --------------------

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
            hco_iso = now_local.isoformat() 

            # Mapeamento do Form para o DB (TUDO EM MAIÚSCULO)
            dados_insercao = {
                "ID": next_id, "DCO": dco_iso, "HCO": hco_iso,
                "PROFESSOR": data.get('PROFESSOR', '').strip(),
                "SALA": data.get('SALA', '').strip(),
                "ALUNO": data.get('ALUNO', '').strip(),
                "TUTOR": data.get('TUTOR', '').strip(),
                "DESCRICAO": data.get('DESCRICAO', '').strip(),
                
                # NOVO CAMPO: Atendimento Professor (ATP)
                "AT_PROFESSOR": data.get('ATP', '').strip(), 
                
                # FLAGS: Se a caixa for marcada, o status inicial é 'NÃO' (Ação Pendente). 
                # Se não for marcada, é 'SIM' (Não Requerido/Feito), para não aparecer na lista de pendências.
                "FT": 'NÃO' if data.get('FT') == 'on' else 'SIM', 
                "FC": 'NÃO' if data.get('FC') == 'on' else 'SIM', 
                "FG": 'NÃO' if data.get('FG') == 'on' else 'SIM', 
                
                # Campos de Atendimento (vazios na criação)
                "ATT": '', "ATC": '', "ATG": '', 
                "DT": None, "DC": None, "DG": None, 
                "STATUS": 'Aberta'
            }

            # Lógica simples para status inicial: 
            # Se qualquer flag foi definida como 'NÃO' (Ação Pendente), o status é ATENDIMENTO.
            if dados_insercao["FT"] == "NÃO" or dados_insercao["FC"] == "NÃO" or dados_insercao["FG"] == "NÃO":
                 dados_insercao["STATUS"] = "ATENDIMENTO"
            
            supabase.table('ocorrencias').insert(dados_insercao).execute()
            
            limpar_caches()
            flash(f"Ocorrência Nº {next_id} registrada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao salvar a ocorrência: {e}", "danger")
            print(f"Erro no POST /nova: {e}")
        
        return redirect(url_for("index"))

    return render_template("nova.html", salas_disp=salas_unicas, professores_disp=professores_unicos, tutores_disp=tutores_unicos)


# -------------------- Rotas de Relatório (Adicionadas) --------------------

@app.route("/relatorio_aluno", methods=['GET', 'POST'])
def relatorio_aluno():
    df = carregar_dados()
    salas = sorted(df['Sala'].unique().tolist())
    alunos = []
    ocorrencias = []
    sala_sel = request.args.get('sala')
    aluno_sel = request.args.get('aluno')
    
    if sala_sel:
        df_alunos_sala = carregar_dados_alunos()
        alunos = sorted(df_alunos_sala[df_alunos_sala['Sala'] == sala_sel]['Aluno'].unique().tolist())
        
    if aluno_sel and sala_sel:
        ocorrencias = df[(df['Aluno'] == aluno_sel) & (df['Sala'] == sala_sel)]
        # Renomeia colunas para o template de PDF (se necessário, usando o nome da coluna no DB)
        ocorrencias = ocorrencias.rename(columns={'Nº Ocorrência': 'ID', 'Descrição da Ocorrência': 'Descrição'}).to_dict('records')

    return render_template("relatorio_aluno.html", salas=salas, alunos=alunos, sala_sel=sala_sel, aluno_sel=aluno_sel, ocorrencias=ocorrencias)

@app.route("/gerar_pdf_aluno", methods=['POST'])
def gerar_pdf_aluno():
    # Placeholder para a lógica de PDF (requer a implementação da classe PDF)
    
    if HAS_MATPLOTLIB: 
        # A lógica real da FPDF deve ser implementada aqui (omissão por complexidade)
        pdf_output = FPDF().output(dest='S').encode('latin-1') 
    else:
        pdf_output = b"PDF Generation Placeholder"
        
    pdf_file = BytesIO(pdf_output)
    
    aluno = request.form.get('aluno', 'aluno')
    sala = request.form.get('sala', 'sala')

    return send_file(
        pdf_file,
        download_name=f"relatorio_{aluno}_{sala}.pdf",
        as_attachment=True,
        mimetype='application/pdf'
    )

@app.route("/relatorio_geral")
def relatorio_geral():
    # Lógica de relatorio_geral (ajustada para usar o nome do template)
    df = carregar_dados()
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    
    df_filtrado = df.copy() 
    
    ocorrencias_lista = df_filtrado.rename(columns={'Nº Ocorrência': 'ID', 'Descrição da Ocorrência': 'Descrição'}).to_dict('records')

    return render_template("relatorio_geral.html", 
                           data_inicio=data_inicio_str, 
                           data_fim=data_fim_str, 
                           ocorrencias=ocorrencias_lista,
                           has_matplotlib=HAS_MATPLOTLIB) 
    
@app.route("/relatorio_tutor")
def relatorio_tutor():
    # Lógica de relatorio_tutor (ajustada para usar o nome do template)
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

@app.route("/relatorios")
def relatorios():
    # Redireciona para o inicial protegido por senha
    return redirect(url_for('relatorio_inicial'))

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))



