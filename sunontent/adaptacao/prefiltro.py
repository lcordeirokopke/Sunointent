"""E4.1 Pré-filtro por nível (docs/arquiteturas.md, E4.1). Determinístico.

Por nível, o claim cujas tags estão todas em `tags_descartaveis` sai com o motivo `metodologico` e
origem `prefiltro`; os demais são candidatos. Os claims candidatos nos 3 níveis são os únicos que podem
ser núcleo.
"""

from dataclasses import dataclass

from sunontent.levelspec import LevelSpec
from sunontent.schemas import NIVEIS, Claim, Descarte


@dataclass(frozen=True)
class Prefiltro:
    candidatos: dict[str, list[str]]  # por nível, na ordem do FactSheet
    descartes: dict[str, list[Descarte]]  # por nível, origem prefiltro

    @property
    def candidatos_nos_3(self) -> list[str]:
        comuns = set.intersection(*(set(self.candidatos[n]) for n in NIVEIS))
        return [c for c in self.candidatos[NIVEIS[0]] if c in comuns]


def filtrar(claims: list[Claim], specs: dict[str, LevelSpec]) -> Prefiltro:
    candidatos: dict[str, list[str]] = {}
    descartes: dict[str, list[Descarte]] = {}
    for nivel in NIVEIS:
        descartaveis = set(specs[nivel].tags_descartaveis)
        candidatos[nivel], descartes[nivel] = [], []
        for claim in claims:
            if set(claim.tags) <= descartaveis:
                descartes[nivel].append(Descarte(claim_id=claim.id, motivo="metodologico", origem="prefiltro"))
            else:
                candidatos[nivel].append(claim.id)
    return Prefiltro(candidatos, descartes)
