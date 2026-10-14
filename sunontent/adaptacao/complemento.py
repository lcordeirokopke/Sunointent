"""E4.3 Complemento do plano (docs/arquiteturas.md, E4.3). Determinístico.

Completa a saída do planejador e devolve o PlanoEditorial:
1. `atribuir_fonte=true` nos claims usados de tipo projecao, condicionante ou risco;
2. descartes do pré-filtro, os do planejador (origem planejador) e `secundario` (origem padrao) para todo
   candidato que o planejador não usou nem descartou;
3. a tese fica só com os ids dos claims usados no nível;
4. no Iniciante, cada termo do glossário acima de iniciante no texto dos claims usados recebe a analogia do
   glossário ou, se o termo não tem nenhuma, a analogia `nova` do planejador para esse termo. Analogia
   `nova` de termo fora da lista, de termo que já tem analogia no glossário ou de termo fora dos claims
   usados é deixada de fora.
Também monta a lista de termos sem analogia que o planejador recebe e retira do plano os ids inexistentes
depois da última rodada.
"""

from sunontent.adaptacao.prefiltro import Prefiltro
from sunontent.glossario import Glossario
from sunontent.schemas import (
    Analogia,
    Claim,
    ClaimPlanejado,
    Descarte,
    PlanoEditorial,
    PlanoNivel,
    SaidaPlanejador,
)

TIPOS_COM_ATRIBUICAO = ("projecao", "condicionante", "risco")


def termos_sem_analogia(glossario: Glossario, claims: dict[str, Claim], candidatos: list[str]) -> list[str]:
    """Ids dos termos acima de iniciante no texto dos candidatos do Iniciante sem nenhuma analogia no
    glossário, na ordem da primeira ocorrência."""
    ids: dict[str, None] = {}
    for claim_id in candidatos:
        for termo_id in glossario.acima_de(glossario.termos_em(claims[claim_id].texto), "iniciante"):
            if not glossario.termos[termo_id].analogias:
                ids.setdefault(termo_id)
    return list(ids)


def termos_dos_claims(glossario: Glossario, claims: dict[str, Claim], claim_ids, nivel: str) -> list[str]:
    """Ids dos termos acima do nível no texto dos claims existentes, sem repetir."""
    ids: dict[str, None] = {}
    for claim_id in claim_ids:
        if claim_id in claims:
            for termo_id in glossario.acima_de(glossario.termos_em(claims[claim_id].texto), nivel):
                ids.setdefault(termo_id)
    return list(ids)


def completar(
    saida: SaidaPlanejador,
    claims: dict[str, Claim],
    prefiltro: Prefiltro,
    glossario: Glossario,
    *,
    rodadas: int,
) -> PlanoEditorial:
    lista_nova = set(termos_sem_analogia(glossario, claims, prefiltro.candidatos["iniciante"]))
    niveis = []
    for proposto in saida.niveis:
        nivel = proposto.nivel
        usados = [
            ClaimPlanejado(
                claim_id=c.claim_id,
                papel=c.papel,
                atribuir_fonte=c.claim_id in claims and claims[c.claim_id].tipo in TIPOS_COM_ATRIBUICAO,
            )
            for c in proposto.claims
        ]
        ids_usados = {c.claim_id for c in usados}
        do_planejador = [
            Descarte(claim_id=d.claim_id, motivo=d.motivo, relacionado=d.relacionado, origem="planejador")
            for d in proposto.descartes
        ]
        decididos = ids_usados | {d.claim_id for d in do_planejador}
        padrao = [
            Descarte(claim_id=c, motivo="secundario", origem="padrao")
            for c in prefiltro.candidatos[nivel]
            if c not in decididos
        ]
        analogias = []
        if nivel == "iniciante":
            novas = {}
            for a in proposto.analogias:
                termo_id = glossario.termo_da_forma(a.termo)
                if termo_id in lista_nova:
                    novas.setdefault(termo_id, a.explicacao)
            for termo_id in termos_dos_claims(glossario, claims, [c.claim_id for c in usados], "iniciante"):
                analogia = glossario.analogia(termo_id)
                if analogia is None and termo_id in novas:
                    analogia = Analogia(
                        termo=glossario.termos[termo_id].termo,
                        explicacao=novas[termo_id],
                        origem="nova",
                        revisada=False,
                    )
                if analogia is not None:
                    analogias.append(analogia)
        niveis.append(
            PlanoNivel(
                nivel=nivel,
                descartes=[*prefiltro.descartes[nivel], *do_planejador, *padrao],
                claims=usados,
                tese=proposto.tese,
                tese_claim_ids=[c for c in proposto.tese_claim_ids if c in ids_usados],
                analogias=analogias,
            )
        )
    return PlanoEditorial(nucleo=list(saida.nucleo), niveis=niveis, rodadas=rodadas)


def retirar_ids_inexistentes(plano: PlanoEditorial, existentes) -> PlanoEditorial:
    """Tira do plano o que a E5 não consegue usar: ids fora do FactSheet no núcleo, nos claims, nos
    descartes e na tese; `relacionado` inexistente vira None. O núcleo pode ficar vazio, e quem chama
    confere isso antes de validar o plano."""
    existentes = set(existentes)
    niveis = [
        p.model_copy(
            update={
                "claims": [c for c in p.claims if c.claim_id in existentes],
                "descartes": [
                    d.model_copy(update={"relacionado": d.relacionado if d.relacionado in existentes else None})
                    for d in p.descartes
                    if d.claim_id in existentes
                ],
                "tese_claim_ids": [c for c in p.tese_claim_ids if c in existentes],
            }
        )
        for p in plano.niveis
    ]
    return plano.model_construct(
        nucleo=[c for c in plano.nucleo if c in existentes], niveis=niveis, rodadas=plano.rodadas, aviso=plano.aviso
    )
