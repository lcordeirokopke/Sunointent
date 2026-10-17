"""Ponto de entrada: python -m sunontent [fonte] [--dev].

Sem a fonte, roda as três em sequência (copom, cvm e b3), um run por fonte.

Lê os parâmetros, confere as credenciais do Supabase, do Gemini e do Groq, gera o run_id, registra o run
e escolhe a entrada do grafo (E0 no modo normal, nó de referência no --dev). Ver docs/fluxos/ingestao.md.
"""

import argparse
import os
import secrets
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from sunontent import graph, llm, manifesto, persistencia
from sunontent.ingestao import comum
from sunontent.metrics import llm_judge
from sunontent.retentativa import Falha
from sunontent.schemas import Fonte, PipelineState, RunManifest

RAIZ = Path(__file__).resolve().parent.parent
FONTES = ("copom", "cvm", "b3")


def gerar_run_id(fonte: str, momento: datetime) -> str:
    """<fonte>_<AAAAMMDDTHHMMSSZ>_<6 hex>"""
    return f"{fonte}_{momento:%Y%m%dT%H%M%SZ}_{secrets.token_hex(3)}"


def _ler_parametros(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m sunontent", description=__doc__.splitlines()[0])
    parser.add_argument(
        "fonte", nargs="?", choices=FONTES, help="fonte do documento; sem ela, roda copom, cvm e b3 em sequência"
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="usa o documento de referência de tests/fixtures/referencia/ em vez de baixar",
    )
    return parser.parse_args(argv)


def _abortar(
    repositorio: persistencia.Repositorio,
    raiz_dados: Path,
    manifest: RunManifest,
    classe: str,
    mensagem: str,
    etapa: str | None,
) -> None:
    """Mostra o erro e registra o aborto, com a etapa do erro. Uma falha ao registrar não esconde o erro original."""
    print(f"abortado ({classe}): {mensagem}", file=sys.stderr)
    try:
        persistencia.abortar_run(repositorio, raiz_dados, manifest, classe, mensagem, etapa)
    except Falha as falha:
        print(f"o aborto não foi registrado: {falha.mensagem}", file=sys.stderr)


def _varrer_orfaos(repositorio: persistencia.Repositorio, raiz_dados: Path) -> None:
    """Fecha como abortado o run antigo que ficou em_andamento (processo parado ou morto). Uma falha aqui
    não impede o novo run."""
    try:
        abortados = persistencia.abortar_orfaos(repositorio, raiz_dados, comum.agora_utc())
    except Falha as falha:
        print(f"varredura de runs em andamento não feita: {falha.mensagem}", file=sys.stderr)
        return
    for run_id in abortados:
        print(f"run {run_id} marcado como abortado: {persistencia.MENSAGEM_ORFAO}")


def _com_custo(manifest: RunManifest, cliente_llm: llm.ClienteLLM) -> RunManifest:
    """Manifest com o custo acumulado pelas chamadas de LLM do run, para a cópia local."""
    return manifest.model_copy(
        update={
            "custo_total_tokens": cliente_llm.custo_total_tokens,
            "custo_total_brl": cliente_llm.custo_total_brl,
        }
    )


def _celula_da_entrada(evento: dict):
    """A CelulaState na entrada de uma tarefa do fan-out, ou None se a tarefa não é de uma célula."""
    entrada = evento.get("input")
    celula = entrada.get("celula") if isinstance(entrada, dict) else getattr(entrada, "celula", None)
    return celula if hasattr(celula, "celula_id") else None


def _rodar_grafo(grafo, estado: PipelineState, config: dict, progresso: dict | None = None) -> dict:
    """Roda o grafo e escreve uma linha no início e no fim de cada etapa e de cada passo de célula. Devolve o
    estado final.

    Formato, fora das células: `etapa <no>: iniciada` e `etapa <no>: concluída`. Nas células, que rodam em
    paralelo: `celula <celula_id> <no> <tentativa>: iniciada|concluída` para cada nó do subgrafo e
    `celula <celula_id>: <ESTADO>` quando a célula termina. A etapa que falha não tem a linha de fim: a exceção
    sobe e o `abortado (...)` vem em seguida. O dashboard (app/) lê essas linhas.
    `progresso["etapa"]` guarda o último nó iniciado: na falha, é o nó em execução.
    """
    resultado = None
    celula_da_tarefa: dict[str, str] = {}  # id da tarefa `celula` do grafo principal -> celula_id
    tentativa_da_tarefa: dict[str, int] = {}  # id da tarefa de um nó do subgrafo -> tentativa da linha de início
    for namespace, modo, evento in grafo.stream(estado, config, stream_mode=["tasks", "values"], subgraphs=True):
        if modo == "values":
            if not namespace:
                resultado = evento
            continue
        if evento.get("error"):
            continue  # tarefa que falhou: sem linha de fim, a exceção sobe do stream
        iniciada = "result" not in evento
        if namespace:
            celula_id = celula_da_tarefa.get(namespace[0].partition(":")[2])
            if celula_id is None:
                continue
            if iniciada:
                tentativa_da_tarefa[evento["id"]] = (
                    _celula_da_entrada(evento).tentativa + (evento["name"] == "synth")
                )
                if progresso is not None:
                    progresso["etapa"] = evento["name"]
            print(
                f"celula {celula_id} {evento['name']} {tentativa_da_tarefa[evento['id']]}: "
                f"{'iniciada' if iniciada else 'concluída'}",
                flush=True,
            )
        elif evento["name"] == "celula":
            if iniciada:
                celula_da_tarefa[evento["id"]] = _celula_da_entrada(evento).celula_id
            else:
                (celula,) = evento["result"]["celulas"].values()
                print(f"celula {celula.celula_id}: {celula.estado}", flush=True)
        else:
            if progresso is not None:
                progresso["etapa"] = evento["name"]
            print(f"etapa {evento['name']}: {'iniciada' if iniciada else 'concluída'}", flush=True)
    return resultado


def executar(
    fonte: Fonte,
    *,
    dev: bool,
    repositorio: persistencia.Repositorio,
    cliente_llm: llm.ClienteLLM,
    raiz: Path = RAIZ,
    cliente_http=None,
    relogio: Callable[[], datetime] = comum.agora_utc,
    juiz=None,
) -> int:
    """Roda um run. Devolve o código de saída: 0 concluído, 1 abortado ou concluído com célula em ERRO_INFRA.
    Sem `juiz` (metrics.llm_judge.criar_juiz), a E6 roda só as verificações determinísticas."""
    momento = relogio()
    run_id = gerar_run_id(fonte, momento)
    raiz_dados = raiz / "data"
    manifest = manifesto.montar(
        raiz,
        run_id=run_id,
        timestamp=momento,
        modo="dev" if dev else "normal",
        fonte=fonte,
        k=graph.K,
        max_concorrencia=graph.MAX_CONCORRENCIA,
    )
    persistencia.criar_run(repositorio, manifest)
    persistencia.gravar_manifest_local(raiz_dados, manifest, "em_andamento")
    print(f"run {run_id} ({manifest.modo})")

    configuravel = {
        "repositorio": repositorio,
        "raiz": raiz,
        "raiz_dados": raiz_dados,
        "llm": cliente_llm,
        "pasta_referencia": raiz / "tests" / "fixtures" / "referencia",
    }
    if cliente_http is not None:
        configuravel["cliente_http"] = cliente_http
    if juiz is not None:
        configuravel["juiz"] = juiz

    progresso: dict = {}
    try:
        resultado = _rodar_grafo(
            graph.construir_grafo(),
            PipelineState(run_id=run_id, manifest=manifest),
            graph.config_execucao(configuravel),
            progresso,
        )
    except Falha as falha:
        _abortar(
            repositorio, raiz_dados, _com_custo(manifest, cliente_llm), falha.classe, falha.mensagem,
            progresso.get("etapa"),
        )
        return 1
    except Exception as erro:
        # Bug fora do contrato de falhas: registra como terminal, com o tipo da exceção, e propaga.
        _abortar(
            repositorio, raiz_dados, _com_custo(manifest, cliente_llm), "terminal", f"{type(erro).__name__}: {erro}",
            progresso.get("etapa"),
        )
        raise

    documento = resultado["documento_fonte"]
    manifest = _com_custo(manifest, cliente_llm).model_copy(
        update={
            "doc_id": documento.doc_id,
            "doc_sha256": documento.doc_sha256,
            "url_origem": documento.url_origem,
            "coletado_em": documento.coletado_em,
            "coleta_id": documento.coleta_id,
        }
    )
    persistencia.concluir_run(repositorio, raiz_dados, manifest)
    claims = len(resultado["factsheet"].claims)
    # Com o run concluído, as correções que restam são os claims descartados depois da última rodada.
    descartados = len(resultado["correcoes"])
    plano = resultado["plano"]
    contagem = resultado["contagem_celulas"]
    celulas = ", ".join(f"{n} {estado}" for estado, n in contagem.items() if n)
    print(
        f"concluído: {documento.doc_id} ({documento.doc_sha256[:12]}), {claims} claims,"
        + (f" {descartados} {'descartado' if descartados == 1 else 'descartados'}," if descartados else "")
        + f" plano em {plano.rodadas} {'rodada' if plano.rodadas == 1 else 'rodadas'}"
        + (" (com aviso)," if plano.aviso else ",")
        + f" {sum(contagem.values())} células ({celulas}),"
        + f" {cliente_llm.custo_total_tokens} tokens, R$ {cliente_llm.custo_total_brl:.4f}"
    )
    return 1 if contagem.get("ERRO_INFRA") else 0


def main(argv: Sequence[str] | None = None) -> int:
    parametros = _ler_parametros(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(RAIZ / ".env")
        repositorio = persistencia.criar_repositorio_supabase(os.environ)
        llm.conferir_credenciais(os.environ)
        _varrer_orfaos(repositorio, RAIZ / "data")
        # Um cliente por run, para que o custo acumulado seja só daquele run.
        codigos = []
        for fonte in [parametros.fonte] if parametros.fonte else FONTES:
            cliente_llm = llm.criar_cliente(RAIZ, os.environ)
            codigos.append(executar(
                fonte,
                dev=parametros.dev,
                repositorio=repositorio,
                cliente_llm=cliente_llm,
                juiz=llm_judge.criar_juiz(cliente_llm, RAIZ),
            ))
        return int(any(codigos))
    except Falha as falha:
        # Antes de existir a linha em runs: credencial, conexão ou config inválida.
        print(f"erro ({falha.classe}): {falha.mensagem}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
