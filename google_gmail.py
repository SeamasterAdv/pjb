"""Leitura do Gmail para o agente de e-mail (somente leitura: a Beatriz não envia nem apaga nada).

Usa um cliente OAuth do Google (google_credentials.json ou GOOGLE_CLIENT_ID/SECRET no .env). Para vincular a conta pela primeira vez, diga "conectar gmail" ou rode:
    .venv312\\Scripts\\python.exe google_gmail.py
O token fica salvo em GMAIL_TOKEN_FILE (no .gitignore).
"""
import json
import logging
import os

from config import GMAIL_TOKEN_FILE, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_CREDENTIALS_FILE

logger = logging.getLogger(__name__)

ESCOPOS = ["https://www.googleapis.com/auth/gmail.readonly"]

INSTRUCOES_VINCULO = (
    "O Gmail não está vinculado. Para vincular (é gratuito): no Google Cloud Console, crie um projeto, "
    "ative a Gmail API, crie um ID do cliente OAuth do tipo 'App para computador', baixe o JSON como "
    "google_credentials.json na pasta da Beatriz e adicione seu e-mail em Tela de consentimento > "
    "Usuários de teste. Depois diga 'conectar gmail'."
)


class GmailNaoConectado(Exception):
    pass


def _configuracao_cliente():
    """Lê o cliente OAuth do google_credentials.json ou, na falta dele, do .env."""
    if os.path.exists(GOOGLE_CREDENTIALS_FILE):
        with open(GOOGLE_CREDENTIALS_FILE, "r", encoding="utf-8") as arquivo:
            configuracao = json.load(arquivo)
        if "installed" not in configuracao:
            tipo = next(iter(configuracao), "desconhecido")
            raise ValueError(
                f"O arquivo {GOOGLE_CREDENTIALS_FILE} é do tipo '{tipo}'. "
                "Crie no Google Cloud um ID do cliente OAuth do tipo 'App para computador' (Desktop app)."
            )
        return configuracao
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        return {
            "installed": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
    raise FileNotFoundError(
        f"Credenciais do Google não encontradas: crie o arquivo {GOOGLE_CREDENTIALS_FILE} "
        "ou preencha GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET no .env."
    )


def _carregar_credenciais():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not os.path.exists(GMAIL_TOKEN_FILE):
        return None
    credenciais = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, ESCOPOS)
    if credenciais.valid:
        return credenciais
    if credenciais.expired and credenciais.refresh_token:
        try:
            credenciais.refresh(Request())
        except RefreshError:
            logger.warning("Token do Gmail inválido; será preciso vincular a conta de novo.")
            os.remove(GMAIL_TOKEN_FILE)
            return None
        with open(GMAIL_TOKEN_FILE, "w", encoding="utf-8") as arquivo:
            arquivo.write(credenciais.to_json())
        return credenciais
    return None


def gmail_conectado() -> bool:
    try:
        return _carregar_credenciais() is not None
    except Exception as exc:
        logger.warning("Falha ao ler o token do Gmail: %s", exc)
        return False


def conectar_gmail() -> str:
    """Abre o navegador para o login OAuth (somente leitura) e salva o token."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        fluxo = InstalledAppFlow.from_client_config(_configuracao_cliente(), ESCOPOS)
        credenciais = fluxo.run_local_server(
            port=0,
            prompt="consent",
            authorization_prompt_message="Abrindo o navegador para você entrar na conta do Google...",
            success_message="Gmail vinculado à Beatriz (somente leitura). Pode fechar esta aba.",
            open_browser=True,
        )
        with open(GMAIL_TOKEN_FILE, "w", encoding="utf-8") as arquivo:
            arquivo.write(credenciais.to_json())
        return "Gmail vinculado com sucesso, somente para leitura."
    except (FileNotFoundError, ValueError):
        return INSTRUCOES_VINCULO
    except Exception as exc:
        if "access_denied" in str(exc):
            return "O Google negou o acesso. Adicione seu e-mail em Tela de consentimento OAuth > Usuários de teste."
        logger.exception("Falha ao vincular o Gmail")
        return f"Não consegui vincular o Gmail: {exc}"


def listar_emails(consulta: str = "is:unread newer_than:3d", limite: int = 10) -> list:
    """E-mails que atendem à busca do Gmail, com remetente, assunto, data e o trecho inicial."""
    from googleapiclient.discovery import build

    credenciais = _carregar_credenciais()
    if credenciais is None:
        raise GmailNaoConectado(INSTRUCOES_VINCULO)
    servico = build("gmail", "v1", credentials=credenciais, cache_discovery=False)
    itens = servico.users().messages().list(userId="me", q=consulta, maxResults=limite).execute().get("messages", [])
    emails = []
    for item in itens:
        dados = servico.users().messages().get(
            userId="me", id=item["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        cabecalhos = {h["name"].lower(): h["value"] for h in dados.get("payload", {}).get("headers", [])}
        emails.append({
            "id": item["id"],
            "de": cabecalhos.get("from", "desconhecido"),
            "assunto": cabecalhos.get("subject", "(sem assunto)"),
            "data": cabecalhos.get("date", ""),
            "trecho": dados.get("snippet", ""),
            "importante": "IMPORTANT" in dados.get("labelIds", []),
        })
    return emails


if __name__ == "__main__":
    print(conectar_gmail())
