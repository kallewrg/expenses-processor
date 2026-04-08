import json
import os
from datetime import date

import gspread
import streamlit as st
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
NOMES_MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
               "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
ABA_PARAMETROS = "Parametros"
PARAM_RENDA = "renda_mensal_liquida"
PARAM_LIMITE_GASTOS = "limite_gastos_pct"
PARAM_LIMITE_PARCELADOS = "limite_parcelados_pct"


def parse_valor(valor_str) -> float:
    if isinstance(valor_str, (int, float)):
        return float(valor_str)
    try:
        cleaned = str(valor_str).replace("R$", "").replace(" ", "").strip()
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


@st.cache_resource
def get_gspread_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        st.error("Variável de ambiente GOOGLE_CREDENTIALS não configurada.")
        st.stop()
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


@st.cache_resource
def get_planilha():
    client = get_gspread_client()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        st.error("Variável de ambiente GOOGLE_SHEET_ID não configurada.")
        st.stop()
    return client.open_by_key(sheet_id)


def carregar_planilha_completa() -> tuple[list, list, list]:
    planilha = get_planilha()
    lancamentos = planilha.sheet1.get_all_records(numericise_ignore=["all"])
    try:
        assinaturas = planilha.worksheet("Assinaturas").get_all_records(numericise_ignore=["all"])
    except Exception:
        assinaturas = []
    try:
        parametros = planilha.worksheet(ABA_PARAMETROS).get_all_records(numericise_ignore=["all"])
    except Exception:
        parametros = []
    return lancamentos, assinaturas, parametros


def salvar_parametros(linhas: list[list]) -> None:
    ws = get_planilha().worksheet(ABA_PARAMETROS)
    ws.append_rows(linhas, value_input_option="USER_ENTERED")


def get_valor_parametro(parametros: list[dict], tipo: str, ano: int, mes: int) -> float | None:
    primeiro_dia_mes = date(ano, mes, 1)
    melhor_valor = None
    melhor_data = None
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
                melhor_data = data_vig
                melhor_valor = parse_valor(linha.get("valor", "0"))
    return melhor_valor


def avancar_mes(ano: int, mes: int, quantidade: int) -> tuple[int, int]:
    mes_total = mes - 1 + quantidade
    return ano + mes_total // 12, mes_total % 12 + 1


st.set_page_config(page_title="Parâmetros Financeiros", page_icon="⚙️", layout="wide")
st.title("⚙️ Parâmetros Financeiros")
st.caption("Configure renda e limites financeiros nesta página dedicada.")

try:
    _, _, parametros = carregar_planilha_completa()
except Exception as e:
    st.error(f"Erro ao carregar dados da planilha: {e}")
    st.stop()

hoje = date.today()
renda_atual = get_valor_parametro(parametros, PARAM_RENDA, hoje.year, hoje.month)
lim_gastos_atual = get_valor_parametro(parametros, PARAM_LIMITE_GASTOS, hoje.year, hoje.month)
lim_parcel_atual = get_valor_parametro(parametros, PARAM_LIMITE_PARCELADOS, hoje.year, hoje.month)

if "param_page_renda" not in st.session_state:
    st.session_state.param_page_renda = float(renda_atual or 0.0)
if "param_page_lim_gastos" not in st.session_state:
    st.session_state.param_page_lim_gastos = float(lim_gastos_atual or 0.0)
if "param_page_lim_parcel" not in st.session_state:
    st.session_state.param_page_lim_parcel = float(lim_parcel_atual or 0.0)

with st.form("form_parametros_page"):
    st.number_input(
        "Renda mensal líquida (R$)",
        min_value=0.0,
        step=100.0,
        format="%.2f",
        key="param_page_renda",
        help="A alteração entra em vigor no mês seguinte.",
    )
    st.number_input(
        "Limite de gastos (%)",
        min_value=0.0,
        max_value=100.0,
        step=1.0,
        format="%.1f",
        key="param_page_lim_gastos",
    )
    st.number_input(
        "Limite de gastos parcelados (%)",
        min_value=0.0,
        max_value=100.0,
        step=1.0,
        format="%.1f",
        key="param_page_lim_parcel",
    )
    submitted = st.form_submit_button("Salvar alterações", type="primary")

if submitted:
    proximo_mes = avancar_mes(hoje.year, hoje.month, 1)
    vig_renda = date(proximo_mes[0], proximo_mes[1], 1)
    vig_pct = date(hoje.year, hoje.month, 1)
    renda_val = float(st.session_state["param_page_renda"])
    lim_g_val = float(st.session_state["param_page_lim_gastos"])
    lim_p_val = float(st.session_state["param_page_lim_parcel"])
    try:
        salvar_parametros([
            [PARAM_RENDA, str(renda_val), vig_renda.strftime("%d/%m/%Y")],
            [PARAM_LIMITE_GASTOS, str(lim_g_val), vig_pct.strftime("%d/%m/%Y")],
            [PARAM_LIMITE_PARCELADOS, str(lim_p_val), vig_pct.strftime("%d/%m/%Y")],
        ])
        st.success(
            f"Salvo. Renda: R$ {renda_val:,.2f} "
            f"(vigência {NOMES_MESES[vig_renda.month-1]}/{vig_renda.year}) "
            f"| Gastos: {lim_g_val:.1f}% | Parcelados: {lim_p_val:.1f}%"
        )
    except Exception as e:
        st.error(f"Erro ao salvar: {e}")

st.divider()
st.caption("Valores vigentes neste mês")
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Renda líquida", f"R$ {renda_atual:,.2f}" if renda_atual is not None else "—")
with col2:
    if lim_gastos_atual is not None and renda_atual is not None:
        st.metric("Limite de gastos", f"{lim_gastos_atual:.1f}%  (R$ {renda_atual*lim_gastos_atual/100:,.2f})")
    else:
        st.metric("Limite de gastos", "—")
with col3:
    if lim_parcel_atual is not None and renda_atual is not None:
        st.metric("Limite parcelados", f"{lim_parcel_atual:.1f}%  (R$ {renda_atual*lim_parcel_atual/100:,.2f})")
    else:
        st.metric("Limite parcelados", "—")
