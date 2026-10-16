"""Monta o grafo LangGraph. A ordem das etapas, o fan-out e o retry ficam só aqui.

Etapas implementadas: E0 (coleta), a entrada do modo --dev (referencia), a E1 (leitura), a E2
(extract), a E3 (validate), a quarentena do loop E2/E3 e a E4 (adapt). As duas entradas seguem para a E1;
o FactSheet assinado, na E3 ou na quarentena, segue para a E4, e o grafo termina com o plano e as 9 células
em PENDENTE.

O subgrafo de uma célula (`construir_grafo_celula`), ainda sem ligação com o grafo principal, roda a E5 (synth),
a E6 (evaluate) e a E7 (router) com o refinement loop: a falha da E5 vai direto à E7, o retry volta à E5 e o
estado final termina o subgrafo.
"""

from functools import partial

from langgraph.graph import END, START, StateGraph

from sunontent.nodes.adapt import adapt
from sunontent.nodes.coleta import coleta
from sunontent.nodes.extract import extract
from sunontent.nodes.leitura import leitura
from sunontent.nodes.quarentena import quarentena
from sunontent.nodes.evaluate import evaluate
from sunontent.nodes.referencia import referencia
from sunontent.nodes.router import ESTADOS_FINAIS, router
from sunontent.nodes.synth import synth
from sunontent.nodes.validate import validate
from sunontent.schemas import CelulaGrafoState, PipelineState

K = 2  # orçamento de retry por célula; o valor lido no início do run entra no RunManifest
RODADAS_EXTRACAO = 3  # loop E2/E3: a extração e até 2 correções; depois disso os reprovados vão para a quarentena
RODADAS_PLANO = 3  # gate da E4, dentro do nó adapt: o plano e até 2 correções; depois disso o plano segue com aviso


def escolher_entrada(state: PipelineState) -> str:
    """Modo normal entra pela E0; modo dev, pelo nó de referência."""
    return "referencia" if state.manifest.modo == "dev" else "coleta"


def depois_da_validacao(state: PipelineState) -> str:
    """Sem claim reprovado, segue para a E4; com reprovado, volta à E2 até RODADAS_EXTRACAO e depois vai para a
    quarentena."""
    if not state.correcoes:
        return "adapt"
    if state.rodada < RODADAS_EXTRACAO:
        return "extract"
    return "quarentena"


def construir_grafo():
    grafo = StateGraph(PipelineState)
    grafo.add_node("coleta", coleta)
    grafo.add_node("referencia", referencia)
    grafo.add_node("leitura", leitura)
    grafo.add_node("extract", extract)
    grafo.add_node("validate", validate)
    grafo.add_node("quarentena", quarentena)
    grafo.add_node("adapt", partial(adapt, rodadas=RODADAS_PLANO))
    grafo.add_conditional_edges(START, escolher_entrada, ["coleta", "referencia"])
    grafo.add_edge("coleta", "leitura")
    grafo.add_edge("referencia", "leitura")
    grafo.add_edge("leitura", "extract")
    grafo.add_edge("extract", "validate")
    grafo.add_conditional_edges("validate", depois_da_validacao, ["extract", "quarentena", "adapt"])
    grafo.add_edge("quarentena", "adapt")
    grafo.add_edge("adapt", END)
    return grafo.compile()


def depois_da_sintese(state: CelulaGrafoState) -> str:
    """Com falha na E5, vai direto à E7; com a peça, à E6."""
    return "router" if state.falha is not None else "evaluate"


def depois_do_router(state: CelulaGrafoState) -> str:
    """Estado final termina a célula; senão, o relatório corretivo volta à E5."""
    return END if state.celula.estado in ESTADOS_FINAIS else "synth"


def construir_grafo_celula():
    grafo = StateGraph(CelulaGrafoState)
    grafo.add_node("synth", synth)
    grafo.add_node("evaluate", evaluate)
    grafo.add_node("router", partial(router, K=K))
    grafo.add_edge(START, "synth")
    grafo.add_conditional_edges("synth", depois_da_sintese, ["evaluate", "router"])
    grafo.add_edge("evaluate", "router")
    grafo.add_conditional_edges("router", depois_do_router, ["synth", END])
    return grafo.compile()
