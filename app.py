import os
import json
import base64
from io import BytesIO
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from dateutil import parser as date_parser
import pytz

from flask import (
    Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, abort
)
import pandas as pd
from supabase import create_client, Client
from datetime import timezone
import pytz

# -------------------- Configurações gerais --------------------
TZ_SAO = pytz.timezone("America/Sao_Paulo")
FORMATO_ENTRADA = "%Y-%m-%dT%H:%M:%S%z"  # Formato ISO padrão do Supabase

# Formatos
FORMATO_ENTRADA = "%Y-%m-%dT%H:%M:%S%z"  # formato ISO padrão vindo do Supabase
FORMATO_SAIDA_DATA = "%d/%m/%Y"
FORMATO_SAIDA_HORA = "%H:%M"

# Prazo padrão para relatórios (em dias)
PRAZO_DIAS = 7
SETORES_ATENDIMENTO = ["Tutor", "Coordenação", "Gestão"]

# -------------------- Flask app --------------------
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_key_insegura_para_teste_local')

# -------------------- Caches globais --------------------
_df_cache = None
_alunos_cache = None
_professores_cache = None
_salas_cache = None

FINAL_COLUMNS_MAP = {
    "ID": "Nº Ocorrência",
    "PROFESSOR": "PROFESSOR",
    "SALA": "Sala",
    "ALUNO": "Aluno",
    "TUTOR": "Tutor",
    "DESCRICAO_OCORRENCIA": "Descrição da Ocorrência",
    "ATENDIMENTO_PROFESSOR": "Atendimento Professor",
    "ATT": "ATT",
    "ATC": "ATC",
    "ATG": "ATG",
    "STATUS": "Status",
    "FT": "FT",
    "FC": "FC",
    "FG": "FG",
    "DCO": "DCO",  # Data da Ocorrência
    "HCO": "HCO",  # Hora da Ocorrência
    "DT": "DT",    # Data Tutor
    "DC": "DC",    # Data Coordenação
    "DG": "DG"     # Data Gestão
}
# -------------------- Conexão com Supabase --------------------
def conectar_supabase() -> Client:
    """Cria a conexão com o Supabase."""
    url = "https://<SEU_PROJETO>.supabase.co"
    key = "<SUA_CHAVE_API>"
    try:
        supabase = create_client(url, key)
        return supabase
    except Exception as e:
        print("❌ Erro ao conectar no Supabase:", e)
        return None


def obter_dados_supabase(tabela: str) -> list:
    """Obtém dados de uma tabela do Supabase."""
    supabase = conectar_supabase()
    if not supabase:
        return []
    try:
        resp = supabase.table(tabela).select("*").execute()
        return resp.data or []
    except Exception as e:
        print(f"❌ Erro ao carregar dados da tabela {tabela}:", e)
        return []

# -------------------- Utilitários de cache --------------------
def limpar_caches():
    global _df_cache, _alunos_cache, _professores_cache, _salas_cache
    _df_cache = None
    _alunos_cache = None
    _professores_cache = None
    _salas_cache = None

# -------------------- Funções de leitura --------------------
def carregar_professores():
    global _professores_cache
    if _professores_cache is not None:
        return _professores_cache

    supabase = conectar_supabase()
    if not supabase:
        return []

    try:
        resp = supabase.table('Professores').select('Professor').order('Professor').execute()
        data = resp.data or []
        profs = sorted([d.get('Professor', '').strip() for d in data if d.get('Professor')])
        _professores_cache = profs
        return profs
    except Exception as e:
        print(f"Erro ao ler Professores: {e}")
        return []


def carregar_salas():
    global _salas_cache
    if _salas_cache is not None:
        return _salas_cache

    supabase = conectar_supabase()
    if not supabase:
        return []

    try:
        resp = supabase.table('Salas').select('Sala').order('Sala').execute()
        data = resp.data or []
        salas = sorted([d.get('Sala', '').strip() for d in data if d.get('Sala')])
        _salas_cache = salas
        return salas
    except Exception as e:
        print(f"Erro ao ler Salas: {e}")
        return []


def carregar_dados_alunos():
    global _alunos_cache
    if _alunos_cache is not None:
        return _alunos_cache

    supabase = conectar_supabase()
    if not supabase:
        return pd.DataFrame(columns=['Sala', 'Aluno', 'Tutor'])

    try:
        resp = supabase.table('Alunos').select('Sala, Aluno, Tutor').execute()
        data = resp.data or []
        df = pd.DataFrame(data)
    except Exception as e:
        print(f"Erro ao ler Alunos: {e}")
        return pd.DataFrame(columns=['Sala', 'Aluno', 'Tutor'])

    if 'Tutor' in df.columns:
        df['Tutor'] = df['Tutor'].fillna('SEM TUTOR').astype(str).str.strip()
    else:
        df['Tutor'] = 'SEM TUTOR'

    df['Aluno'] = df.get('Aluno', '').astype(str).str.strip()
    df['Sala'] = df.get('Sala', '').astype(str).str.strip()

    _alunos_cache = df
    return df


def get_proximo_id_supabase(supabase: Client):
    try:
        resp = supabase.table('ocorrencias').select('ID').order('ID', desc=True).limit(1).execute()
        data = resp.data or []
        if data and data[0].get('ID') is not None:
            return int(data[0]['ID']) + 1
        return 1
    except Exception as e:
        print(f"Erro ao obter próximo ID: {e}")
        return 9999


# -------------------- Configurações globais --------------------
from datetime import timezone
import pytz

TZ_SAO = pytz.timezone("America/Sao_Paulo")
FORMATO_ENTRADA = "%Y-%m-%dT%H:%M:%S%z"  # Formato padrão ISO vindo do Supabase


# -------------------- Função carregar_dados --------------------
def carregar_dados():
    global _df_cache

    data = obter_dados_supabase("ocorrencias")  # função que busca no Supabase
    expected_cols_app = list(FINAL_COLUMNS_MAP.values())

    if not data:
        df = pd.DataFrame([], columns=expected_cols_app)
    else:
        df = pd.DataFrame(data)

        # Renomeia colunas do DB (MAIÚSCULO) para as chaves do App/Pandas
        rename_map = {
            db_col: app_col
            for db_col, app_col in FINAL_COLUMNS_MAP.items()
            if db_col in df.columns
        }
        df = df.rename(columns=rename_map)

    # 1. Garante todas as colunas e valores padrão
    for col in expected_cols_app:
        if col not in df.columns:
            df[col] = 0 if col == "Nº Ocorrência" else ""

    # 2. Processamento de tipos e datas
    if "Nº Ocorrência" in df.columns:
        df["Nº Ocorrência"] = (
            pd.to_numeric(df["Nº Ocorrência"], errors="coerce")
            .fillna(0)
            .astype(int)
        )

    for col in ["DCO", "DT", "DC", "DG", "HCO"]:
        if col in df.columns:
            df[col] = pd.to_datetime(
                df[col],
                format=FORMATO_ENTRADA,
                errors="coerce",
                utc=True,
            ).dt.tz_convert(TZ_SAO)

            # Formata para exibição
            if col == "DCO":
                df["DCO"] = df["DCO"].dt.strftime("%d/%m/%Y")
            elif col == "HCO":
                df["HCO"] = df["HCO"].dt.strftime("%H:%M")

    # 3. Limpeza de colunas de texto
    text_cols = [
        "PROFESSOR", "Sala", "Aluno", "Tutor", "Descrição da Ocorrência",
        "Atendimento Professor", "ATT", "ATC", "ATG", "Status", "FT", "FC", "FG"
    ]
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.upper().fillna("")

    _df_cache = df
    return df

# -------------------- Funções de status e relatórios --------------------

def calculate_display_status_and_color(row):
    """Calcula o status de exibição e cor com base nos flags FT/FC/FG.
    Observação: neste código adotamos a semântica onde 'SIM' significa "PENDENTE" (a ser atendido) e 'NÃO' significa "ATENDIDO".
    Isto mantém compatibilidade com a lógica de edição onde, ao atender, o campo é alterado para 'NÃO'."""
    status_db = str(row.get('Status', '')).upper()
    ft_done = str(row.get('FT', '')).upper() == 'NÃO'
    fc_done = str(row.get('FC', '')).upper() == 'NÃO'
    fg_done = str(row.get('FG', '')).upper() == 'NÃO'

    if status_db == 'ASSINADA':
        row['DisplayStatus'] = 'ASSINADA'
        row['DisplayColor'] = 'success'
        return row

    # Se algum ainda estiver pendente (SIM), precisa de atendimento
    if not ft_done or not fc_done or not fg_done:
        row['DisplayStatus'] = 'ATENDIMENTO'
        row['DisplayColor'] = 'danger'
        return row

    # Se todos atendidos, mas não assinada
    if ft_done and fc_done and fg_done:
        row['DisplayStatus'] = 'FINALIZADA'
        row['DisplayColor'] = 'warning'
        return row

    row['DisplayStatus'] = status_db or 'ABERTA'
    row['DisplayColor'] = 'secondary'
    return row


def calcular_status_prazo(row):
    """Retorna um dict com o status por setor (No Prazo / Fora do Prazo / Não Respondida).
    Usa as colunas DT, DC, DG (datas de atendimento) comparadas com DCO (data da ocorrência).
    """
    resultado = {}
    try:
        dco = row.get('DCO')
        # tenta parse de DCO como data; se já estiver DD/MM/YYYY, handle
        try:
            data_ocorrencia = pd.to_datetime(dco, dayfirst=True, errors='coerce')
        except Exception:
            data_ocorrencia = None

        for setor, col in zip(SETORES_ATENDIMENTO, ['DT', 'DC', 'DG']):
            data_at = row.get(col)
            if not data_at or str(data_at).strip() in ['', 'nan', 'None']:
                resultado[setor] = 'Não Respondida'
                continue
            try:
                dt_parsed = date_parser.parse(str(data_at))
            except Exception:
                resultado[setor] = 'Não Respondida'
                continue

            if data_ocorrencia is None or pd.isna(data_ocorrencia):
                resultado[setor] = 'Não Respondida'
            else:
                dias = (dt_parsed.date() - pd.to_datetime(data_ocorrencia).date()).days
                resultado[setor] = 'No Prazo' if dias <= PRAZO_DIAS else 'Fora do Prazo'
    except Exception as e:
        print(f"Erro calcular_status_prazo: {e}")
    return resultado


def calcular_relatorio_estatistico():
    """Gera estatísticas resumidas simples para exibição no relatorio_geral.
    Retorna um dict com totais e porcentagens básicas.
    """
    df = carregar_dados()
    if df.empty:
        return {}

    total = len(df)
    df = df.apply(calculate_display_status_and_color, axis=1)
    atendidas = len(df[df['DisplayStatus'] == 'FINALIZADA'])
    atendimento = len(df[df['DisplayStatus'] == 'ATENDIMENTO'])
    assinadas = len(df[df['DisplayStatus'] == 'ASSINADA'])

    return {
        'total': total,
        'atendidas': atendidas,
        'atendimento': atendimento,
        'assinadas': assinadas,
        'pct_atendidas': f"{(atendidas/total*100):.1f}%" if total else '0%'
    }


def calcular_relatorio_por_sala():
    df = carregar_dados()
    if df.empty:
        return []

    out = []
    total_geral = len(df)
    for sala, grupo in df.groupby('Sala'):
        out.append({
            'Sala': sala,
            'Total Ocorrências': len(grupo),
            'Porcentagem': f"{(len(grupo)/total_geral*100):.1f}%" if total_geral else '0%'
        })
    return out

# -------------------- Rotas --------------------
@app.route('/')
def home():
    return render_template('home.html')


@app.route('/index')
def index():
    df = carregar_dados()

    tutores_disp = sorted(df['Tutor'].dropna().unique().tolist()) if not df.empty and 'Tutor' in df.columns else []
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

    registros = ocorrencias_filtradas.to_dict('records')
    return render_template('index.html', registros=registros, tutores_disp=tutores_disp,
                           tutor_sel=filtro_tutor, status_disp=status_disp, status_sel=filtro_status)


@app.route('/nova', methods=['POST'])
def nova():
    supabase = conectar_supabase()
    if not supabase:
        flash('Erro de conexão com banco.', 'danger')
        return redirect(url_for('index'))

    try:
        professor = request.form.get('professor')
        descricao = request.form.get('descricao')
        usuario = request.form.get('usuario')
        aluno = request.form.get('aluno')
        sala = request.form.get('sala')

        now_local = datetime.now(TZ_SAO)
        dco_str = now_local.strftime('%Y-%m-%d %H:%M:%S')
        hco_str = now_local.strftime('%H:%M:%S')

        dados_insercao = {
            'PROFESSOR': professor,
            'DESCRICAO': descricao,
            'USUARIO': usuario,
            'ALUNO': aluno,
            'SALA': sala,
            'DCO': dco_str,
            'HCO': hco_str,
            # Flags inicializados como 'SIM' -> pendente (conforme convenção adotada aqui)
            'FT': 'SIM',
            'FC': 'SIM',
            'FG': 'SIM',
            'STATUS': 'ABERTA'
        }

        supabase.table('ocorrencias').insert(dados_insercao).execute()
        limpar_caches()
        flash('Ocorrência registrada com sucesso!', 'success')
    except Exception as e:
        print(f'Erro ao inserir ocorrencia: {e}')
        flash(f'Erro ao registrar ocorrência: {e}', 'danger')

    return redirect(url_for('index'))


@app.route('/api/alunos_por_sala/<sala>')
def alunos_por_sala(sala):
    df_alunos = carregar_dados_alunos()
    alunos_filtrados = df_alunos[df_alunos['Sala'].str.upper() == sala.upper()]
    resultado = alunos_filtrados[['Aluno', 'Tutor']].to_dict('records')
    return jsonify(resultado)


# -------------------- PDF: geração simples por aluno --------------------
from fpdf import FPDF


def gerar_pdf_ocorrencias(aluno, sala, ocorrencias):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    for ocorrencia in ocorrencias:
        pdf.add_page()
        pdf.set_font('Arial', 'B', 14)
        pdf.cell(0, 10, 'RELATÓRIO DE OCORRÊNCIAS', 0, 1, 'C')
        pdf.set_font('Arial', '', 10)

        pdf.cell(0, 6, f"Aluno: {aluno} | Sala: {sala}", 0, 1)
        pdf.cell(0, 6, f"Ocorrência Nº: {ocorrencia.get('ID')} | Data: {ocorrencia.get('DCO')}", 0, 1)
        pdf.ln(2)
        pdf.set_font('Arial', 'B', 10)
        pdf.multi_cell(0, 5, 'Descrição da Ocorrência:', 0, 'L')
        pdf.set_font('Arial', '', 10)
        pdf.multi_cell(0, 5, str(ocorrencia.get('DESCRICAO', 'N/D')))

    raw = pdf.output(dest='S')
    if isinstance(raw, str):
        raw = raw.encode('latin-1')
    bio = BytesIO(raw)
    bio.seek(0)
    return bio


@app.route('/gerar_pdf_aluno', methods=['POST'])
def gerar_pdf_aluno():
    aluno = request.form.get('aluno')
    sala = request.form.get('sala')
    selecionadas = request.form.getlist('ocorrencias[]')

    if not selecionadas:
        flash('Nenhuma ocorrência selecionada.', 'warning')
        return redirect(url_for('relatorio_aluno', sala=sala, aluno=aluno))

    selecionadas = [int(x) for x in selecionadas]
    supabase = conectar_supabase()
    if not supabase:
        flash('Erro de conexão com banco.', 'danger')
        return redirect(url_for('relatorio_aluno'))

    try:
        resp = supabase.table('ocorrencias').select('*').in_('ID', selecionadas).execute()
        ocorrencias = resp.data or []

        # Gera PDF
        pdf_bytes = gerar_pdf_ocorrencias(aluno, sala, ocorrencias)

        # Marca como ASSINADA cada ocorrência (opcional) - aqui apenas como exemplo
        for row in ocorrencias:
            try:
                supabase.table('ocorrencias').update({'STATUS': 'ASSINADA'}).eq('ID', row['ID']).execute()
            except Exception:
                pass

        limpar_caches()

        return send_file(pdf_bytes, as_attachment=True, download_name=f'Relatorio_{aluno}.pdf', mimetype='application/pdf')
    except Exception as e:
        print(f"Erro gerar_pdf_aluno: {e}")
        flash(f"Erro ao gerar PDF: {e}", 'danger')
        return redirect(url_for('relatorio_aluno'))


# -------------------- Edição de ocorrência --------------------
@app.route('/editar/<int:oid>', methods=['GET', 'POST'])
def editar(oid):
    supabase = conectar_supabase()
    if not supabase:
        flash('Erro ao conectar ao banco de dados.', 'danger')
        return redirect(url_for('index'))

    try:
        resp = supabase.table('ocorrencias').select('*').eq('ID', oid).execute()
        if not resp.data:
            flash(f'Ocorrência Nº {oid} não encontrada.', 'danger')
            return redirect(url_for('index'))
        ocorrencia = resp.data[0]
    except Exception as e:
        flash(f'Erro ao carregar ocorrência: {e}', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        data = request.form
        update_data = {}
        now_local = datetime.now(TZ_SAO)

        # Se o campo FT/FC/FG estiver pendente ('SIM'), ao inserir atendimento, marca como 'NÃO' (atendido)
        if str(ocorrencia.get('FT', '')).upper() == 'SIM' and data.get('ATT'):
            update_data['ATT'] = data.get('ATT')
            update_data['FT'] = 'NÃO'
            update_data['DT'] = now_local.isoformat()

        if str(ocorrencia.get('FC', '')).upper() == 'SIM' and data.get('ATC'):
            update_data['ATC'] = data.get('ATC')
            update_data['FC'] = 'NÃO'
            update_data['DC'] = now_local.isoformat()

        if str(ocorrencia.get('FG', '')).upper() == 'SIM' and data.get('ATG'):
            update_data['ATG'] = data.get('ATG')
            update_data['FG'] = 'NÃO'
            update_data['DG'] = now_local.isoformat()

        # Campos editáveis
        update_data['DESCRICAO'] = data.get('DESCRICAO', ocorrencia.get('DESCRICAO', ''))
        update_data['ATP'] = data.get('ATP', ocorrencia.get('ATP', ''))

        # Atualiza STATUS com base nos flags resultantes
        ft_final = update_data.get('FT', ocorrencia.get('FT', 'NÃO'))
        fc_final = update_data.get('FC', ocorrencia.get('FC', 'NÃO'))
        fg_final = update_data.get('FG', ocorrencia.get('FG', 'NÃO'))

        if ft_final.upper() == 'SIM' or fc_final.upper() == 'SIM' or fg_final.upper() == 'SIM':
            update_data['STATUS'] = 'ATENDIMENTO'
        else:
            update_data['STATUS'] = 'FINALIZADA'

        try:
            supabase.table('ocorrencias').update(update_data).eq('ID', oid).execute()
            limpar_caches()
            flash(f'Ocorrência Nº {oid} atualizada com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao atualizar ocorrência: {e}', 'danger')

        return redirect(url_for('index'))

    # GET: prepara quais campos serão editáveis
    campos_editaveis = {
        'DESCRICAO': True,
        'ATP': True,
        'ATT': str(ocorrencia.get('FT', '')).upper() == 'SIM',
        'ATC': str(ocorrencia.get('FC', '')).upper() == 'SIM',
        'ATG': str(ocorrencia.get('FG', '')).upper() == 'SIM'
    }

    papel = request.args.get('papel', 'ver')
    if papel == 'ver':
        for k in campos_editaveis:
            campos_editaveis[k] = False
        modo = 'view'
    else:
        modo = 'edit'

    return render_template('editar.html', ocorrencia=ocorrencia, campos_editaveis=campos_editaveis, modo=modo)


# -------------------- Relatórios --------------------
@app.route('/relatorio_inicial')
def relatorio_inicial():
    return render_template('relatorio_inicial.html')


@app.route('/relatorio_aluno', methods=['GET', 'POST'])
def relatorio_aluno():
    sala_sel = request.args.get('sala', '')
    aluno_sel = request.args.get('aluno', '')

    supabase = conectar_supabase()
    if not supabase:
        flash('Erro ao conectar ao banco de dados.', 'danger')
        return redirect(url_for('relatorio_inicial'))

    try:
        resp = supabase.table('ocorrencias').select('*').execute()
        data = resp.data or []
        df = pd.DataFrame(data)

        if not df.empty:
            if 'DCO' in df.columns:
                df['DCO'] = pd.to_datetime(df['DCO'], errors='coerce').dt.strftime(FORMATO_SAIDA_DATA)
            if 'HCO' in df.columns:
                df['HCO'] = pd.to_datetime(df['HCO'], errors='coerce').dt.strftime(FORMATO_SAIDA_HORA)

        if sala_sel:
            df = df[df['SALA'] == sala_sel]
        if aluno_sel:
            df = df[df['ALUNO'] == aluno_sel]

        salas = sorted(df['SALA'].dropna().unique().tolist()) if 'SALA' in df.columns else []
        alunos = sorted(df['ALUNO'].dropna().unique().tolist()) if 'ALUNO' in df.columns else []
        registros = df.to_dict(orient='records')

    except Exception as e:
        flash(f'Erro ao carregar relatório de alunos: {e}', 'danger')
        registros, salas, alunos, sala_sel, aluno_sel = [], [], [], '', ''

    return render_template('relatorio_aluno.html', registros=registros, salas=salas, alunos=alunos,
                           sala_sel=sala_sel, aluno_sel=aluno_sel)


@app.route('/relatorio_geral')
def relatorio_geral():
    estatisticas_resumo = calcular_relatorio_estatistico()
    relatorio_salas = calcular_relatorio_por_sala()
    return render_template('relatorio_geral.html', resumo=estatisticas_resumo, salas=relatorio_salas,
                           data_geracao=datetime.now(TZ_SAO).strftime('%d/%m/%Y %H:%M:%S'))


@app.route('/relatorio_tutor')
def relatorio_tutor():
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    # Placeholder de cálculo; você pode expandir conforme necessidade
    relatorio = {'TUTOR A': {'total': 10, 'prazo': 8, 'fora': 1, 'nao': 1}}
    return render_template('relatorio_tutor.html', relatorio=relatorio, start=start_date_str, end=end_date_str)


# -------------------- Rota de tutoria (placeholder) --------------------
@app.route('/tutoria')
def tutoria():
    return render_template('tutoria.html')


# -------------------- Inicialização --------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)


