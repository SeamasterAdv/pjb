import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import GOOGLE_DRIVE_FOLDER_ID, GOOGLE_TOKEN_FILE, GOOGLE_CREDENTIALS_FILE, NOTELOOKLM_PATH

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _token_path():
    return Path(GOOGLE_TOKEN_FILE)


def _credentials_path():
    return Path(GOOGLE_CREDENTIALS_FILE)


def get_google_drive_service():
    creds = None
    token_path = _token_path()
    creds_path = _credentials_path()

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    "Arquivo de credenciais do Google não encontrado. "
                    "Crie o arquivo google_credentials.json a partir do OAuth do Google Cloud."
                )
            with open(creds_path, "r", encoding="utf-8") as file:
                client_config = json.load(file)
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def listar_pastas_google_drive(folder_id: str = None):
    service = get_google_drive_service()
    query = "mimeType='application/vnd.google-apps.folder'"
    if folder_id:
        query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder'"

    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType, webViewLink)",
            pageSize=100,
        )
        .execute()
    )

    return results.get("files", [])


def listar_arquivos_google_drive(folder_id: str = None, max_items: int = 50):
    service = get_google_drive_service()
    query = "trashed = false"
    if folder_id:
        query = f"'{folder_id}' in parents and trashed = false"

    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType, webViewLink)",
            pageSize=max_items,
        )
        .execute()
    )

    return results.get("files", [])


def download_arquivos_google_drive(destino: str, folder_id: str = None):
    service = get_google_drive_service()
    destino_path = Path(destino)
    destino_path.mkdir(parents=True, exist_ok=True)

    files = listar_arquivos_google_drive(folder_id=folder_id, max_items=100)
    baixados = []

    for item in files:
        file_id = item["id"]
        filename = Path(item["name"]).name
        if not filename or filename in {".", ".."}:
            continue
        request = service.files().get_media(fileId=file_id)
        file_path = destino_path / filename
        if file_path.exists():
            file_path = destino_path / f"{file_path.stem}_{file_id[:8]}{file_path.suffix}"

        with open(file_path, "wb") as fh:
            fh.write(request.execute())
        baixados.append(str(file_path))

    return baixados


def sync_notelooklm_from_google_drive(destino: str = None, folder_id: str = None, confirmado: bool = False):
    target = destino or os.path.join(NOTELOOKLM_PATH, "documentos_assistente", "drive_imports")
    folder = folder_id or GOOGLE_DRIVE_FOLDER_ID
    if not folder:
        raise ValueError("GOOGLE_DRIVE_FOLDER_ID não definido. Configure a pasta do seu NotelookLM no arquivo .env.")
    return download_arquivos_google_drive(target, folder_id=folder)


if __name__ == "__main__":
    print("Autenticação Google Drive para NotelookLM")
    print("1. Coloque o arquivo google_credentials.json na pasta do projeto.")
    print("2. Configure GOOGLE_DRIVE_FOLDER_ID no .env.")
    print("3. Execute este script. Ele abrirá o navegador para autenticar.")
    try:
        itens = listar_arquivos_google_drive(folder_id=GOOGLE_DRIVE_FOLDER_ID, max_items=20)
        print(f"Arquivos encontrados: {len(itens)}")
        for item in itens:
            print(f"- {item.get('name')} ({item.get('id')})")
    except Exception as exc:
        print(f"Erro de autenticação ou configuração: {exc}")
        print("Crie o arquivo google_credentials.json e configure o GOOGLE_DRIVE_FOLDER_ID antes de continuar.")
