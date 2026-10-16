"""Fan-out das 9 células: o `Send` com `max_concurrency`, o isolamento de falhas, o fechamento do run, as linhas do
terminal e a trava do custo do `ClienteLLM`. As células são simuladas (`SubgrafoFalso`), sem LLM; o loop de uma célula
está em test_router.py."""

import threading
import time
from typing import Annotated

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel

from conftest import SubgrafoFalso, cliente_falso
from sunontent import graph
from sunontent.nodes.fechamento import fechamento
from sunontent.retentativa import Falha, FalhaTerminal
from sunontent.schemas import CelulaState, PipelineState, celula_id
from test_main_graph import MOMENTO, RAIZ, raiz, repo, rodar, manifest_local  # noqa: F401  (fixtures)

CELULAS = [celula_id(n, f) for n in ("iniciante", "intermediario", "avancado") for f in ("artigo", "carrossel", "roteiro")]


# Mecânica do Send (grafo mínimo)


class Pai(BaseModel):
    ids: list[str] = []
    resultado: Annotated[dict[str, str], lambda a, b: {**a, **b}] = {}


class Ramo(BaseModel):
    id: str


def grafo_de_ramos(trabalho):
    """Um `Send` por id; `trabalho(id)` roda dentro do ramo e devolve o texto do resultado."""

    def ramo(state: Ramo) -> dict:
        return {"resultado": {state.id: trabalho(state.id)}}

    grafo = StateGraph(Pai)
    grafo.add_node("ramo", ramo)
    grafo.add_conditional_edges(START, lambda s: [Send("ramo", Ramo(id=i)) for i in s.ids], ["ramo"])
    grafo.add_edge("ramo", END)
    return grafo.compile()


def test_max_concurrency_limita_os_ramos_do_send():
    ativos, pico, trava = 0, 0, threading.Lock()

    def trabalho(_):
        nonlocal ativos, pico
        with trava:
            ativos += 1
            pico = max(pico, ativos)
        time.sleep(0.05)
        with trava:
            ativos -= 1
        return "ok"

    ids = [str(i) for i in range(9)]
    final = grafo_de_ramos(trabalho).invoke(Pai(ids=ids), {"max_concurrency": 3})
    assert pico == 3 and sorted(final["resultado"]) == sorted(ids)

    ativos = pico = 0
    grafo_de_ramos(trabalho).invoke(Pai(ids=ids))
    assert pico > 3  # sem o limite, os ramos passam de 3: é o max_concurrency que segura


def test_falha_tratada_no_ramo_vira_estado_e_os_outros_ramos_terminam():
    def trabalho(i):
        try:
            if i == "4":
                raise FalhaTerminal("falha simulada")
        except Falha as falha:
            return f"ERRO_INFRA: {falha.mensagem}"
        return "APROVADA"

    ids = [str(i) for i in range(9)]
    final = grafo_de_ramos(trabalho).invoke(Pai(ids=ids), {"max_concurrency": 3})
    assert final["resultado"].pop("4") == "ERRO_INFRA: falha simulada"
    assert set(final["resultado"].values()) == {"APROVADA"} and len(final["resultado"]) == 8


# Grafo principal


def test_constante_e_config_de_execucao():
    assert graph.MAX_CONCORRENCIA == 3
    assert graph.config_execucao({"a": 1}) == {"configurable": {"a": 1}, "max_concurrency": 3}


def test_as_9_celulas_rodam_com_no_maximo_3_em_paralelo(raiz, repo, monkeypatch):
    falso = SubgrafoFalso(atraso=0.05)
    monkeypatch.setattr(graph, "construir_grafo_celula", falso.construir)
    assert rodar(raiz, repo, dev=True) == 0
    assert falso.pico == 3


def test_manifest_registra_a_concorrencia(raiz, repo, celulas_aprovadas):
    assert rodar(raiz, repo, dev=True) == 0
    (run,) = repo.runs.values()
    assert run["manifest"].max_concorrencia == 3
    assert manifest_local(raiz)["max_concorrencia"] == 3


def test_linhas_do_terminal_por_celula_e_resumo_final(raiz, repo, monkeypatch, capsys):
    falso = SubgrafoFalso(estados={CELULAS[0]: "REPROVADA", CELULAS[4]: "REPROVADA"})
    monkeypatch.setattr(graph, "construir_grafo_celula", falso.construir)
    assert rodar(raiz, repo, dev=True) == 0
    linhas = capsys.readouterr().out.splitlines()
    por_celula = [l for l in linhas if l.startswith("celula ")]
    for c in CELULAS:
        proprias = [l for l in por_celula if l.startswith(f"celula {c}")]
        assert proprias[:6] == [
            f"celula {c} {no} {tentativa}: {estado}"
            for no, tentativa in (("synth", 1), ("evaluate", 1), ("router", 1))
            for estado in ("iniciada", "concluída")
        ]
    assert "celula iniciante__artigo: REPROVADA" in por_celula and "celula iniciante__roteiro: APROVADA" in por_celula
    assert len(por_celula) == 9 * 7
    assert not any(l.startswith(("etapa synth", "etapa evaluate", "etapa router", "etapa celula")) for l in linhas)
    final = [l for l in linhas if l.startswith("concluído: ")][0]
    assert "9 células (7 APROVADA, 2 REPROVADA)," in final


def test_celula_em_erro_infra_nao_para_as_outras_e_o_codigo_de_saida_e_1(raiz, repo, monkeypatch, capsys):
    falso = SubgrafoFalso(estados={CELULAS[2]: "ERRO_INFRA"})
    monkeypatch.setattr(graph, "construir_grafo_celula", falso.construir)
    assert rodar(raiz, repo, dev=True) == 1
    (run,) = repo.runs.values()
    assert run["status"] == "concluido"
    saida = capsys.readouterr().out
    assert f"celula {CELULAS[2]}: ERRO_INFRA" in saida
    assert "9 células (8 APROVADA, 1 ERRO_INFRA)," in saida
    assert saida.count(": APROVADA") == 8


def test_excecao_que_nao_e_falha_na_celula_aborta_o_run_com_o_no(raiz, repo, monkeypatch):
    falso = SubgrafoFalso(excecao=RuntimeError("bug"))
    monkeypatch.setattr(graph, "construir_grafo_celula", falso.construir)
    with pytest.raises(RuntimeError):
        rodar(raiz, repo, dev=True)
    (run,) = repo.runs.values()
    assert (run["status"], run["erro_mensagem"], run["etapa_erro"]) == ("abortado", "RuntimeError: bug", "synth")


# Fechamento


def estado_com(**estados):
    celulas = {
        c: CelulaState.model_validate({
            "celula_id": c, "nivel": c.split("__")[0], "formato": c.split("__")[1],
            **({"estado": estados[c]} if c in estados else {"estado": "APROVADA"}),
            **({"motivo_reprovacao": "k_esgotado"} if estados.get(c) == "REPROVADA" else {}),
            **({"erro_classe": "terminal", "erro_mensagem": "x"} if estados.get(c) == "ERRO_INFRA" else {}),
        })
        for c in CELULAS
    }
    return PipelineState.model_construct(run_id="x", celulas=celulas)


def test_fechamento_conta_os_estados_finais():
    estado = estado_com(**{CELULAS[0]: "REPROVADA", CELULAS[1]: "ERRO_INFRA", CELULAS[2]: "ERRO_INFRA"})
    assert fechamento(estado) == {"contagem_celulas": {"APROVADA": 6, "REPROVADA": 1, "ERRO_INFRA": 2}}


def test_fechamento_com_celula_sem_estado_final_e_bug():
    with pytest.raises(ValueError, match=CELULAS[3]):
        fechamento(estado_com(**{CELULAS[3]: "AVALIANDO"}))


# Trava do custo do ClienteLLM


def test_custo_do_cliente_e_exato_com_chamadas_em_paralelo():
    from pydantic import BaseModel as Modelo

    class Saida(Modelo):
        ok: bool

    threads, por_thread = 8, 25
    llm = cliente_falso(['{"ok": true}'] * (threads * por_thread))

    def chamar():
        for _ in range(por_thread):
            llm.chamar("synthesizer", "p", Saida, run_id="r", registrar_span=lambda span: None)

    trabalhadores = [threading.Thread(target=chamar) for _ in range(threads)]
    for t in trabalhadores:
        t.start()
    for t in trabalhadores:
        t.join()
    chamadas = threads * por_thread
    assert llm.custo_total_tokens == chamadas * 1200  # 1000 de entrada e 200 de saída por chamada
    assert llm.custo_total_brl == llm.custo_brl("modelo-falso", 1000, 200) * chamadas
