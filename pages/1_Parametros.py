import importlib.util
from datetime import date

import streamlit as st

try:
    from shared_data import (
        NOMES_MESES,
        PARAM_RENDA,
        PARAM_LIMITE_GASTOS,
        PARAM_LIMITE_PARCELADOS,
        carregar_planilha_completa,
        get_valor_parametro,
        salvar_parametros,
    )
except ModuleNotFoundError:
    from pathlib import Path
    module_path = Path(__file__).resolve().parents[1] / "shared_data.py"
    spec = importlib.util.spec_from_file_location("shared_data_fallback", module_path)
    shared_data_fallback = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(shared_data_fallback)
    NOMES_MESES = shared_data_fallback.NOMES_MESES
    PARAM_RENDA = shared_data_fallback.PARAM_RENDA
    PARAM_LIMITE_GASTOS = shared_data_fallback.PARAM_LIMITE_GASTOS
    PARAM_LIMITE_PARCELADOS = shared_data_fallback.PARAM_LIMITE_PARCELADOS
    carregar_planilha_completa = shared_data_fallback.carregar_planilha_completa
    get_valor_parametro = shared_data_fallback.get_valor_parametro
    salvar_parametros = shared_data_fallback.salvar_parametros


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
