"""Integração da Beatriz com o Google Agenda (Google Calendar API v3).

Para vincular a conta pela primeira vez, rode:
    .venv312\\Scripts\\python.exe google_agenda.py
O navegador abre a tela de login do Google e o token fica salvo em
GOOGLE_CALENDAR_TOKEN_FILE (no .gitignore).
"""
import json
import logging
import os
from datetime import datetime, timezone

from config import (
    GOOGLE_CALENDAR_TOKEN_FILE,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_CREDENTIALS_FILE,
)

logger = logging.getLogger(__name__)

ESCOPOS = ["https://www.googleapis.com/auth/calendar.events"]
FUSO_PADRAO = "America/Sao_Paulo"


class GoogleAgendaNaoConectada(Exception):
    """A conta do Google ainda não foi vinculada (ou o token foi revogado)."""


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

    if not os.path.exists(GOOGLE_CALENDAR_TOKEN_FILE):
        return None
    credenciais = Credentials.from_authorized_user_file(GOOGLE_CALENDAR_TOKEN_FILE, ESCOPOS)
    if credenciais.valid:
        return credenciais
    if credenciais.expired and credenciais.refresh_token:
        try:
            credenciais.refresh(Request())
        except RefreshError:
            # Token revogado ou expirado (comum em apps OAuth em modo "Teste", que expiram em 7 dias).
            logger.warning("Token do Google Agenda inválido; será preciso vincular a conta de novo.")
            os.remove(GOOGLE_CALENDAR_TOKEN_FILE)
            return None
        _salvar_token(credenciais)
        return credenciais
    return None


def _salvar_token(credenciais):
    with open(GOOGLE_CALENDAR_TOKEN_FILE, "w", encoding="utf-8") as arquivo:
        arquivo.write(credenciais.to_json())


def google_agenda_conectada() -> bool:
    try:
        return _carregar_credenciais() is not None
    except Exception as exc:
        logger.warning("Falha ao ler o token do Google Agenda: %s", exc)
        return False


def _servico():
    from googleapiclient.discovery import build

    credenciais = _carregar_credenciais()
    if credenciais is None:
        raise GoogleAgendaNaoConectada(
            "O Google Agenda não está vinculado. Diga 'conectar google agenda' para fazer o login."
        )
    return build("calendar", "v3", credentials=credenciais, cache_discovery=False)


def _mensagem_erro_http(exc) -> str:
    texto = str(exc)
    if "accessNotConfigured" in texto or "has not been used in project" in texto or "is disabled" in texto:
        return (
            "A Google Calendar API não está ativada no seu projeto do Google Cloud. "
            "Ative-a em APIs e serviços > Biblioteca > Google Calendar API e tente de novo."
        )
    if "insufficientPermissions" in texto or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in texto:
        return "O token não tem permissão para a agenda. Diga 'conectar google agenda' para vincular de novo."
    return f"O Google Agenda recusou a operação: {exc}"


def conectar_google_agenda() -> str:
    """Abre o navegador para o login OAuth e salva o token. Retorna mensagem ao usuário."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        fluxo = InstalledAppFlow.from_client_config(_configuracao_cliente(), ESCOPOS)
        credenciais = fluxo.run_local_server(
            port=0,
            prompt="consent",
            authorization_prompt_message="Abrindo o navegador para você entrar na conta do Google...",
            success_message="Conta vinculada à Beatriz. Pode fechar esta aba.",
            open_browser=True,
        )
        _salvar_token(credenciais)
        agenda = _servico().calendars().get(calendarId="primary").execute()
        return f"Google Agenda vinculado com sucesso à conta {agenda.get('id', '')}."
    except (FileNotFoundError, ValueError) as exc:
        return str(exc)
    except Exception as exc:
        if exc.__class__.__name__ == "HttpError":
            return _mensagem_erro_http(exc)
        if "access_denied" in str(exc):
            return (
                "O Google negou o acesso. Se o app OAuth está em modo 'Teste', adicione seu e-mail "
                "em Tela de consentimento OAuth > Usuários de teste."
            )
        logger.exception("Falha ao vincular o Google Agenda")
        return f"Não consegui vincular o Google Agenda: {exc}"


def desconectar_google_agenda() -> str:
    if os.path.exists(GOOGLE_CALENDAR_TOKEN_FILE):
        os.remove(GOOGLE_CALENDAR_TOKEN_FILE)
        return "Google Agenda desvinculado deste computador."
    return "O Google Agenda já não estava vinculado."


def criar_evento_google(titulo, inicio, fim, descricao="", minutos_lembrete=None) -> str:
    """Cria o evento na agenda principal. `inicio`/`fim` são datetimes com fuso."""
    evento = {
        "summary": titulo,
        "description": descricao,
        "start": {"dateTime": inicio.isoformat(), "timeZone": FUSO_PADRAO},
        "end": {"dateTime": fim.isoformat(), "timeZone": FUSO_PADRAO},
    }
    if minutos_lembrete is not None:
        evento["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": int(minutos_lembrete)}],
        }
    try:
        criado = _servico().events().insert(calendarId="primary", body=evento).execute()
    except GoogleAgendaNaoConectada:
        raise
    except Exception as exc:
        if exc.__class__.__name__ == "HttpError":
            raise RuntimeError(_mensagem_erro_http(exc)) from exc
        raise
    quando = inicio.strftime("%d/%m às %H:%M")
    return f"Evento '{titulo}' criado no Google Agenda para {quando}."


def listar_proximos_eventos(quantidade: int = 5) -> str:
    try:
        agora = datetime.now(timezone.utc).isoformat()
        resposta = (
            _servico()
            .events()
            .list(calendarId="primary", timeMin=agora, maxResults=quantidade, singleEvents=True, orderBy="startTime")
            .execute()
        )
    except GoogleAgendaNaoConectada as exc:
        return str(exc)
    except Exception as exc:
        if exc.__class__.__name__ == "HttpError":
            return _mensagem_erro_http(exc)
        logger.exception("Falha ao listar eventos do Google Agenda")
        return f"Não consegui consultar o Google Agenda: {exc}"

    eventos = resposta.get("items", [])
    if not eventos:
        return "Você não tem compromissos futuros no Google Agenda."
    linhas = []
    for evento in eventos:
        inicio = evento.get("start", {})
        if "dateTime" in inicio:
            quando = datetime.fromisoformat(inicio["dateTime"]).strftime("%d/%m às %H:%M")
        else:
            quando = datetime.fromisoformat(inicio.get("date", "")).strftime("%d/%m (dia inteiro)")
        linhas.append(f"{quando}: {evento.get('summary', '(sem título)')}")
    return "Seus próximos compromissos: " + "; ".join(linhas) + "."


if __name__ == "__main__":
    print(conectar_google_agenda())
