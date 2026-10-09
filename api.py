"""API HTTP local da Beatriz para automatacoes, incluindo Google Agenda."""
import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from config import BEATRIZ_API_KEY
from tools import criar_evento_agenda

app = FastAPI(title="Beatriz API", version="1.0.0")


class Agendamento(BaseModel):
    titulo: str = Field(min_length=1, max_length=200)
    data_hora: str = Field(min_length=1, max_length=100)
    descricao: str = Field(default="", max_length=5000)
    quando_lembrar: str = Field(default="", max_length=100)


def validar_api_key(api_key: str | None):
    if not BEATRIZ_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Configure BEATRIZ_API_KEY no arquivo .env antes de usar a API.",
        )
    if api_key != BEATRIZ_API_KEY:
        raise HTTPException(status_code=401, detail="Chave da API inválida.")


@app.get("/saude")
def saude():
    return {"status": "ok", "servico": "beatriz"}


@app.post("/agenda", status_code=201)
def agendar(
    pedido: Agendamento,
    x_api_key: str | None = Header(default=None),
):
    validar_api_key(x_api_key)
    resultado = criar_evento_agenda(
        titulo=pedido.titulo,
        data_hora=pedido.data_hora,
        descricao=pedido.descricao,
        quando_lembrar=pedido.quando_lembrar,
        confirmado=True,
    )
    if resultado.startswith("Não consegui") or resultado.startswith("Não foi possível"):
        raise HTTPException(status_code=502, detail=resultado)
    return {"sucesso": True, "mensagem": resultado}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host=os.getenv("BEATRIZ_API_HOST", "127.0.0.1"),
        port=int(os.getenv("BEATRIZ_API_PORT", "8000")),
        reload=False,
    )
