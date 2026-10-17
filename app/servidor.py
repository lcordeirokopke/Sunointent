"""Servidor do dashboard: inicia o pipeline, transmite o terminal por SSE e lê os runs do Supabase.

Escuta só em 127.0.0.1 (ver app/__main__.py): os botões iniciam processos na máquina do pipeline.
A leitura passa por persistencia.ConsultaSupabase, criada na primeira consulta a partir do .env.
"""

import asyncio
import json
import os
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import execucao, slide_svg
from sunontent import graph, levelspec, persistencia
from sunontent.retentativa import Falha

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = Path(__file__).resolve().parent / "static"


class PedidoExecucao(BaseModel):
    fonte: str | None = None  # sem fonte, o pipeline roda copom, cvm e b3 em sequência
    dev: bool = False


class _Recurso:
    """Cria na primeira chamada, a partir do .env, a conexão com o Supabase; uma falha não fica guardada e a
    próxima chamada tenta de novo."""

    def __init__(self, raiz: Path, criar, existente=None):
        self._raiz = raiz
        self._criar = criar
        self._objeto = existente
        self._trava = threading.Lock()

    def __call__(self):
        with self._trava:
            if self._objeto is None:
                from dotenv import load_dotenv

                load_dotenv(self._raiz / ".env")
                self._objeto = self._criar(os.environ)
            return self._objeto


def _sem_pseudonos(desenho) -> tuple[list[str], list[dict]]:
    """Nós e arestas do desenho do grafo (xray) com os nós do subgrafo da célula sem o prefixo `celula:` e sem os
    pseudonós `__start__` e `__end__` do subgrafo: a aresta que chega ao início do subgrafo passa a chegar ao primeiro
    nó dele, e a que sai do fim passa a sair do último."""
    def nome(no: str) -> str:
        return no.rpartition(":")[2] if ":" in no else no

    def pseudo(no: str) -> bool:
        return ":" in no and nome(no) in ("__start__", "__end__")

    arestas = [(a.source, a.target, a.conditional) for a in desenho.edges]
    for no in [n for n in desenho.nodes if pseudo(n)]:
        entram = [a for a in arestas if a[1] == no]
        saem = [a for a in arestas if a[0] == no]
        arestas = [a for a in arestas if no not in (a[0], a[1])]
        arestas += [(de, para, cond) for de, _, cond in entram for _, para, _ in saem]
    nos = [nome(n) for n in desenho.nodes if not nome(n).startswith("__") and not pseudo(n)]
    return nos, [{"de": nome(de), "para": nome(para), "condicional": cond} for de, para, cond in arestas]


def _ler(obter_consulta, leitura):
    """Executa a leitura; uma Falha do banco vira 503 com a mensagem."""
    try:
        return leitura(obter_consulta())
    except Falha as falha:
        raise HTTPException(503, f"Supabase ({falha.classe}): {falha.mensagem}") from falha


def _faixas_ifl_do_artigo(raiz: Path) -> dict[str, dict] | None:
    """As faixas de IFL do artigo por nível (`sinal_ifl` do LevelSpec), para o dashboard classificar o artigo de
    qualquer run pelo `ifl` gravado. None se o LevelSpec não pode ser lido."""
    try:
        specs = levelspec.carregar_todos(raiz)
    except Falha:
        return None
    return {n: s.sinal_ifl.artigo.model_dump() for n, s in specs.items() if s.sinal_ifl is not None} or None


def criar_app(raiz: Path = RAIZ, gerenciador: execucao.Gerenciador | None = None, consulta=None) -> FastAPI:
    """`consulta`: objeto com listar_runs(rodadas_extracao=), detalhar_run(run_id, rodadas_extracao=) e
    peca(run_id, celula_id, tentativa); sem ele, ConsultaSupabase do .env."""
    app = FastAPI(title="Sunontent")
    gerenciador = gerenciador or execucao.Gerenciador(raiz)
    obter_consulta = _Recurso(raiz, persistencia.criar_consulta_supabase, consulta)

    @app.get("/")
    def pagina():
        return FileResponse(ESTATICOS / "index.html")

    @app.get("/api/grafo")
    def grafo():
        """Nós e arestas do grafo montado por graph.construir_grafo(), com o subgrafo da célula aberto (xray), sem
        executar nada."""
        nos, arestas = _sem_pseudonos(graph.construir_grafo().get_graph(xray=True))
        return {
            "nos": nos,
            "arestas": arestas,
            "rodadas_extracao": graph.RODADAS_EXTRACAO,
            "rodadas_plano": graph.RODADAS_PLANO,
            "k": graph.K,
            "max_concorrencia": graph.MAX_CONCORRENCIA,
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
        return _ler(obter_consulta, lambda consulta: consulta.listar_runs(rodadas_extracao=graph.RODADAS_EXTRACAO))

    @app.get("/api/balanco")
    def balanco():
        return _ler(obter_consulta, lambda consulta: consulta.balanco())

    @app.get("/api/biblioteca")
    def biblioteca():
        return _ler(obter_consulta, lambda consulta: consulta.biblioteca())

    @app.get("/api/runs/{run_id}/pecas/{celula_id}")
    def peca_da_celula(run_id: str, celula_id: str):
        peca = _ler(obter_consulta, lambda consulta: consulta.peca(run_id, celula_id))
        if peca is None:
            raise HTTPException(404, f"peça não encontrada: {run_id} {celula_id}")
        return peca

    @app.get("/api/runs/{run_id}")
    def detalhar_run(run_id: str):
        detalhe = _ler(obter_consulta, lambda consulta: consulta.detalhar_run(run_id, rodadas_extracao=graph.RODADAS_EXTRACAO))
        if detalhe is None:
            raise HTTPException(404, f"run não encontrado: {run_id}")
        return {**detalhe, "sinal_ifl_artigo": _faixas_ifl_do_artigo(raiz)}

    @app.get("/api/runs/{run_id}/carrossel/{celula_id}/{arquivo}")
    def slide(run_id: str, celula_id: str, arquivo: str, tentativa: int | None = None, baixar: bool = False):
        """SVG do slide `n` (1-indexado, `<n>.svg`) do carrossel da célula, na última tentativa ou na pedida. Gerado
        na hora a partir da peça gravada; nada é salvo."""
        achado = re.fullmatch(r"(\d+)\.svg", arquivo)
        if achado is None or int(achado.group(1)) < 1:
            raise HTTPException(404, f"slide inválido: {arquivo}")
        n = int(achado.group(1))
        peca = _ler(obter_consulta, lambda consulta: consulta.peca(run_id, celula_id, tentativa))
        if peca is None:
            raise HTTPException(404, f"peça não encontrada: {run_id} {celula_id}")
        if peca["formato"] != "carrossel":
            raise HTTPException(404, f"a célula {celula_id} não é um carrossel")
        slides = peca["payload"]["slides"]
        if n > len(slides):
            raise HTTPException(404, f"o carrossel tem {len(slides)} slides")
        item = slides[n - 1]
        desenho = slide_svg.montar_slide(
            texto=item["texto"], papel=item["papel"], claim_ids=item["claim_ids"], celula_id=celula_id,
            tentativa=peca["tentativa"], posicao=n, total=len(slides),
        )
        nome = slide_svg.nome_do_arquivo(celula_id, peca["tentativa"], n)
        return Response(
            desenho.svg,
            media_type="image/svg+xml",
            headers={
                "Content-Disposition": f'{"attachment" if baixar else "inline"}; filename="{nome}"',
                "X-Estouro-Altura": str(desenho.estouro_altura).lower(),
                "Cache-Control": "no-store",
            },
        )

    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    return app


app = criar_app()
