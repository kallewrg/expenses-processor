import streamlit as st
import requests
import base64

st.title("Enviar imagens para o n8n")

# Campo para escolher as imagens
imagens = st.file_uploader(
    "Escolha uma ou mais imagens",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True
)

# Botão de envio
if st.button("Enviar para o n8n"):

    if not imagens:
        st.warning("Selecione pelo menos uma imagem antes de enviar.")
    else:
        # Monta a lista de imagens em formato que o n8n entende
        lista_imagens = []
        for img in imagens:
            conteudo = img.read()
            imagem_base64 = base64.b64encode(conteudo).decode("utf-8")
            lista_imagens.append({
                "nome": img.name,
                "tipo": img.type,
                "dados": imagem_base64
            })

        # Envia para o n8n
        url_n8n = "https://n8n-production-9e43.up.railway.app/webhook-test/expenses"  # ← TROQUE AQUI

        resposta = requests.post(url_n8n, json={"imagens": lista_imagens})

        if resposta.status_code == 200:
            st.success("Imagens enviadas com sucesso!")
            st.json(resposta.json())  # Mostra o retorno do n8n
        else:
            st.error(f"Erro ao enviar: {resposta.status_code}")
