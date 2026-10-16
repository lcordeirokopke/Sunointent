"""Fechamento do run: depois das 9 células, conta quantas terminaram em cada estado final.

Não grava nada: o estado final de cada célula já foi gravado pela E7, e o `__main__.py` conclui o run. Célula que
chega sem estado final é bug do grafo, não falha de infraestrutura.
"""

from sunontent.schemas import ESTADOS_FINAIS, PipelineState


def fechamento(state: PipelineState) -> dict:
    sem_final = sorted(c.celula_id for c in state.celulas.values() if c.estado not in ESTADOS_FINAIS)
    if sem_final:
        raise ValueError(f"fechamento: células sem estado final: {', '.join(sem_final)}")
    return {"contagem_celulas": {e: sum(c.estado == e for c in state.celulas.values()) for e in ESTADOS_FINAIS}}
