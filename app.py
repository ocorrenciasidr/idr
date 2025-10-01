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
from google.oauth2.service_account import Credentials

def conectar_sheets():
    try:
        creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
        if not creds_json:
            print("ERRO: Variável de ambiente GOOGLE_SHEETS_CREDENTIALS não configurada.")
            return None

        creds_dict = json.loads(creds_json)
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 
                  'https://www.googleapis.com/auth/drive']

        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)

        SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', 'xxxxxxxxxxxxxxxx')  
        spreadsheet = gc.open_by_key(SHEET_ID)
        return spreadsheet
    except Exception as e:
        print(f"Erro ao conectar com Google Sheets: {e}")
        return None

# Imports para Geração de PDF e Gráficos (necessita de `fpdf` e `matplotlib`)
from fpdf import FPDF
try:
    import matplotlib.pyplot as plt
    plt.switch_backend('Agg') # Usa backend que não precisa de display gráfico
    HAS_MATPLOTLIB = True
except ImportError:
    plt = None
    HAS_MATPLOTLIB = False
    # print("Aviso: Matplotlib não instalado. Gráficos não serão gerados.") # Comentado para não poluir o console

# Tenta importar ZoneInfo (Python 3.9+) ou usa timezone fallback
try:
    from zoneinfo import ZoneInfo
    TZ_SAO = ZoneInfo("America/Sao_Paulo")
except Exception:
    # Fallback para versões mais antigas
    TZ_SAO = timezone(timedelta(hours=-3))

app = Flask(__name__)
# Chave secreta: Mantenha esta chave secreta segura em produção.
app.secret_key = os.environ.get('SECRET_KEY', 'sua_chave_secreta_padrao') 

# -------------------- Configuração do Google Sheets --------------------
def conectar_sheets():
    # Carrega credenciais do JSON
    try:
        # A chave de credencial deve ser fornecida como uma variável de ambiente (string JSON)
        creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
        if not creds_json:
             print("ERRO: Variável de ambiente GOOGLE_SHEETS_CREDENTIALS não configurada.")
             return None
             
        creds_dict = json.loads(creds_json)
        
        # O gspread espera um arquivo de credenciais. Vamos criar um temp_file 
        # (Em ambientes de nuvem, é comum carregar diretamente do JSON string)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        gc = gspread.authorize(creds)
        
        # ID da sua planilha (substitua pelo ID real)
        SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '1qB6K243zI367T19Q0-z_p93K2Y2n0lT0sX8Xj3rY') # ID Exemplo, USAR O REAL
        
        # Abre a planilha pelo ID
        spreadsheet = gc.open_by_key(SHEET_ID)
        return spreadsheet
    except Exception as e:
        print(f"Erro ao conectar com Google Sheets: {e}")
        return None

# Variáveis globais para caches
_df_cache = None
_alunos_cache = None

def carregar_dados_alunos():
    global _alunos_cache
    if _alunos_cache is not None:
        return _alunos_cache

    # 1. Conecta
    sh = conectar_sheets()
    if not sh:
        return pd.DataFrame({'Sala': [], 'Aluno': [], 'Tutor': []})

    # 2. Seleciona a aba 'Alunos'
    try:
        ws_alunos = sh.worksheet('Alunos')
        data = ws_alunos.get_all_records()
        df_alunos = pd.DataFrame(data)
    except Exception as e:
        print(f"Erro ao ler a aba 'Alunos': {e}")
        return pd.DataFrame({'Sala': [], 'Aluno': [], 'Tutor': []})

    # Normaliza as colunas (importante para evitar erros de maiúsculas/minúsculas/espaços)
    df_alunos.columns = ['Sala', 'Aluno', 'Tutor']
    
    # Preenche NaN/vazios no Tutor com 'SEM TUTOR'
    df_alunos['Tutor'] = df_alunos['Tutor'].fillna('SEM TUTOR').str.strip().str.upper()
    df_alunos['Aluno'] = df_alunos['Aluno'].str.strip()
    df_alunos['Sala'] = df_alunos['Sala'].str.strip()
    
    _alunos_cache = df_alunos
    return df_alunos


def carregar_dados():
    global _df_cache
    if _df_cache is not None:
        return _df_cache

    sh = conectar_sheets()
    if not sh:
        return pd.DataFrame()

    try:
        ws_ocorrencias = sh.worksheet('Dados') # Nome da aba principal de dados
        data = ws_ocorrencias.get_all_records()
        df = pd.DataFrame(data)
    except Exception as e:
        print(f"Erro ao ler a aba 'Dados': {e}")
        return pd.DataFrame()

    # Limpeza/conversão de dados (Essencial para garantir tipos corretos)
    if 'ID' in df.columns:
        df['ID'] = pd.to_numeric(df['ID'], errors='coerce').fillna(0).astype(int)
    if 'Nº Ocorrência' in df.columns:
        df['Nº Ocorrência'] = pd.to_numeric(df['Nº Ocorrência'], errors='coerce').fillna(0).astype(int)
    else:
        # Se 'Nº Ocorrência' não existe, usa 'ID' (assumindo que são a mesma coisa)
        df['Nº Ocorrência'] = df['ID']

    df['DCO'] = pd.to_datetime(df['DCO'], errors='coerce', dayfirst=True)
    df['DT'] = pd.to_datetime(df['DT'], errors='coerce', dayfirst=True)
    df['DC'] = pd.to_datetime(df['DC'], errors='coerce', dayfirst=True)
    df['DG'] = pd.to_datetime(df['DG'], errors='coerce', dayfirst=True)

    # Preenchimento de colunas de texto para evitar NaN no filtro
    text_cols = ['Professor', 'Sala', 'Aluno', 'Tutor', 'Descrição da Ocorrência', 
                 'Atendimento Professor', 'ATT', 'ATC', 'ATG', 'Status']
    for col in text_cols:
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].astype(str).str.strip().fillna('')

    _df_cache = df
    return df

def get_proximo_id(ws):
    # Encontra o próximo ID disponível (Última linha + 1)
    try:
        # Pega a coluna 'ID' (ou 'Nº Ocorrência') inteira
        ids = ws.col_values(1)[1:] # Ignora o cabeçalho
        if not ids:
            return 1 # Se a planilha está vazia (exceto cabeçalho)
        
        # Converte para números e encontra o máximo
        max_id = 0
        for id_str in ids:
            try:
                max_id = max(max_id, int(id_str))
            except ValueError:
                continue # Ignora valores não numéricos
        
        return max_id + 1
    except Exception as e:
        print(f"Erro ao obter próximo ID: {e}")
        return 9999 # Valor de fallback seguro

# Função utilitária para obter listas únicas
def get_listas_dropdowns(df, df_alunos):
    salas = sorted(df_alunos['Sala'].unique().tolist())
    tutores = sorted(df_alunos['Tutor'].unique().tolist())
    professores = sorted(df['Professor'].unique().tolist())
    return salas, tutores, professores

# Função utilitária para gerar o gráfico (necessária para a rota relatorio_tutor)
def gerar_grafico_barras(relatorio, tutor):
    if not HAS_MATPLOTLIB:
        return None

    labels = ['No Prazo', 'Fora do Prazo', 'Não Respondido', 'Aberto']
    values = [relatorio['prazo'], relatorio['fora'], relatorio['nao'], relatorio['aberto']]
    
    # Filtra valores zero para o gráfico
    filtered_labels = [labels[i] for i, v in enumerate(values) if v > 0]
    filtered_values = [v for v in values if v > 0]

    if not filtered_values:
        return None

    # Cores personalizadas
    colors = ['#4CAF50', '#FF9800', '#F44336', '#2196F3'] # Verde, Laranja, Vermelho, Azul
    filtered_colors = [colors[i] for i, v in enumerate(values) if v > 0]

    plt.figure(figsize=(8, 6))
    plt.bar(filtered_labels, filtered_values, color=filtered_colors)
    plt.title(f'Desempenho do Tutor: {tutor}', fontsize=16)
    plt.ylabel('Número de Ocorrências', fontsize=12)
    plt.xlabel('Status de Resposta', fontsize=12)
    
    # Adiciona os valores nas barras
    for i, v in enumerate(filtered_values):
        plt.text(i, v + 0.1, str(v), ha='center', fontsize=10, fontweight='bold')

    plt.grid(axis='y', linestyle='--', alpha=0.7)
    plt.xticks(rotation=15, ha='right')
    plt.tight_layout()

    buffer = BytesIO()
    plt.savefig(buffer, format='png')
    plt.close() # Fecha a figura para liberar memória
    buffer.seek(0)
    return buffer

def gerar_pdf_tutor(relatorio, tutor, registros, grafico_buffer):
    # Código de geração de PDF para o relatório do Tutor (omito a implementação complexa do ReportLab
    # para usar FPDF, conforme o outro snippet)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=16, style='B')
    pdf.cell(0, 10, txt=f"Relatório de Atendimento do Tutor: {tutor}", ln=True, align='C')
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, txt="Período: " + request.args.get('start', 'N/D') + " a " + request.args.get('end', 'N/D'), ln=True, align='C')
    pdf.ln(5)

    # Tabela de resumo
    pdf.set_font("Arial", size=12, style='B')
    pdf.cell(40, 7, "Total", 1, 0, 'C')
    pdf.cell(40, 7, "No Prazo", 1, 0, 'C')
    pdf.cell(40, 7, "Fora do Prazo", 1, 0, 'C')
    pdf.cell(40, 7, "Não Respondido", 1, 1, 'C')
    
    pdf.set_font("Arial", size=12)
    pdf.cell(40, 7, str(relatorio['total']), 1, 0, 'C')
    pdf.cell(40, 7, str(relatorio['prazo']), 1, 0, 'C')
    pdf.cell(40, 7, str(relatorio['fora']), 1, 0, 'C')
    pdf.cell(40, 7, str(relatorio['nao']), 1, 1, 'C')
    
    pdf.ln(10)

    # Gráfico
    if grafico_buffer:
        # Decodifica e insere o gráfico
        pdf.set_font("Arial", size=14, style='B')
        pdf.cell(0, 10, "Distribuição de Status de Resposta", ln=True, align='L')
        pdf.image(grafico_buffer, x=30, y=pdf.get_y(), w=150, type='PNG')
        pdf.ln(150) # Avança para o próximo bloco

    # Lista de Ocorrências
    if not registros.empty:
        pdf.set_font("Arial", size=14, style='B')
        pdf.cell(0, 10, "Detalhes das Ocorrências Atendidas", ln=True, align='L')
        pdf.ln(2)

        for _, row in registros.iterrows():
            pdf.set_font("Arial", size=10, style='B')
            pdf.cell(0, 5, f"Ocorrência Nº {row['Nº Ocorrência']} - Aluno: {row['Aluno']} ({row['Sala']})", ln=True)
            pdf.set_font("Arial", size=10)
            
            # Formata o status para o PDF
            status_map = {'prazo': 'RESPONDIDA NO PRAZO', 'fora': 'RESPONDIDA FORA DO PRAZO', 'aberto': 'ABERTA'}
            status_display = status_map.get(row['StatusTutor'], 'ERRO')
            
            pdf.multi_cell(0, 5, f"Status do Tutor: {status_display} | Criação: {row['DCO'].strftime('%d/%m/%Y')} | Prazo Resposta: {row['DT'].strftime('%d/%m/%Y') if pd.notna(row['DT']) else 'N/D'}")
            
            atendimento = row['ATT']
            if atendimento:
                pdf.set_font("Arial", size=10, style='I')
                pdf.multi_cell(0, 5, f"Resposta do Tutor: {atendimento}")
            
            pdf.ln(3)

@app.route("/gerar_pdf")
def gerar_pdf():
    pdf_output = BytesIO()
    pdf_bytes = pdf.output(dest='S').encode('latin1')  # Gera como bytes
    pdf_output.write(pdf_bytes)
    pdf_output.seek(0)
    return send_file(
        pdf_output,
        as_attachment=True,
        download_name="relatorio.pdf",
        mimetype="application/pdf"
    )

# -------------------- ROTAS --------------------

@app.route("/")
def home():
    # Rota para a página principal com os links
    return render_template("home.html")

@app.route("/index")
def index():
    df = carregar_dados()
    df_alunos = carregar_dados_alunos()
    
    salas, tutores, professores = get_listas_dropdowns(df, df_alunos)

    # Filtros
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
    
    # Conversão para formato de lista de dicionários para o template
    ocorrencias_lista = ocorrencias_filtradas.sort_values(by='Nº Ocorrência', ascending=False).to_dict('records')

    # Status únicos para o filtro de dropdown (incluindo 'Todos')
    status_opcoes = ['Todos'] + df['Status'].unique().tolist()

    return render_template("Index.html",
                           ocorrencias=ocorrencias_lista,
                           tutores=tutores,
                           salas=salas,
                           status_opcoes=status_opcoes,
                           tutor_sel=filtro_tutor,
                           sala_sel=filtro_sala,
                           status_sel=filtro_status)

@app.route("/nova", methods=["GET", "POST"])
def nova():
    df_alunos = carregar_dados_alunos()
    salas_unicas = sorted(df_alunos['Sala'].unique().tolist())

    if request.method == "POST":
        data = request.form
        
        # 1. Conecta e pega a aba de dados
        sh = conectar_sheets()
        if not sh:
            flash("Erro ao conectar com Google Sheets. Tente novamente.", "danger")
            return redirect(url_for("nova"))
            
        try:
            ws = sh.worksheet('Dados')
            
            # 2. Gera o próximo ID
            next_id = get_proximo_id(ws)
            
            # Obtém a data/hora atual no fuso horário de São Paulo
            now_local = datetime.now(TZ_SAO)
            dco = now_local.strftime('%Y-%m-%d')
            hco = now_local.strftime('%H:%M:%S')

            # Cria a linha com os dados do formulário
            nova_linha = [
                next_id, # ID
                dco, # DCO (Data Criação Ocorrência)
                hco, # HCO (Hora Criação Ocorrência)
                data.get('professor', ''),
                data.get('sala', ''),
                data.get('aluno', ''),
                data.get('tutor', ''),
                data.get('descricao', ''),
                '', # Atendimento Professor
                '', # ATT (Atendimento Tutor)
                '', # ATC (Atendimento Coordenação)
                '', # ATG (Atendimento Gestão)
                'NÃO', # FT (Fechamento Tutor)
                'NÃO', # FC (Fechamento Coordenação)
                'NÃO', # FG (Fechamento Gestão)
                '', # DT (Data Tutor)
                '', # DC (Data Coordenação)
                '', # DG (Data Gestão)
                'Aberta' # Status (Padrão)
            ]

            # 3. Inserir na planilha
            ws.append_row(nova_linha)
            # Limpa o cache após a inserção
            global _df_cache, _alunos_cache
            _df_cache = None
            _alunos_cache = None
            
            flash(f"Ocorrência Nº {next_id} registrada com sucesso!", "success")

        except Exception as e:
            flash(f"Erro ao salvar: {e}", "danger")
            print(f"Erro no POST /nova: {e}")

        return redirect(url_for("index"))

    # GET request
    return render_template("nova.html", salas=salas_unicas)

@app.route("/api/alunos_sala/<sala>")
def api_alunos_sala(sala):
    df_alunos = carregar_dados_alunos()
    
    # Filtra alunos pela sala
    lista_alunos = df_alunos[df_alunos['Sala'].str.lower() == sala.lower()] \
                        .sort_values(by='Aluno') \
                        [['Aluno', 'Tutor']] \
                        .to_dict('records')
    
    return jsonify(lista_alunos)

@app.route("/editar/<oid>", methods=["GET", "POST"])
def editar(oid):
    df = carregar_dados()
    try:
        ocorrencia = df[df["Nº Ocorrência"] == int(oid)].to_dict('records')[0]
    except (IndexError, ValueError):
        flash(f"Ocorrência {oid} não encontrada.", "danger")
        return redirect(url_for("index"))

    # Lógica de Permissões (Simulada - deve vir da sua lógica de autenticação real)
    # Aqui, você precisa saber o PAPEL do usuário logado.
    # Como não temos um sistema de login, vamos manter a lógica de permissões simples
    # baseada em uma suposição de papel para testes (ex: 'gestao')
    
    # Exemplo: Acessou com a senha 'idrgestao' -> permissão total
    # Para o teste, vamos simular que o papel de acesso é passado como um parâmetro
    papel_acesso = request.args.get('papel', 'gestao') # 'lapis' é o valor padrão que vem do Index.html
    
    permissoes = {
        'professor': papel_acesso == 'professor' or papel_acesso == 'gestao',
        'tutor': papel_acesso == 'tutor' or papel_acesso == 'gestao',
        'coord': papel_acesso == 'coord' or papel_acesso == 'gestao',
        'gestao': papel_acesso == 'gestao',
    }
    
    if request.method == "POST":
        data = request.form
        
        # 1. Conecta e pega a aba de dados
        sh = conectar_sheets()
        if not sh:
            flash("Erro ao conectar com Google Sheets. Tente novamente.", "danger")
            return redirect(url_for("editar", oid=oid, papel=papel_acesso))
        
        try:
            ws = sh.worksheet('Dados')
            
            # 2. Encontra a linha da ocorrência (gspread usa índice 1-based)
            # Nota: O gspread.get_all_records() ignora o cabeçalho, então a linha 'N' no DataFrame é a linha 'N+2' no Sheet
            row_index = df[df["Nº Ocorrência"] == int(oid)].index.values[0] + 2
            
            # Campos de Atendimento
            at_prof = data.get('at_professor', '')
            at_tutor = data.get('at_tutor', '')
            at_coord = data.get('at_coord', '')
            at_gestao = data.get('at_gestao', '')

            # Atualiza os campos se tiver permissão e o campo foi alterado
            updates = {}
            now_local = datetime.now(TZ_SAO).strftime('%Y-%m-%d %H:%M:%S')
            
            # Colunas na Planilha (Assumindo a ordem: ID(1), DCO(2), HCO(3), Professor(4), Sala(5), Aluno(6), Tutor(7), Descrição(8), 
            # Atendimento Professor(9), ATT(10), ATC(11), ATG(12), FT(13), FC(14), FG(15), DT(16), DC(17), DG(18), Status(19))
            
            # Professor
            if permissoes['professor'] and at_prof != ocorrencia.get('Atendimento Professor', ''):
                updates[9] = at_prof
            
            # Tutor (ATT)
            if permissoes['tutor'] and at_tutor != ocorrencia.get('ATT', ''):
                updates[10] = at_tutor
                # Atualiza Data Tutor (DT) e FT (Fechamento Tutor)
                updates[16] = now_local
                updates[13] = 'SIM' # FT
            
            # Coordenação (ATC)
            if permissoes['coord'] and at_coord != ocorrencia.get('ATC', ''):
                updates[11] = at_coord
                # Atualiza Data Coordenação (DC) e FC (Fechamento Coordenação)
                updates[17] = now_local
                updates[14] = 'SIM' # FC

            # Gestão (ATG)
            if permissoes['gestao'] and at_gestao != ocorrencia.get('ATG', ''):
                updates[12] = at_gestao
                # Atualiza Data Gestão (DG) e FG (Fechamento Gestão)
                updates[18] = now_local
                updates[15] = 'SIM' # FG

            # Aplica as atualizações no Sheets
            if updates:
                for col_index, value in updates.items():
                    ws.update_cell(row_index, col_index, value)
                
                # Regra de Status Simples (Pode precisar de mais refinamento na sua lógica real)
                # Se algum campo ATT, ATC ou ATG for preenchido, marca como ASSINADA/FINALIZADA
                novo_status = ocorrencia.get('Status', 'Aberta')
                if at_tutor or at_coord or at_gestao:
                    novo_status = 'ASSINADA'
                if at_tutor and at_coord and at_gestao:
                     novo_status = 'Finalizada'

                # Atualiza o status na planilha (Coluna 19)
                ws.update_cell(row_index, 19, novo_status)
                
                # Limpa o cache após a atualização
                global _df_cache
                _df_cache = None
                
                flash(f"Ocorrência Nº {oid} salva e atualizada como '{novo_status}'!", "success")
            else:
                flash("Nenhuma alteração foi feita ou você não tem permissão para editar os campos.", "warning")
            
        except Exception as e:
            flash(f"Erro ao salvar: {e}", "danger")
            print(f"Erro no POST /editar: {e}")

        return redirect(url_for("index"))


    # GET request
    return render_template("editar.html", ocorrencia=ocorrencia, permissoes=permissoes)


# --- ROTAS DE RELATÓRIOS ---

@app.route("/relatorio_inicial")
def relatorio_inicial():
    # Tela de escolha dos tipos de relatório
    return render_template("relatorio_inicial.html")

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

    # A lista de alunos para o dropdown 'aluno'
    alunos_na_sala = []
    if sala_sel:
        alunos_na_sala = df_alunos[df_alunos['Sala'].str.lower() == sala_sel.lower()] \
                            .sort_values(by='Aluno')['Aluno'].tolist()

    return render_template("relatorio_aluno.html", 
                           salas=salas, 
                           sala_sel=sala_sel, 
                           alunos_na_sala=alunos_na_sala, # Passa a lista filtrada
                           aluno_sel=aluno_sel,
                           ocorrencias=ocorrencias_aluno)

@app.route("/gerar_pdf_aluno", methods=["POST"])
def gerar_pdf_aluno():
    # A rota agora espera que o nome do aluno e os IDs das ocorrências selecionadas
    # venham do formulário POST.
    aluno = request.form.get("aluno")
    ocorrencias_ids = request.form.getlist("ocorrencias")
    
    if not aluno or not ocorrencias_ids:
        flash("Selecione um aluno e pelo menos uma ocorrência para gerar o PDF.", "warning")
        return redirect(url_for('relatorio_aluno'))
        
    df = carregar_dados() # LÊ DO SHEETS

    try:
        ocorrencias = df[df['Nº Ocorrência'].astype(str).isin(ocorrencias_ids)].sort_values(by='Nº Ocorrência', ascending=True)
    except Exception:
        flash("Erro ao filtrar ocorrências. Verifique os dados.", "danger")
        return redirect(url_for('relatorio_aluno'))
    
    if ocorrencias.empty:
        flash(f"Nenhuma ocorrência encontrada para os IDs selecionados do aluno {aluno}.", "danger")
        return redirect(url_for('relatorio_aluno'))

    # Geração do PDF
    from fpdf import FPDF
    pdf = FPDF('P', 'mm', 'A4')
    pdf.add_page()
    pdf.set_font("Arial", size=16, style='B')
    pdf.cell(0, 10, txt=f"Relatório de Ocorrências: {aluno}", ln=True, align='C')
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, txt=f"Sala: {ocorrencias['Sala'].iloc[0]} | Tutor: {ocorrencias['Tutor'].iloc[0]}", ln=True, align='C')
    pdf.ln(5)

    for i, row in ocorrencias.iterrows():
        # Informações da Ocorrência
        pdf.set_font("Arial", size=12, style='B')
        pdf.cell(0, 7, txt=f"Ocorrência Nº {row['Nº Ocorrência']} - Data: {row['DCO'].strftime('%d/%m/%Y')}", ln=True, border=1)
        
        pdf.set_font("Arial", size=10, style='I')
        pdf.multi_cell(0, 5, txt=f"Professor: {row['Professor']} | Status: {row['Status']}")
        
        pdf.set_font("Arial", size=10)
        pdf.multi_cell(0, 5, txt=f"Descrição: {row['Descrição da Ocorrência']}", border=0)
        
        # Atendimentos (Títulos em negrito)
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
        
        pdf.ln(5) # Espaçamento entre ocorrências

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
    
    # Validação de datas
    try:
        if data_inicio_str:
            data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO)
        if data_fim_str:
            # Adiciona 1 dia para incluir o dia final no filtro
            data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO) + timedelta(days=1)
    except ValueError:
        flash("Formato de data inválido.", "danger")
        return render_template("relatorio_geral.html", ocorrencias=None, data_inicio=data_inicio_str, data_fim=data_fim_str, grafico_base64=None)


    df_filtrado = df.copy()

    # Filtra por data
    if data_inicio:
        df_filtrado = df_filtrado[df_filtrado['DCO'] >= data_inicio.replace(tzinfo=None)]
    if data_fim:
        df_filtrado = df_filtrado[df_filtrado['DCO'] < data_fim.replace(tzinfo=None)]

    # Cálculo do Prazo (assumindo a coluna 'DT' para resposta do Tutor)
    # Define o prazo de 7 dias úteis (ou apenas 7 dias corridos, simplificando) a partir do DCO
    # Para simplicidade, usamos 7 dias corridos, como visto no `relatorio_tutor`.
    def calcular_prazo(row):
        # A lógica mais complexa de prazo (como a utilizada em relatorio_tutor) deve ser aplicada aqui
        # Para fins de demonstração, vamos apenas listar as ocorrências no geral.
        if row['Status'] == 'Aberta':
            return 'Pendente'
        
        # Simula o cálculo de prazo simples (Exemplo: 7 dias corridos)
        data_criacao = row['DCO']
        data_resposta = row['DT'] if pd.notna(row['DT']) else row['DC'] if pd.notna(row['DC']) else row['DG']
        
        if pd.isna(data_resposta):
            return 'Não Respondido' # Mesmo se o status não for 'Aberta', se não tiver data, pode ser um erro de registro

        prazo_limite = data_criacao + timedelta(days=7)
        
        if data_resposta.date() <= prazo_limite.date():
            return 'No Prazo'
        else:
            return 'Fora do Prazo'

    df_filtrado['Prazo'] = df_filtrado.apply(calcular_prazo, axis=1)

    # Gera o Gráfico (Estatística de Status)
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
    # Esta rota usaria o df_filtrado e o grafico_base64 da rota GET para gerar o PDF
    # Para fins de simplificação, precisaria re-executar a lógica de filtragem/cálculo
    # ou passar os parâmetros via hidden fields (que é mais comum em Flask).
    
    # Assumimos que os parâmetros 'data_inicio' e 'data_fim' são passados no POST
    data_inicio_str = request.form.get('data_inicio')
    data_fim_str = request.form.get('data_fim')
    
    # Re-executar a lógica de filtragem (simplificada aqui para evitar repetição massiva)
    df = carregar_dados()
    df_filtrado = df.copy()

    # Aplica a filtragem por data (lógica omitida, assumindo que foi feita corretamente acima)
    # ... (aqui entraria a lógica de filtragem e cálculo do prazo) ...
    # Exemplo: Apenas para ter dados
    if data_inicio_str:
         start = datetime.strptime(data_inicio_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO)
         df_filtrado = df_filtrado[df_filtrado['DCO'] >= start.replace(tzinfo=None)]
    
    # Criação do PDF FPDF (simplificada)
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
        for _, row in df_filtrado[['Nº Ocorrência', 'Aluno', 'Sala', 'Status']].iterrows():
            pdf.cell(20, 6, str(row['Nº Ocorrência']), 1, 0, 'C')
            pdf.cell(50, 6, row['Aluno'], 1, 0, 'L')
            pdf.cell(30, 6, row['Sala'], 1, 0, 'C')
            pdf.cell(40, 6, row['Status'], 1, 0, 'C')
            # Simplificação, o Prazo deveria ser recalculado ou armazenado
            pdf.cell(40, 6, "N/A", 1, 1, 'C') 
            
    output = BytesIO()
    pdf.output(output)
    output.seek(0)
    return send_file(output, download_name="relatorio_geral.pdf", as_attachment=True)


@app.route("/relatorio_tutor")
def relatorio_tutor():
    # ... (Conteúdo da função relatorio_tutor) ...
    df = carregar_dados()
    df_alunos = carregar_dados_alunos()
    
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    
    relatorio_tutor_dados = {}
    grafico_base64 = None

    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO)
            # Adiciona 1 dia para incluir o dia final no filtro
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(tzinfo=TZ_SAO) + timedelta(days=1)
        except ValueError:
            flash("Formato de data inválido.", "danger")
            return render_template("relatorio_tutor.html", relatorio=relatorio_tutor_dados, grafico_base64=None)
            
        df_filtrado = df.copy()
        
        # Filtra por data de criação (DCO)
        df_filtrado = df_filtrado[
            (df_filtrado['DCO'] >= start_date.replace(tzinfo=None)) & 
            (df_filtrado['DCO'] < end_date.replace(tzinfo=None))
        ]
        
        # Merge com dados de alunos para garantir que todos os tutores estão na lista
        df_merge = pd.merge(df_alunos[['Tutor']].drop_duplicates(), 
                            df_filtrado, 
                            on='Tutor', 
                            how='left', 
                            indicator=True)
        
        # Garante que as colunas de data e atendimento existam
        if 'DT' not in df_merge.columns: df_merge['DT'] = pd.NaT
        if 'ATT' not in df_merge.columns: df_merge['ATT'] = ''

        # Classificação do status de atendimento do tutor
        def classificar_status_tutor(row):
            if pd.isna(row['Nº Ocorrência']):
                 return 'nao_req' # Tutor existe, mas não teve ocorrência no período
            
            if not row['ATT'] or pd.isna(row['DT']):
                return 'nao' # Não respondido (independente do status geral)

            # Prazo: 7 dias corridos para resposta (ajuste para 7 dias úteis se necessário)
            prazo_limite = row['DCO'] + timedelta(days=7)
            
            if row['DT'] <= prazo_limite:
                return 'prazo'
            else:
                return 'fora'

        df_merge['StatusTutor'] = df_merge.apply(classificar_status_tutor, axis=1)

        # Agrupa por tutor e conta os status
        relatorio = df_merge[df_merge['StatusTutor'] != 'nao_req'].groupby('Tutor')['StatusTutor'].value_counts().unstack(fill_value=0)
        
        # Prepara o dicionário de saída
        relatorio_tutor_dados = {}
        for tutor in df_merge['Tutor'].unique():
            counts = relatorio.loc[tutor] if tutor in relatorio.index else pd.Series(0, index=['prazo', 'fora', 'nao', 'aberto'])
            total_requisitado = counts.sum()
            relatorio_tutor_dados[tutor] = {
                'total': int(total_requisitado),
                'prazo': int(counts.get('prazo', 0)),
                'fora': int(counts.get('fora', 0)),
                'nao': int(counts.get('nao', 0)),
                # 'aberto': int(counts.get('aberto', 0)) # Status 'aberto' aqui pode ser confuso, 'nao' já cobre o não-atendido
            }

        # Gera o gráfico geral de desempenho (opcional)
        if HAS_MATPLOTLIB and not relatorio.empty:
            total_por_tutor = relatorio[['prazo', 'fora', 'nao']].sum(axis=1)
            
            plt.figure(figsize=(12, 6))
            total_por_tutor.sort_values(ascending=False).plot(kind='bar', color='#4fc3f7') # Cor azul claro
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
    
    df = carregar_dados()
    df_alunos = carregar_dados_alunos()
    
    # Re-executar a lógica de filtragem e classificação (igual à da rota GET)
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
        if not row['ATT'] or pd.isna(row['DT']):
            return 'nao'
        prazo_limite = row['DCO'] + timedelta(days=7)
        return 'prazo' if row['DT'] <= prazo_limite else 'fora'

    df_filtrado['StatusTutor'] = df_filtrado.apply(classificar_status_tutor_pdf, axis=1)
    
    # Filtra apenas o tutor selecionado e as ocorrências que exigiram atendimento (não 'nao_req')
    df_tutor = df_filtrado[df_filtrado['Tutor'] == tutor]
    
    # Prepara o relatório final do tutor (resumo)
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

    # Gera o gráfico para o PDF
    img_buffer = None
    if HAS_MATPLOTLIB and relatorio_final['total'] > 0:
        # Usa a função auxiliar (assumindo que existe)
        labels = ['No Prazo', 'Fora do Prazo', 'Não Respondido']
        values = [relatorio_final['prazo'], relatorio_final['fora'], relatorio_final['nao']]
        
        filtered_labels = [labels[i] for i, v in enumerate(values) if v > 0]
        filtered_values = [v for v in values if v > 0]
        colors = ['#4CAF50', '#FF9800', '#F44336']
        filtered_colors = [colors[i] for i, v in enumerate(values) if v > 0]

        if filtered_values:
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

            img_buffer = BytesIO()
            plt.savefig(img_buffer, format='png')
            plt.close()
            img_buffer.seek(0)
        
    registros_relatorio = df_tutor.sort_values(by='DCO', ascending=False)
    
    # Gera o PDF
    pdf_output = gerar_pdf_tutor(relatorio_final, tutor, registros_relatorio, img_buffer)
    
    return send_file(
        pdf_output, 
        download_name=f"relatorio_tutor_{tutor}_{start_date_str}_a_{end_date_str}.pdf", 
        as_attachment=True, 
        mimetype='application/pdf'
    )

# NOVO: Rota para a lista de Tutores e Tutorados
@app.route("/relatorio_tutoraluno")
def relatorio_tutoraluno():
    df_alunos = carregar_dados_alunos()
    
    # 1. Agrupar os alunos pelo nome do Tutor
    # O Tutor 'SEM TUTOR' é incluído por padrão na função carregar_dados_alunos
    dados_agrupados = df_alunos.groupby('Tutor').apply(lambda x: x[['Aluno', 'Sala']].to_dict('records')).to_dict()
    
    # O resultado é um dicionário: {'Tutor X': [{'Aluno': 'A', 'Sala': 'S'}, ...], 'Tutor Y': [...]}
    
    # 2. Renderizar o template
    return render_template("relatorio_tutoraluno.html", dados=dados_agrupados)


# Rota para a página de Tutoria (atualmente placeholder)
@app.route("/tutoria")
def tutoria():
    # Rota para a página de Tutoria (placeholder)
    return render_template("tutoria.html")

# Rota para abrir pendência (uso interno) - Mantida para compatibilidade
@app.route("/abrir_pendencia/<oid>/<papel>")
def abrir_pendencia(oid, papel):
    # Esta rota é um redirecionamento simples (pode ser ajustada para sua lógica real)
    # Assumindo que a rota 'editar' já faz a validação e definição de permissões
    return redirect(url_for('editar', oid=oid, papel=papel))
    
if __name__ == "__main__":
    # A porta é definida pelo ambiente de hospedagem, mas 5000 é comum localmente
    port = int(os.environ.get('PORT', 5000)) 
    app.run(host='0.0.0.0', port=port, debug=True)

