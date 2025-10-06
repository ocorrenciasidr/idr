import os
from datetime import datetime
import pandas as pd
from flask import Flask, render_template, request, redirect
from dateutil import parser as date_parser

from supabase_client import conectar_supabase  # Função que conecta ao Supabase

app = Flask(__name__)

TZ_SAO = 'America/Sao_Paulo'

def upperize_row_keys(row):
    """Transforma todas as chaves do dicionário em maiúsculas."""
    return {k.upper(): v for k, v in row.items()}

def carregar_dados_ocorrencias() -> list:
    supabase = conectar_supabase()
    if not supabase:
        return []

    try:
        resp = supabase.table("ocorrencias").select("*").execute()
        data = resp.data or []
        normalized = [upperize_row_keys(r) for r in data]
        df = pd.DataFrame(normalized)

        # Ordena por ID decrescente
        if 'ID' in df.columns:
            df = df.sort_values(by='ID', ascending=False)

        # Corrigir datas e horas
        for col in ['DCO', 'DT', 'DC', 'DG', 'HCO']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce', utc=True).dt.tz_convert(TZ_SAO)
                if col == 'DCO':
                    df['DCO'] = df['DCO'].dt.strftime('%d/%m/%Y')
                elif col == 'HCO':
                    df['HCO'] = df['HCO'].dt.strftime('%H:%M')

        # Status dinâmico
        df['DisplayStatus'] = ''
        df['DisplayColor'] = ''
        for idx, row in df.iterrows():
            ft_done = str(row.get('FT', '')).upper() == 'SIM'
            fc_done = str(row.get('FC', '')).upper() == 'SIM'
            fg_done = str(row.get('FG', '')).upper() == 'SIM'
            status = str(row.get('STATUS', '')).upper()

            if status == 'ASSINADA':
                df.at[idx, 'DisplayStatus'] = 'ASSINADA'
                df.at[idx, 'DisplayColor'] = 'success'
            elif not (ft_done and fc_done and fg_done):
                df.at[idx, 'DisplayStatus'] = 'ATENDIMENTO'
                df.at[idx, 'DisplayColor'] = 'danger'
            else:
                df.at[idx, 'DisplayStatus'] = 'FINALIZADA'
                df.at[idx, 'DisplayColor'] = 'warning'

        return df.to_dict(orient='records')
    except Exception as e:
        print("Erro ao carregar ocorrências:", e)
        return []

def carregar_lista_tabela(tabela_nome):
    """Carrega dados de qualquer tabela e transforma em lista de dicionários"""
    supabase = conectar_supabase()
    try:
        resp = supabase.table(tabela_nome).select("*").execute()
        data = resp.data or []
        return [upperize_row_keys(r) for r in data]
    except Exception as e:
        print(f"Erro ao carregar {tabela_nome}:", e)
        return []

@app.route('/')
def index():
    ocorrencias = carregar_dados_ocorrencias()
    return render_template('index.html', ocorrencias=ocorrencias)

@app.route('/nova', methods=['GET', 'POST'])
def nova_ocorrencia():
    supabase = conectar_supabase()
    alunos = carregar_lista_tabela('alunos')
    professores = carregar_lista_tabela('professores')
    salas = carregar_lista_tabela('salas')

    if request.method == 'POST':
        dados = request.form.to_dict()
        supabase.table('ocorrencias').insert(dados).execute()
        return redirect('/')
    return render_template('nova.html', alunos=alunos, professores=professores, salas=salas)

@app.route('/editar/<int:id>', methods=['GET', 'POST'])
def editar_ocorrencia(id):
    supabase = conectar_supabase()
    alunos = carregar_lista_tabela('alunos')
    professores = carregar_lista_tabela('professores')
    salas = carregar_lista_tabela('salas')

    if request.method == 'POST':
        dados = request.form.to_dict()
        supabase.table('ocorrencias').update(dados).eq('id', id).execute()
        return redirect('/')
    else:
        resp = supabase.table('ocorrencias').select("*").eq('id', id).execute()
        ocorrencia = resp.data[0] if resp.data else None
        return render_template('editar.html', ocorrencia=ocorrencia, alunos=alunos, professores=professores, salas=salas)

# Evita múltiplos app.run()
if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))
