"""Monta o grafo LangGraph. A ordem das etapas, o fan-out e o retry ficam só aqui.

Etapas implementadas: E0 (coleta), a entrada do modo --dev (referencia), a E1 (leitura), a E2
(extract) e a E3 (validate). As duas entradas seguem para a E1; o grafo termina na E3 com o
FactSheet assinado até a E4 existir.
"""

from langgraph.graph import END, START, StateGraph

from sunontent.nodes.coleta import coleta
from sunontent.nodes.extract import extract
from sunontent.nodes.leitura import leitura
from sunontent.nodes.referencia import referencia
from sunontent.nodes.validate import validate
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import PipelineState

K = 2  # orçamento de retry por célula; o valor lido no início do run entra no RunManifest
RODADAS_EXTRACAO = 3  # loop E2/E3: a extração e até 2 correções; depois disso o run aborta


def escolher_entrada(state: PipelineState) -> str:
    """Modo normal entra pela E0; modo dev, pelo nó de referência."""
    return "referencia" if state.manifest.modo == "dev" else "coleta"


def depois_da_validacao(state: PipelineState) -> str:
    """Sem claim reprovado, segue; com reprovado, volta à E2 até RODADAS_EXTRACAO e depois aborta."""
    if not state.correcoes:
        return END
    if state.rodada < RODADAS_EXTRACAO:
        return "extract"
    motivos = "; ".join(f"{claim_id}: {motivo}" for claim_id, motivo in state.correcoes.items())
    raise FalhaTerminal(
        f"E3: {len(state.correcoes)} claims seguem inválidos depois de {state.rodada} rodadas de extração. {motivos}"
    )


def construir_grafo(checkpointer=None):
    grafo = StateGraph(PipelineState)
    grafo.add_node("coleta", coleta)
    grafo.add_node("referencia", referencia)
    grafo.add_node("leitura", leitura)
    grafo.add_node("extract", extract)
    grafo.add_node("validate", validate)
    grafo.add_conditional_edges(START, escolher_entrada, ["coleta", "referencia"])
    grafo.add_edge("coleta", "leitura")
    grafo.add_edge("referencia", "leitura")
    grafo.add_edge("leitura", "extract")
    grafo.add_edge("extract", "validate")
    grafo.add_conditional_edges("validate", depois_da_validacao, ["extract", END])
    return grafo.compile(checkpointer=checkpointer)
