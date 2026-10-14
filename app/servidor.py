"""Servidor do dashboard: inicia o pipeline, transmite o terminal por SSE e lê os runs do Supabase.

Escuta só em 127.0.0.1 (ver app/__main__.py): os botões iniciam processos na máquina do pipeline.
A leitura passa por persistencia.ConsultaSupabase, criada na primeira consulta a partir do .env.
"""

import asyncio
import json
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import execucao
from sunontent import graph, persistencia
from sunontent.retentativa import Falha

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = Path(__file__).resolve().parent / "static"


class PedidoExecucao(BaseModel):
    fonte: str | None = None  # sem fonte, o pipeline roda copom, cvm e b3 em sequência
    dev: bool = False


class _Consulta:
    """Cria a ConsultaSupabase na primeira leitura; uma falha não fica guardada e a próxima leitura tenta de novo."""

    def __init__(self, raiz: Path, consulta=None):
        self._raiz = raiz
        self._consulta = consulta
        self._trava = threading.Lock()

    def __call__(self):
        with self._trava:
            if self._consulta is None:
                from dotenv import load_dotenv

                load_dotenv(self._raiz / ".env")
                self._consulta = persistencia.criar_consulta_supabase(os.environ)
            return self._consulta


def _ler(obter_consulta, leitura):
    """Executa a leitura; uma Falha do banco vira 503 com a mensagem."""
    try:
        return leitura(obter_consulta())
    except Falha as falha:
        raise HTTPException(503, f"Supabase ({falha.classe}): {falha.mensagem}") from falha


def criar_app(raiz: Path = RAIZ, gerenciador: execucao.Gerenciador | None = None, consulta=None) -> FastAPI:
    """`consulta`: objeto com listar_runs() e detalhar_run(run_id); sem ele, ConsultaSupabase do .env."""
    app = FastAPI(title="Sunontent")
    gerenciador = gerenciador or execucao.Gerenciador(raiz)
    obter_consulta = _Consulta(raiz, consulta)

    @app.get("/")
    def pagina():
        return FileResponse(ESTATICOS / "index.html")

    @app.get("/api/grafo")
    def grafo():
        """Nós e arestas do grafo montado por graph.construir_grafo(), sem executar nada."""
        desenho = graph.construir_grafo().get_graph()
        return {
            "nos": [no for no in desenho.nodes if not no.startswith("__")],
            "arestas": [
                {"de": a.source, "para": a.target, "condicional": a.conditional} for a in desenho.edges
            ],
            "rodadas_extracao": graph.RODADAS_EXTRACAO,
            "rodadas_plano": graph.RODADAS_PLANO,
            "k": graph.K,
        }

    @app.post("/api/execucao", status_code=201)
    def iniciar(pedido: PedidoExecucao):
        try:
            atual = gerenciador.iniciar(pedido.fonte, pedido.dev)
        except ValueError as erro:
            raise HTTPException(422, str(erro)) from erro
        except RuntimeError as erro:
            raise HTTPException(409, str(erro)) from erro
        return atual.resumo()

    @app.get("/api/execucao")
    def atual():
        """A execução atual (ou a última), com todos os eventos, para reconstruir a página."""
        if gerenciador.atual is None:
            return None
        return {**gerenciador.atual.resumo(), "eventos": gerenciador.atual.eventos_desde(0)}

    @app.post("/api/execucao/parar")
    def parar():
        if gerenciador.atual is None or gerenciador.atual.terminou:
            raise HTTPException(409, "nenhuma execução em andamento")
        gerenciador.atual.parar()
        return gerenciador.atual.resumo()

    @app.get("/api/execucao/eventos")
    async def eventos(request: Request, desde: int = 0):
        """SSE com os eventos a partir de `desde`; termina depois do evento `fim`."""
        atual = gerenciador.atual
        if atual is None:
            raise HTTPException(404, "nenhuma execução")
        ultimo = request.headers.get("last-event-id")
        indice = int(ultimo) + 1 if ultimo and ultimo.isdigit() else desde

        async def transmitir():
            nonlocal indice
            while True:
                novos = atual.eventos_desde(indice)
                for evento in novos:
                    yield f"id: {evento['n']}\ndata: {json.dumps(evento, ensure_ascii=False)}\n\n"
                indice += len(novos)
                if novos and novos[-1]["tipo"] == "fim":
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.2)

        return StreamingResponse(transmitir(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/runs")
    def listar_runs():
        return _ler(obter_consulta, lambda consulta: consulta.listar_runs())

    @app.get("/api/runs/{run_id}")
    def detalhar_run(run_id: str):
        detalhe = _ler(obter_consulta, lambda consulta: consulta.detalhar_run(run_id))
        if detalhe is None:
            raise HTTPException(404, f"run não encontrado: {run_id}")
        return detalhe

    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    return app


app = criar_app()
