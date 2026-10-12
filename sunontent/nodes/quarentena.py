"""Quarentena do loop E2/E3: chama extracao/quarentena.py depois da última rodada com claim reprovado.

Grava os reprovados em claims_quarentena e assina o FactSheet só com os claims válidos, que segue
no grafo. Sem nenhum claim válido, grava a quarentena e aborta o run (docs/fluxos/extracao.md, Quarentena).
Dependências vêm de config["configurable"]: repositorio e raiz_dados.
"""

from pathlib import Path

from sunontent import persistencia
from sunontent.extracao import quarentena as dominio
from sunontent.retentativa import FalhaTerminal


def quarentena(state, config) -> dict:
    configuravel = config["configurable"]
    repositorio, raiz_dados = configuravel["repositorio"], Path(configuravel["raiz_dados"])
    assinado, itens = dominio.separar(state.factsheet, state.propostas, state.correcoes, state.rodada)
    persistencia.gravar_quarentena(repositorio, raiz_dados, state.run_id, itens)
    if not assinado.claims:
        motivos = "; ".join(f"{item.proposto.id}: {item.motivo}" for item in itens)
        raise FalhaTerminal(
            f"E3: nenhum claim válido depois de {state.rodada} rodadas de extração; "
            f"{len(itens)} claims ficaram em quarentena. {motivos}"
        )
    persistencia.gravar_factsheet(repositorio, raiz_dados, state.run_id, assinado)
    return {"factsheet": assinado}
