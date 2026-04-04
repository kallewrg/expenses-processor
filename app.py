import os
import json
import base64
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
APP_VERSION = "1.5.0"

# ─── Constantes ─────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
NOMES_MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
               "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

# Nomes exatos das colunas na planilha
COL_DATA      = "Data da compra"
COL_DESCRICAO = "Descrição do gasto"
COL_VALOR     = "Valor do gasto/parcela"
COL_PARCELAS  = "Quantidade de parcelas [Parcelas]"


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


@st.cache_data(ttl=300)  # atualiza a cada 5 minutos
def carregar_dados():
    client = get_gspread_client()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        st.error("Variável de ambiente GOOGLE_SHEET_ID não configurada.")
        st.stop()
    sheet = client.open_by_key(sheet_id).sheet1
    return sheet.get_all_records(value_render_option='UNFORMATTED_VALUE')


# ─── Lógica de negócio ───────────────────────────────────────────────────────
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


# ─── Interface ───────────────────────────────────────────────────────────────
col_title, col_version = st.columns([5, 1])
with col_title:
    st.title("💳 Gestão de Fatura")
with col_version:
    st.markdown(
        f"<div style='text-align:right; padding-top:18px; color:gray; font-size:13px;'>"
        f"v{APP_VERSION}</div>",
        unsafe_allow_html=True,
    )

aba_grafico, aba_upload = st.tabs(["📊 Visão Geral", "📤 Enviar Fatura"])

# ── Aba: Gráfico ──────────────────────────────────────────────────────────────
with aba_grafico:
    col_titulo, col_botao = st.columns([6, 1])
    with col_titulo:
        st.subheader("Faturas em aberto e futuras")
    with col_botao:
        if st.button("🔄 Atualizar", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    try:
        registros = carregar_dados()
        totais, fatura_atual = calcular_totais_por_fatura(registros)

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
                    nome += " ●"          # marcador visual de fatura aberta
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

            # Cards de resumo abaixo do gráfico
            cards = []

            # 1. Fatura em aberto e a próxima
            for fat in faturas_ordenadas[:2]:
                ano_c, mes_c = fat
                rotulo = f"{NOMES_MESES[mes_c - 1]}/{ano_c}"
                if fat == fatura_atual:
                    rotulo += " (aberta)"
                cards.append((rotulo, totais[fat]))

            # 2-4. Primeira fatura abaixo de cada limiar (a partir da 3ª em diante)
            for limiar in [1000, 500, 100]:
                for fat in faturas_ordenadas[2:]:
                    if totais[fat] < limiar:
                        ano_c, mes_c = fat
                        rotulo = f"< R${limiar} em {NOMES_MESES[mes_c - 1]}/{ano_c}"
                        cards.append((rotulo, totais[fat]))
                        break  # só o primeiro que cruza o limiar

            colunas = st.columns(len(cards))
            for i, (rotulo, valor) in enumerate(cards):
                with colunas[i]:
                    st.metric(rotulo, formatar_brl(valor))

        # ── Debug ────────────────────────────────────────────────────────────
        with st.expander("🔍 Debug — lançamentos por fatura"):
            st.write(f"**Linhas lidas do Sheets:** {len(registros)}")
            st.write(f"**Fatura atual:** {NOMES_MESES[fatura_atual[1]-1]}/{fatura_atual[0]}")

            # Amostra dos 5 primeiros valores brutos para diagnóstico
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
            import pandas as pd
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

    except Exception as e:
        st.error(f"Erro ao carregar dados da planilha: {e}")


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

            lista_imagens = []
            for img in imagens:
                conteudo = img.read()
                imagem_b64 = base64.b64encode(conteudo).decode("utf-8")
                lista_imagens.append({
                    "nome": img.name,
                    "tipo": img.type,
                    "dados": imagem_b64,
                })

            with st.spinner("Enviando imagens..."):
                try:
                    resposta = requests.post(
                        url_n8n,
                        json={"imagens": lista_imagens},
                        timeout=60,
                    )
                    if resposta.status_code == 200:
                        st.success("✅ Imagens enviadas com sucesso!")
                        st.json(resposta.json())
                    else:
                        st.error(f"Erro ao enviar: HTTP {resposta.status_code}")
                except requests.exceptions.Timeout:
                    st.error("Tempo de resposta excedido. Verifique se o n8n está ativo.")
                except requests.exceptions.RequestException as e:
                    st.error(f"Erro de conexão: {e}")
