"""E3. Validador de Âncoras: chama extracao/validador.py sobre o FactSheet da E2.

Os claims que a E2 não ancorou também reprovam, com o motivo da E2. Grava o resultado de cada
rodada em validacoes_ancora; com todos aprovados, assina e grava o FactSheet. Com algum
reprovado, devolve os motivos em `correcoes`; o graph.py decide se volta à E2 ou vai para a quarentena.
Dependências vêm de config["configurable"]: repositorio e raiz_dados.
"""

from pathlib import Path

from sunontent import persistencia
from sunontent.extracao import validador


def validate(state, config) -> dict:
    configuravel = config["configurable"]
    repositorio, raiz_dados = configuravel["repositorio"], Path(configuravel["raiz_dados"])
    reprovados = {**validador.validar(state.documento, state.factsheet), **state.pendencias}
    resultados = [(claim_id, reprovados.get(claim_id)) for claim_id in sorted(state.propostas)]
    persistencia.gravar_validacao(repositorio, raiz_dados, state.run_id, state.rodada, resultados)
    if reprovados:
        return {"correcoes": dict(sorted(reprovados.items()))}
    assinado = state.factsheet.model_copy(update={"assinado": True})
    persistencia.gravar_factsheet(repositorio, raiz_dados, state.run_id, assinado)
    return {"factsheet": assinado, "correcoes": {}}
