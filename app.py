# -------------------- Imports --------------------
import os
import pytz
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from supabase import create_client, Client

# -------------------- Configurações gerais --------------------
TZ_SAO = pytz.timezone("America/Sao_Paulo")
FORMATO_ENTRADA = "%Y-%m-%dT%H:%M:%S%z"  # Formato ISO padrão do Supabase
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

# -------------------- Mapeamento de colunas --------------------
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
    url = "https://rimuhgulxliduugenxro.supabase.co"  # <-- substitua pelo seu URL real
    key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."   # <-- substitua pela sua chave real
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
# -------------------- Rotas principais --------------------

# -------------------- Rotas principais --------------------

@app.route("/")
def home():
    """Redireciona para a página principal."""
    return redirect(url_for("index"))


@app.route("/index", methods=["GET", "POST"])
def index():
    """Página principal com filtros de Tutor e Status."""
    global _df_cache

    # Carrega cache ou busca do Supabase
    if _df_cache is None:
        _df_cache = obter_dados_supabase("ocorrencias")

    dados = _df_cache or []

    # Filtros vindos da URL (ex: ?tutor=MARIA&status=ABERTA)
    filtro_tutor = request.args.get("tutor", "").strip()
    filtro_status = request.args.get("status", "").strip()

    # Aplica filtro de Tutor
    if filtro_tutor:
        dados = [d for d in dados if d.get("TUTOR") == filtro_tutor]

    # Aplica filtro de Status
    if filtro_status:
        dados = [d for d in dados if d.get("STATUS") == filtro_status]

    # Gera listas únicas de opções para os selects
    tutores_unicos = sorted(set(d.get("TUTOR") for d in _df_cache if d.get("TUTOR")))
    status_unicos = sorted(set(d.get("STATUS") for d in _df_cache if d.get("STATUS")))

    return render_template(
        "index.html",
        dados=dados,
        tutores=tutores_unicos,
        status=status_unicos,
        filtro_tutor=filtro_tutor,
        filtro_status=filtro_status
    )


@app.route("/nova", methods=["GET", "POST"])
def nova():
    """Página para registrar nova ocorrência."""
    global _alunos_cache, _professores_cache, _salas_cache

    # Carrega tabelas auxiliares
    if _alunos_cache is None:
        _alunos_cache = obter_dados_supabase("Alunos")
    if _professores_cache is None:
        _professores_cache = obter_dados_supabase("Professores")
    if _salas_cache is None:
        _salas_cache = obter_dados_supabase("Salas")

    if request.method == "POST":
        try:
            dados = {
                "PROFESSOR": request.form.get("professor"),
                "SALA": request.form.get("sala"),
                "ALUNO": request.form.get("aluno"),
                "TUTOR": request.form.get("tutor"),
                "DESCRICAO_OCORRENCIA": request.form.get("descricao"),
                "ATENDIMENTO_PROFESSOR": request.form.get("atendimento_professor"),
                "DCO": datetime.now(TZ_SAO).strftime(FORMATO_SAIDA_DATA),
                "HCO": datetime.now(TZ_SAO).strftime(FORMATO_SAIDA_HORA),
                "STATUS": "Aberta",
                "FT": "NÃO",
                "FC": "NÃO",
                "FG": "NÃO"
            }

            supabase = conectar_supabase()
            if supabase:
                supabase.table("ocorrencias").insert(dados).execute()
                limpar_caches()
                flash("✅ Ocorrência registrada com sucesso!", "success")
                return redirect(url_for("index"))
            else:
                flash("❌ Erro ao conectar no banco de dados.", "danger")

        except Exception as e:
            flash(f"❌ Erro ao salvar ocorrência: {e}", "danger")

    return render_template(
        "nova.html",
        alunos=_alunos_cache or [],
        professores=_professores_cache or [],
        salas=_salas_cache or []
    )


# -------------------- Erros --------------------
@app.errorhandler(404)
def pagina_nao_encontrada(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def erro_interno(e):
    return render_template("500.html", erro=e), 500


# -------------------- Execução local --------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
