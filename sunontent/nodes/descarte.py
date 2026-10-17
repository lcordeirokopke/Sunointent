"""Descarte do loop E2/E3: chama extracao/validador.py depois da última rodada com claim reprovado.

Assina e grava o FactSheet só com os claims válidos, que segue no grafo; os reprovados ficam de fora, com o
motivo na linha da rodada em validacoes_ancora. Sem nenhum claim válido, aborta o run
(docs/fluxos/extracao.md, Descarte).
Dependências vêm de config["configurable"]: repositorio e raiz_dados.
"""

from pathlib import Path

from sunontent import persistencia
from sunontent.extracao import validador
from sunontent.retentativa import FalhaTerminal


def descarte(state, config) -> dict:
    configuravel = config["configurable"]
    repositorio, raiz_dados = configuravel["repositorio"], Path(configuravel["raiz_dados"])
    assinado = validador.descartar_reprovados(state.factsheet, state.correcoes)
    if not assinado.claims:
        motivos = "; ".join(f"{claim_id}: {motivo}" for claim_id, motivo in sorted(state.correcoes.items()))
        raise FalhaTerminal(
            f"E3: nenhum claim válido depois de {state.rodada} rodadas de extração; "
            f"{len(state.correcoes)} claims descartados. {motivos}"
        )
    persistencia.gravar_factsheet(repositorio, raiz_dados, state.run_id, assinado)
    return {"factsheet": assinado}
