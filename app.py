import os
import json
import base64
import hashlib
import calendar
import requests
import gspread
import streamlit as st
import plotly.graph_objects as go
from datetime import date, timedelta
from google.oauth2.service_account import Credentials
from collections import defaultdict

# ─── Configuração da página ──────────────────────────────────────────────────
st.set_page_config(page_title="Gestão de Fatura", page_icon="💳", layout="wide")

# ─── Versão ─────────────────────────────────────────────────────────────────
APP_VERSION = "1.9.2"

# ─── Constantes ─────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
NOMES_MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
               "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

# Nomes exatos das colunas na planilha de lançamentos
COL_DATA      = "Data da compra"
COL_DESCRICAO = "Descrição do gasto"
COL_VALOR     = "Valor do gasto/parcela"
COL_PARCELAS  = "Quantidade de parcelas [Parcelas]"

# Aba e colunas de assinaturas (mesma ordem das colunas no GSheets)
ABA_ASSINATURAS = "Assinaturas"
COLUNAS_ASSINATURAS = [
    "id", "descricao", "valor", "dia_do_mes", "periodicidade_meses",
    "status", "data_inicio", "data_ultimo_lancamento", "data_cancelamento",
]

# Aba e nomes de parâmetros
ABA_PARAMETROS = "Parametros"
PARAM_RENDA            = "renda_mensal_liquida"
PARAM_LIMITE_GASTOS    = "limite_gastos_pct"
PARAM_LIMITE_PARCELADOS = "limite_parcelados_pct"


# ─── Google Sheets ───────────────────────────────────────────────────────────
@st.cache_resource
def get_gspread_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        st.error("Variável de ambiente GOOGLE_CREDENTIALS não configurada.")
        st.stop()
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_planilha():
    """Abre a planilha uma única vez. Usado pelas funções de escrita."""
    client = get_gspread_client()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        st.error("Variável de ambiente GOOGLE_SHEET_ID não configurada.")
        st.stop()
    return client.open_by_key(sheet_id)


@st.cache_data(ttl=300)
def _carregar_planilha_completa() -> tuple[list, list, list]:
    """
    Lê as três abas em uma única abertura da planilha (1 cota de API).
    Retorna (lancamentos, assinaturas, parametros).
    """
    planilha = _get_planilha()
    lancamentos = planilha.sheet1.get_all_records(numericise_ignore=['all'])
    try:
        assinaturas = planilha.worksheet(ABA_ASSINATURAS).get_all_records(numericise_ignore=['all'])
    except Exception:
        assinaturas = []
    try:
        parametros = planilha.worksheet(ABA_PARAMETROS).get_all_records(numericise_ignore=['all'])
    except Exception:
        parametros = []
    return lancamentos, assinaturas, parametros


def carregar_dados() -> list:
    return _carregar_planilha_completa()[0]


def carregar_assinaturas() -> list:
    return _carregar_planilha_completa()[1]


def carregar_parametros() -> list:
    return _carregar_planilha_completa()[2]


def gerar_id_assinatura(descricao: str, valor: str, dia_do_mes: int) -> str:
    """Gera ID estável de 12 chars: sha1(descricao|valor|dia)."""
    chave = f"{descricao.strip().lower()}|{str(valor).strip()}|{dia_do_mes}"
    return hashlib.sha1(chave.encode()).hexdigest()[:12]


def salvar_assinatura(assinatura: dict) -> None:
    """Adiciona uma nova assinatura na aba. Limpa o cache após escrita."""
    ws = _get_planilha().worksheet(ABA_ASSINATURAS)
    linha = [assinatura.get(col, "") for col in COLUNAS_ASSINATURAS]
    ws.append_row(linha, value_input_option="USER_ENTERED")
    st.cache_data.clear()


def salvar_parametro(parametro: str, valor: float, data_vigencia: date) -> None:
    """
    Adiciona uma nova linha na aba Parametros (histórico preservado).
    data_vigencia define a partir de qual mês o valor é válido.
    """
    ws = _get_planilha().worksheet(ABA_PARAMETROS)
    ws.append_row(
        [parametro, str(valor), data_vigencia.strftime("%d/%m/%Y")],
        value_input_option="USER_ENTERED",
    )
    st.cache_data.clear()


def get_valor_parametro(
    parametros: list[dict],
    tipo: str,
    ano: int,
    mes: int,
) -> float | None:
    """
    Retorna o valor mais recente do parâmetro `tipo` que seja vigente em (ano, mes).
    "Vigente" = data_vigencia <= primeiro dia de (ano, mes).
    Retorna None se não houver nenhum registro aplicável.
    """
    primeiro_dia_mes = date(ano, mes, 1)
    melhor_valor     = None
    melhor_data      = None

    for linha in parametros:
        if str(linha.get("parametro", "")).strip() != tipo:
            continue
        data_str = str(linha.get("data_vigencia", "")).strip()
        try:
            p = data_str.split("/")
            data_vig = date(int(p[2]), int(p[1]), int(p[0]))
        except (ValueError, IndexError):
            continue
        if data_vig <= primeiro_dia_mes:
            if melhor_data is None or data_vig > melhor_data:
                melhor_data  = data_vig
                melhor_valor = parse_valor(linha.get("valor", "0"))

    return melhor_valor


def atualizar_assinatura(id_assinatura: str, campos: dict) -> None:
    """
    Atualiza campos específicos de uma assinatura buscando pelo ID.
    `campos` é um dict {nome_coluna: novo_valor}.
    Lança ValueError se o ID não for encontrado.
    """
    ws = _get_planilha().worksheet(ABA_ASSINATURAS)
    registros = ws.get_all_records(numericise_ignore=['all'])

    for idx, linha in enumerate(registros):
        if str(linha.get("id", "")) == id_assinatura:
            row_num = idx + 2  # linha 1 = cabeçalho; idx é 0-based
            for campo, valor in campos.items():
                if campo in COLUNAS_ASSINATURAS:
                    col_num = COLUNAS_ASSINATURAS.index(campo) + 1
                    ws.update_cell(row_num, col_num, valor)
            st.cache_data.clear()
            return

    raise ValueError(f"Assinatura com id '{id_assinatura}' não encontrada.")


# ─── Lógica de negócio ───────────────────────────────────────────────────────
def detectar_candidatos_assinatura(lancamentos: list, assinaturas: list) -> list[dict]:
    """
    Varre os lançamentos dos últimos 12 meses (mês atual + 11 anteriores) e
    retorna grupos (descricao, valor, dia_do_mes) que aparecem em pelo menos
    2 meses distintos, excluindo combinações já presentes na aba Assinaturas
    com qualquer status.

    Cada item retornado tem:
      - descricao, valor, dia_do_mes
      - ocorrencias: lista de datas (date) em que apareceu
      - id: ID gerado para eventual persistência
    """
    hoje = date.today()
    doze_meses_atras = avancar_mes(hoje.year, hoje.month, -11)  # (ano, mes) do limite inferior

    # Conjunto de IDs já cadastrados em qualquer status → ignorar na detecção
    ids_conhecidos = {str(a.get("id", "")) for a in assinaturas}

    grupos: dict[tuple, list[date]] = defaultdict(list)

    for linha in lancamentos:
        data_str  = str(linha.get(COL_DATA, "")).strip()
        valor_str = str(linha.get(COL_VALOR, "")).strip()
        desc      = str(linha.get(COL_DESCRICAO, "")).strip()

        if not data_str or not desc or not valor_str:
            continue

        try:
            partes = data_str.split("/")
            data_compra = date(int(partes[2]), int(partes[1]), int(partes[0]))
        except (ValueError, IndexError):
            continue

        ano_compra = (data_compra.year, data_compra.month)

        # Só considera os últimos 12 meses
        if ano_compra < doze_meses_atras:
            continue

        chave = (desc, valor_str, data_compra.day)
        grupos[chave].append(data_compra)

    candidatos = []
    for (desc, valor_str, dia), ocorrencias in grupos.items():
        # Meses distintos em que apareceu
        meses_distintos = {(d.year, d.month) for d in ocorrencias}
        if len(meses_distintos) < 2:
            continue

        id_assinatura = gerar_id_assinatura(desc, valor_str, dia)
        if id_assinatura in ids_conhecidos:
            continue  # já foi classificado anteriormente

        candidatos.append({
            "id":          id_assinatura,
            "descricao":   desc,
            "valor":       valor_str,
            "dia_do_mes":  dia,
            "ocorrencias": sorted(ocorrencias),
        })

    return candidatos


def fatura_da_compra(data_compra: date) -> tuple[int, int]:
    """
    Retorna (ano, mês) da fatura em que uma compra será lançada.

    Regra: fechamento = vencimento - 7 dias | vencimento = dia 4 de cada mês
    Algoritmo: data_compra + 7 dias → se dia >= 4, avança um mês.
    O dia do fechamento já pertence ao ciclo da fatura seguinte.
    """
    deslocada = data_compra + timedelta(days=7)
    ano, mes = deslocada.year, deslocada.month
    if deslocada.day >= 4:
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return ano, mes


def avancar_mes(ano: int, mes: int, quantidade: int) -> tuple[int, int]:
    """Avança N meses a partir de (ano, mes)."""
    mes_total = mes - 1 + quantidade
    return ano + mes_total // 12, mes_total % 12 + 1


def parse_valor(valor_str) -> float:
    """Converte valor para float, cobrindo todos os formatos possíveis:
    - float/int Python (retornado diretamente)
    - string com ponto decimal (ex: "44.2" — formato gspread)
    - string com vírgula decimal (ex: "44,2" — formato BR)
    - string com separador de milhar BR (ex: "1.234,56")
    - string com prefixo "R$"
    """
    if isinstance(valor_str, (int, float)):
        return float(valor_str)
    try:
        cleaned = str(valor_str).replace("R$", "").replace(" ", "").strip()
        # Com vírgula: formato BR — remove pontos de milhar, troca vírgula por ponto
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        # Sem vírgula: ponto já é separador decimal (padrão gspread)
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def calcular_totais_por_fatura(registros: list) -> dict:
    """
    Retorna um dicionário {(ano, mes): total_R$} com a fatura em aberto
    e todas as futuras que têm algum valor.
    """
    hoje = date.today()
    fatura_atual = fatura_da_compra(hoje)
    totais = defaultdict(float)

    for linha in registros:
        data_str   = str(linha.get(COL_DATA, "")).strip()
        valor_str  = linha.get(COL_VALOR, "0")
        parcelas   = linha.get(COL_PARCELAS, 1)

        if not data_str:
            continue

        try:
            partes = data_str.split("/")
            data_compra = date(int(partes[2]), int(partes[1]), int(partes[0]))
        except (ValueError, IndexError):
            continue

        try:
            total_parcelas = int(parcelas)
        except (ValueError, TypeError):
            total_parcelas = 1

        valor = parse_valor(valor_str)
        primeira_fatura = fatura_da_compra(data_compra)

        for i in range(total_parcelas):
            ano_fat, mes_fat = avancar_mes(primeira_fatura[0], primeira_fatura[1], i)

            # Só inclui faturas a partir da atual (em aberto e futuras)
            if (ano_fat, mes_fat) >= fatura_atual:
                totais[(ano_fat, mes_fat)] += valor

    return dict(totais), fatura_atual


def projetar_assinaturas(assinaturas_ativas: list, meses_projetados: set) -> dict:
    """
    Para cada assinatura ativa, calcula em quais meses do conjunto `meses_projetados`
    ela deve incidir, respeitando a periodicidade.
    Retorna {(ano, mes): valor_total_assinaturas}.
    Nunca adiciona meses novos — só contribui para meses já existentes na projeção.
    """
    totais = defaultdict(float)

    for assinatura in assinaturas_ativas:
        valor        = parse_valor(assinatura.get("valor", "0"))
        periodicidade = max(1, int(assinatura.get("periodicidade_meses", 1) or 1))
        dia_bruto    = int(assinatura.get("dia_do_mes", 1) or 1)
        data_ult_str = str(assinatura.get("data_ultimo_lancamento", "")).strip()

        if not data_ult_str:
            continue

        try:
            p = data_ult_str.split("/")
            ultimo_ano, ultimo_mes = int(p[2]), int(p[1])
        except (ValueError, IndexError):
            continue

        if not meses_projetados:
            continue

        max_mes = max(meses_projetados)

        ano_p, mes_p = ultimo_ano, ultimo_mes
        for _ in range(200):  # guarda contra loop infinito
            ano_p, mes_p = avancar_mes(ano_p, mes_p, periodicidade)
            if (ano_p, mes_p) > max_mes:
                break

            # Usa dia seguro (ex: 31 fev → 28 fev)
            dia = min(dia_bruto, calendar.monthrange(ano_p, mes_p)[1])
            try:
                data_pag = date(ano_p, mes_p, dia)
            except ValueError:
                continue

            fatura = fatura_da_compra(data_pag)
            if fatura in meses_projetados:
                totais[fatura] += valor

    return dict(totais)


def verificar_assinaturas_ausentes(
    assinaturas_ativas: list,
    lancamentos: list,
    data_hoje: date,
) -> list[dict]:
    """
    Retorna assinaturas esperadas no mês atual que não foram encontradas
    nos lançamentos, após tolerância de 7 dias após o dia esperado.
    """
    ano_atual, mes_atual = data_hoje.year, data_hoje.month

    # Conjunto de (descricao, valor) encontrados no mês atual
    encontrados = set()
    for linha in lancamentos:
        data_str = str(linha.get(COL_DATA, "")).strip()
        if not data_str:
            continue
        try:
            p = data_str.split("/")
            d = date(int(p[2]), int(p[1]), int(p[0]))
        except (ValueError, IndexError):
            continue
        if d.year == ano_atual and d.month == mes_atual:
            encontrados.add((
                str(linha.get(COL_DESCRICAO, "")).strip(),
                str(linha.get(COL_VALOR, "")).strip(),
            ))

    ausentes = []
    for assinatura in assinaturas_ativas:
        periodicidade = max(1, int(assinatura.get("periodicidade_meses", 1) or 1))
        data_ult_str  = str(assinatura.get("data_ultimo_lancamento", "")).strip()
        dia_bruto     = int(assinatura.get("dia_do_mes", 1) or 1)

        if not data_ult_str:
            continue
        try:
            p = data_ult_str.split("/")
            ultimo_ano, ultimo_mes = int(p[2]), int(p[1])
        except (ValueError, IndexError):
            continue

        meses_desde_ultimo = (ano_atual - ultimo_ano) * 12 + (mes_atual - ultimo_mes)

        # Era esperada este mês?
        if meses_desde_ultimo <= 0 or meses_desde_ultimo % periodicidade != 0:
            continue

        # Ainda dentro do prazo de tolerância?
        dia = min(dia_bruto, calendar.monthrange(ano_atual, mes_atual)[1])
        data_limite = date(ano_atual, mes_atual, dia) + timedelta(days=7)
        if data_hoje < data_limite:
            continue

        # Foi encontrada?
        desc = str(assinatura.get("descricao", "")).strip()
        val  = str(assinatura.get("valor", "")).strip()
        if (desc, val) not in encontrados:
            ausentes.append(assinatura)

    return ausentes


def calcular_linhas_referencia(
    parametros: list[dict],
    meses_ordenados: list[tuple[int, int]],
) -> dict[tuple[int, int], dict]:
    """
    Para cada (ano, mes) em meses_ordenados, retorna um dict com:
      - "renda":              float | None
      - "limite_gastos":      float | None  (renda × limite_gastos_pct / 100)
      - "limite_parcelados":  float | None  (renda × limite_parcelados_pct / 100)

    Um valor é None quando o parâmetro ainda não foi cadastrado para aquele mês.
    Os limites dependem da renda: se a renda for None num mês, os limites também são None.
    """
    resultado = {}
    for (ano, mes) in meses_ordenados:
        renda = get_valor_parametro(parametros, PARAM_RENDA, ano, mes)
        if renda is not None:
            pct_gastos    = get_valor_parametro(parametros, PARAM_LIMITE_GASTOS, ano, mes)
            pct_parcelados = get_valor_parametro(parametros, PARAM_LIMITE_PARCELADOS, ano, mes)
            limite_gastos    = round(renda * pct_gastos / 100, 2)    if pct_gastos    is not None else None
            limite_parcelados = round(renda * pct_parcelados / 100, 2) if pct_parcelados is not None else None
        else:
            limite_gastos    = None
            limite_parcelados = None

        resultado[(ano, mes)] = {
            "renda":             renda,
            "limite_gastos":     limite_gastos,
            "limite_parcelados": limite_parcelados,
        }
    return resultado


# ─── Interface ───────────────────────────────────────────────────────────────
import pandas as pd

# Session state
if "classificando_id" not in st.session_state:
    st.session_state.classificando_id = None
if "ausentes_ignoradas" not in st.session_state:
    st.session_state.ausentes_ignoradas = set()

col_title, col_version = st.columns([5, 1])
with col_title:
    st.title("💳 Gestão de Fatura")
with col_version:
    st.markdown(
        f"<div style='text-align:right; padding-top:18px; color:gray; font-size:13px;'>"
        f"v{APP_VERSION}</div>",
        unsafe_allow_html=True,
    )

# Carrega todas as abas em uma única chamada à API
try:
    registros, assinaturas, parametros = _carregar_planilha_completa()
except Exception as e:
    st.error(f"Erro ao carregar dados da planilha: {e}")
    st.stop()

assinaturas_ativas = [a for a in assinaturas if str(a.get("status", "")) == "ativa"]
candidatos         = detectar_candidatos_assinatura(registros, assinaturas)
ausentes           = [
    a for a in verificar_assinaturas_ausentes(assinaturas_ativas, registros, date.today())
    if str(a.get("id", "")) not in st.session_state.ausentes_ignoradas
]

# ── Badge na aba de Assinaturas ───────────────────────────────────────────────
alertas_total = len(candidatos) + len(ausentes)
label_assinaturas = f"🔔 Assinaturas ({alertas_total})" if alertas_total else "🔔 Assinaturas"

aba_grafico, aba_assinaturas, aba_parametros, aba_upload = st.tabs(
    ["📊 Visão Geral", label_assinaturas, "⚙️ Parâmetros", "📤 Enviar Fatura"]
)

# ── Aba: Visão Geral ──────────────────────────────────────────────────────────
with aba_grafico:
    col_titulo, col_botao = st.columns([6, 1])
    with col_titulo:
        st.subheader("Faturas em aberto e futuras")
    with col_botao:
        if st.button("🔄 Atualizar", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # Banners de notificação ──────────────────────────────────────────────────
    if candidatos:
        st.warning(
            f"💡 **{len(candidatos)} possível(is) assinatura(s) detectada(s).** "
            "Acesse a aba 🔔 Assinaturas para classificar."
        )

    if ausentes:
        st.error(
            f"⚠️ **{len(ausentes)} assinatura(s) esperada(s) não encontrada(s) este mês.** "
            "Acesse a aba 🔔 Assinaturas para verificar."
        )

    # Gráfico ─────────────────────────────────────────────────────────────────
    totais, fatura_atual = calcular_totais_por_fatura(registros)

    # Adiciona assinaturas à projeção (sem estender o range)
    meses_projetados = set(totais.keys())
    totais_assin = projetar_assinaturas(assinaturas_ativas, meses_projetados)
    for chave, val in totais_assin.items():
        if chave in totais:
            totais[chave] += val

    if not totais:
        st.info("Nenhum lançamento futuro encontrado na planilha.")
    else:
        faturas_ordenadas = sorted(totais.keys())

        labels  = []
        valores = []
        cores   = []

        for (ano, mes) in faturas_ordenadas:
            nome = f"{NOMES_MESES[mes - 1]}/{str(ano)[-2:]}"
            if (ano, mes) == fatura_atual:
                nome += " ●"
                cores.append("#E05C5C")
            else:
                cores.append("#4A90D9")
            labels.append(nome)
            valores.append(round(totais[(ano, mes)], 2))

        def formatar_brl(v: float) -> str:
            return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        fig = go.Figure(go.Bar(
            x=labels,
            y=valores,
            marker_color=cores,
            text=[formatar_brl(v) for v in valores],
            textposition="outside",
            hovertemplate="%{x}<br>%{text}<extra></extra>",
        ))
        fig.update_layout(
            yaxis_title="Valor (R$)",
            yaxis=dict(tickformat=",.0f"),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
            height=420,
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Cards ───────────────────────────────────────────────────────────────
        cards = []
        for fat in faturas_ordenadas[:2]:
            ano_c, mes_c = fat
            rotulo = f"{NOMES_MESES[mes_c - 1]}/{ano_c}"
            if fat == fatura_atual:
                rotulo += " (aberta)"
            cards.append((rotulo, totais[fat]))

        for limiar in [1000, 500, 100]:
            for fat in faturas_ordenadas[2:]:
                if totais[fat] < limiar:
                    ano_c, mes_c = fat
                    rotulo = f"< R${limiar} em {NOMES_MESES[mes_c - 1]}/{ano_c}"
                    cards.append((rotulo, totais[fat]))
                    break

        colunas = st.columns(len(cards))
        for i, (rotulo, valor) in enumerate(cards):
            with colunas[i]:
                st.metric(rotulo, formatar_brl(valor))

    # Debug ───────────────────────────────────────────────────────────────────
    with st.expander("🔍 Debug — lançamentos por fatura"):
        st.write(f"**Linhas lidas do Sheets:** {len(registros)}")
        st.write(f"**Fatura atual:** {NOMES_MESES[fatura_atual[1]-1]}/{fatura_atual[0]}")

        st.write("**Amostra de valores brutos do Sheets (primeiras 5 linhas):**")
        amostra = []
        for linha in registros[:5]:
            valor_raw = linha.get(COL_VALOR, "N/A")
            amostra.append({
                "Descrição":    str(linha.get(COL_DESCRICAO, ""))[:30],
                "valor_raw":    repr(valor_raw),
                "tipo":         type(valor_raw).__name__,
                "parse_result": parse_valor(valor_raw),
            })
        st.dataframe(pd.DataFrame(amostra), use_container_width=True, hide_index=True)

        debug_rows = []
        for linha in registros:
            data_str  = str(linha.get(COL_DATA, "")).strip()
            valor_raw = linha.get(COL_VALOR, "0")
            parcelas  = linha.get(COL_PARCELAS, 1)
            desc      = str(linha.get(COL_DESCRICAO, ""))
            try:
                partes = data_str.split("/")
                dc = date(int(partes[2]), int(partes[1]), int(partes[0]))
                tp = int(parcelas)
                valor = parse_valor(valor_raw)
                pf = fatura_da_compra(dc)
                for i in range(tp):
                    ano_f, mes_f = avancar_mes(pf[0], pf[1], i)
                    if (ano_f, mes_f) >= fatura_atual:
                        debug_rows.append({
                            "Fatura":      f"{NOMES_MESES[mes_f-1]}/{ano_f}",
                            "Data compra": data_str,
                            "Descrição":   desc[:40],
                            "Valor (R$)":  valor,
                            "Parcela":     f"{i+1}/{tp}",
                        })
            except Exception:
                pass

        df = pd.DataFrame(debug_rows)
        if not df.empty:
            st.write(f"**Total de lançamentos futuros projetados:** {len(df)}")
            st.dataframe(
                df.sort_values(["Fatura", "Data compra"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("Nenhum lançamento futuro encontrado.")


# ── Aba: Assinaturas ──────────────────────────────────────────────────────────
with aba_assinaturas:

    # ── Seção 1: Candidatos a classificar ────────────────────────────────────
    if candidatos:
        st.subheader("💡 Possíveis assinaturas detectadas")
        st.caption(
            "Esses lançamentos apareceram em 2 ou mais meses distintos com "
            "o mesmo valor, descrição e dia. Classifique cada um abaixo."
        )

        for candidato in candidatos:
            cid   = candidato["id"]
            desc  = candidato["descricao"]
            valor = candidato["valor"]
            dia   = candidato["dia_do_mes"]
            n_oc  = len({(d.year, d.month) for d in candidato["ocorrencias"]})

            with st.container():
                col_info, col_acoes = st.columns([3, 2])
                with col_info:
                    st.markdown(f"**{desc}**")
                    st.caption(f"Valor: {valor} · Dia {dia} do mês · {n_oc} ocorrência(s)")

                with col_acoes:
                    # Formulário inline de periodicidade
                    if st.session_state.classificando_id == cid:
                        periodicidade = st.selectbox(
                            "Periodicidade",
                            options=[1, 6, 12],
                            format_func=lambda x: {1: "Mensal", 6: "Semestral", 12: "Anual"}[x],
                            key=f"per_{cid}",
                        )
                        col_conf, col_canc = st.columns(2)
                        with col_conf:
                            if st.button("✅ Confirmar", key=f"conf_{cid}", type="primary"):
                                ultima_oc = max(candidato["ocorrencias"])
                                salvar_assinatura({
                                    "id":                     cid,
                                    "descricao":              desc,
                                    "valor":                  valor,
                                    "dia_do_mes":             dia,
                                    "periodicidade_meses":    periodicidade,
                                    "status":                 "ativa",
                                    "data_inicio":            ultima_oc.strftime("%d/%m/%Y"),
                                    "data_ultimo_lancamento": ultima_oc.strftime("%d/%m/%Y"),
                                    "data_cancelamento":      "",
                                })
                                st.session_state.classificando_id = None
                                st.rerun()
                        with col_canc:
                            if st.button("✖ Cancelar", key=f"canc_{cid}"):
                                st.session_state.classificando_id = None
                                st.rerun()
                    else:
                        col_b1, col_b2 = st.columns(2)
                        with col_b1:
                            if st.button("É assinatura", key=f"sim_{cid}", type="primary"):
                                st.session_state.classificando_id = cid
                                st.rerun()
                        with col_b2:
                            if st.button("Ignorar", key=f"ign_{cid}"):
                                ultima_oc = max(candidato["ocorrencias"])
                                salvar_assinatura({
                                    "id":                     cid,
                                    "descricao":              desc,
                                    "valor":                  valor,
                                    "dia_do_mes":             dia,
                                    "periodicidade_meses":    1,
                                    "status":                 "ignorada",
                                    "data_inicio":            ultima_oc.strftime("%d/%m/%Y"),
                                    "data_ultimo_lancamento": ultima_oc.strftime("%d/%m/%Y"),
                                    "data_cancelamento":      "",
                                })
                                st.rerun()
            st.divider()
    else:
        st.success("✅ Nenhum novo candidato a assinatura detectado.")

    st.divider()

    # ── Seção 2: Assinaturas ausentes ────────────────────────────────────────
    if ausentes:
        st.subheader("⚠️ Assinaturas não encontradas este mês")
        st.caption("Essas assinaturas eram esperadas mas não aparecem nos lançamentos do mês atual.")

        for assinatura in ausentes:
            aid  = str(assinatura.get("id", ""))
            desc = str(assinatura.get("descricao", ""))
            val  = str(assinatura.get("valor", ""))
            dia  = str(assinatura.get("dia_do_mes", ""))

            with st.container():
                col_info, col_acoes = st.columns([3, 2])
                with col_info:
                    st.markdown(f"**{desc}**")
                    st.caption(f"Valor: {val} · Esperada no dia {dia}")
                with col_acoes:
                    col_b1, col_b2 = st.columns(2)
                    with col_b1:
                        if st.button("Sim, cancelada", key=f"cancel_{aid}", type="primary"):
                            atualizar_assinatura(aid, {
                                "status":            "cancelada",
                                "data_cancelamento": date.today().strftime("%d/%m/%Y"),
                            })
                            st.rerun()
                    with col_b2:
                        if st.button("Não, ativa", key=f"ativa_{aid}"):
                            st.session_state.ausentes_ignoradas.add(aid)
                            st.rerun()
            st.divider()

    st.divider()

    # ── Seção 3: Assinaturas ativas ──────────────────────────────────────────
    st.subheader("📋 Assinaturas ativas")

    MAP_PERIODICIDADE = {1: "Mensal", 6: "Semestral", 12: "Anual"}

    if assinaturas_ativas:
        for assinatura in assinaturas_ativas:
            aid  = str(assinatura.get("id", ""))
            desc = str(assinatura.get("descricao", ""))
            val  = str(assinatura.get("valor", ""))
            dia  = str(assinatura.get("dia_do_mes", ""))
            per  = int(assinatura.get("periodicidade_meses", 1) or 1)
            ult  = str(assinatura.get("data_ultimo_lancamento", ""))

            with st.container():
                col_info, col_btn = st.columns([4, 1])
                with col_info:
                    st.markdown(f"**{desc}**")
                    st.caption(
                        f"{MAP_PERIODICIDADE.get(per, f'{per} meses')} · "
                        f"Valor: {val} · Dia {dia} · Último: {ult}"
                    )
                with col_btn:
                    if st.button("Cancelar", key=f"del_{aid}"):
                        atualizar_assinatura(aid, {
                            "status":            "cancelada",
                            "data_cancelamento": date.today().strftime("%d/%m/%Y"),
                        })
                        st.rerun()
            st.divider()
    else:
        st.info("Nenhuma assinatura ativa cadastrada.")


# ── Aba: Parâmetros ───────────────────────────────────────────────────────────
with aba_parametros:
    st.subheader("⚙️ Parâmetros financeiros")
    st.caption(
        "Esses valores são usados para desenhar as linhas de referência no gráfico. "
        "Alterações na renda entram em vigor a partir do mês seguinte."
    )

    hoje = date.today()

    # Lê valores atualmente vigentes para o mês atual
    renda_atual        = get_valor_parametro(parametros, PARAM_RENDA,            hoje.year, hoje.month)
    lim_gastos_atual   = get_valor_parametro(parametros, PARAM_LIMITE_GASTOS,    hoje.year, hoje.month)
    lim_parcel_atual   = get_valor_parametro(parametros, PARAM_LIMITE_PARCELADOS, hoje.year, hoje.month)

    with st.form("form_parametros"):
        nova_renda = st.number_input(
            "💰 Renda mensal líquida (R$)",
            min_value=0.0,
            step=100.0,
            value=float(renda_atual) if renda_atual is not None else 0.0,
            format="%.2f",
            help="Linha preta no gráfico. A alteração entra em vigor no mês seguinte.",
        )
        novo_lim_gastos = st.number_input(
            "🔴 Limite de gastos (%)",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            value=float(lim_gastos_atual) if lim_gastos_atual is not None else 0.0,
            format="%.1f",
            help="Linha vermelha. Percentual da renda líquida.",
        )
        novo_lim_parcel = st.number_input(
            "🟡 Limite de gastos parcelados (%)",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            value=float(lim_parcel_atual) if lim_parcel_atual is not None else 0.0,
            format="%.1f",
            help="Linha amarela. Percentual da renda líquida.",
        )

        salvar = st.form_submit_button("💾 Salvar alterações", type="primary")

    if salvar:
        # data_vigencia para renda = 1º do mês seguinte
        proximo_mes = avancar_mes(hoje.year, hoje.month, 1)
        vig_renda   = date(proximo_mes[0], proximo_mes[1], 1)
        # data_vigencia para percentuais = 1º do mês atual (vigência imediata)
        vig_pct     = date(hoje.year, hoje.month, 1)

        alteracoes = []

        if renda_atual is None or nova_renda != renda_atual:
            salvar_parametro(PARAM_RENDA, nova_renda, vig_renda)
            alteracoes.append(
                f"Renda: R$ {nova_renda:,.2f} — vigência a partir de "
                f"{NOMES_MESES[vig_renda.month - 1]}/{vig_renda.year}"
            )

        if lim_gastos_atual is None or novo_lim_gastos != lim_gastos_atual:
            salvar_parametro(PARAM_LIMITE_GASTOS, novo_lim_gastos, vig_pct)
            abs_gastos = nova_renda * novo_lim_gastos / 100
            alteracoes.append(
                f"Limite de gastos: {novo_lim_gastos:.1f}% "
                f"= R$ {abs_gastos:,.2f}"
            )

        if lim_parcel_atual is None or novo_lim_parcel != lim_parcel_atual:
            salvar_parametro(PARAM_LIMITE_PARCELADOS, novo_lim_parcel, vig_pct)
            abs_parcel = nova_renda * novo_lim_parcel / 100
            alteracoes.append(
                f"Limite parcelados: {novo_lim_parcel:.1f}% "
                f"= R$ {abs_parcel:,.2f}"
            )

        if alteracoes:
            st.success("✅ Parâmetros salvos:\n\n" + "\n\n".join(f"- {a}" for a in alteracoes))
        else:
            st.info("Nenhuma alteração detectada.")

    # Resumo dos valores vigentes
    st.divider()
    st.caption("**Valores vigentes neste mês**")
    col1, col2, col3 = st.columns(3)
    with col1:
        val = f"R$ {renda_atual:,.2f}" if renda_atual is not None else "—"
        st.metric("Renda líquida", val)
    with col2:
        if lim_gastos_atual is not None and renda_atual is not None:
            abs_g = renda_atual * lim_gastos_atual / 100
            val = f"{lim_gastos_atual:.1f}%  (R$ {abs_g:,.2f})"
        else:
            val = "—"
        st.metric("Limite de gastos", val)
    with col3:
        if lim_parcel_atual is not None and renda_atual is not None:
            abs_p = renda_atual * lim_parcel_atual / 100
            val = f"{lim_parcel_atual:.1f}%  (R$ {abs_p:,.2f})"
        else:
            val = "—"
        st.metric("Limite parcelados", val)


# ── Aba: Upload ───────────────────────────────────────────────────────────────
with aba_upload:
    st.subheader("Enviar prints da fatura para processamento")

    imagens = st.file_uploader(
        "Escolha uma ou mais imagens",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
    )

    if st.button("Enviar para o n8n", type="primary"):
        if not imagens:
            st.warning("Selecione pelo menos uma imagem antes de enviar.")
        else:
            url_n8n = os.environ.get("N8N_WEBHOOK_URL")
            if not url_n8n:
                st.error("Variável de ambiente N8N_WEBHOOK_URL não configurada.")
                st.stop()

            # Monta headers — suporta autenticação opcional do webhook n8n
            headers = {"Content-Type": "application/json"}
            auth_token = os.environ.get("N8N_WEBHOOK_AUTH_TOKEN")
            auth_header = os.environ.get("N8N_WEBHOOK_AUTH_HEADER", "Authorization")
            if auth_token:
                headers[auth_header] = auth_token

            total = len(imagens)
            barra = st.progress(0, text="Iniciando envio...")

            for idx, img in enumerate(imagens, start=1):
                barra.progress(
                    (idx - 1) / total,
                    text=f"Enviando {idx}/{total}: {img.name}",
                )
                conteudo = img.read()
                imagem_b64 = base64.b64encode(conteudo).decode("utf-8")
                payload = {
                    "imagens": [{
                        "nome": img.name,
                        "tipo": img.type,
                        "dados": imagem_b64,
                    }]
                }
                try:
                    resposta = requests.post(url_n8n, json=payload, headers=headers, timeout=60)
                    if resposta.status_code == 403:
                        st.error(
                            f"❌ **{img.name}**: HTTP 403 — Acesso negado. "
                            "Verifique se o webhook do n8n exige autenticação "
                            "(configure `N8N_WEBHOOK_AUTH_TOKEN` no Railway)."
                        )
                    elif resposta.status_code != 200:
                        st.error(f"❌ Erro ao enviar **{img.name}**: HTTP {resposta.status_code}")
                except requests.exceptions.Timeout:
                    st.error(f"❌ Timeout ao enviar **{img.name}**. Verifique se o n8n está ativo.")
                except requests.exceptions.RequestException as e:
                    st.error(f"❌ Erro de conexão ao enviar **{img.name}**: {e}")

            barra.progress(1.0, text="Concluído!")
            st.success(f"✅ {total} imagem(ns) enviada(s) com sucesso!")
