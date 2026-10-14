"""E4.3 Gate do plano (docs/arquiteturas.md, E4.3). Determinístico.

Confere o plano completo pelas 6 regras e devolve a lista de problemas, cada um com a regra, o nível
(None quando vale para o plano inteiro) e o detalhe, que é a mensagem devolvida ao planejador.
`fora_do_foco`, `secundario` e a escolha do núcleo não têm conferência por código: quem confere é o
revisor humano na E8.
"""

from sunontent.adaptacao.complemento import termos_dos_claims
from sunontent.adaptacao.prefiltro import Prefiltro
from sunontent.glossario import Glossario
from sunontent.levelspec import LevelSpec
from sunontent.schemas import NIVEIS, Claim, PlanoEditorial, ProblemaPlano


def _problema(regra: str, nivel: str | None, detalhe: str) -> ProblemaPlano:
    return ProblemaPlano(regra=regra, nivel=nivel, detalhe=detalhe)


def conferir(
    plano: PlanoEditorial,
    claims: dict[str, Claim],
    prefiltro: Prefiltro,
    specs: dict[str, LevelSpec],
    glossario: Glossario,
) -> list[ProblemaPlano]:
    problemas: list[ProblemaPlano] = []
    problemas += _id_inexistente(plano, claims)
    problemas += _contraditorio(plano)
    problemas += _nucleo_ausente(plano, claims, prefiltro)
    problemas += _acima_do_limite(plano, specs)
    problemas += _jargao_sem_analogia(plano, claims, glossario)
    problemas += _motivo_invalido(plano, claims, glossario)
    return problemas


def _id_inexistente(plano: PlanoEditorial, claims: dict[str, Claim]) -> list[ProblemaPlano]:
    problemas = [
        _problema("id_inexistente", None, f"o núcleo cita {c}, que não existe no FactSheet")
        for c in plano.nucleo
        if c not in claims
    ]
    for p in plano.niveis:
        citados = [(c.claim_id, "nos claims") for c in p.claims]
        citados += [(d.claim_id, "nos descartes") for d in p.descartes]
        citados += [(d.relacionado, f"como relacionado do descarte de {d.claim_id}") for d in p.descartes if d.relacionado]
        citados += [(c, "na tese") for c in p.tese_claim_ids]
        problemas += [
            _problema("id_inexistente", p.nivel, f"{claim_id} {onde} não existe no FactSheet")
            for claim_id, onde in citados
            if claim_id not in claims
        ]
    return problemas


def _contraditorio(plano: PlanoEditorial) -> list[ProblemaPlano]:
    problemas = []
    for p in plano.niveis:
        usados = [c.claim_id for c in p.claims]
        for claim_id in dict.fromkeys(usados):
            if usados.count(claim_id) > 1:
                problemas.append(_problema("contraditorio", p.nivel, f"{claim_id} aparece mais de uma vez nos claims"))
        descartes: dict[str, list[str]] = {}
        for d in p.descartes:
            descartes.setdefault(d.claim_id, []).append(f"{d.motivo} ({d.origem})")
        for claim_id, motivos in descartes.items():
            if claim_id in usados:
                problemas.append(
                    _problema("contraditorio", p.nivel, f"{claim_id} está nos claims e nos descartes ({', '.join(motivos)})")
                )
            if len(motivos) > 1:
                problemas.append(
                    _problema("contraditorio", p.nivel, f"{claim_id} está em mais de um descarte ({', '.join(motivos)})")
                )
    return problemas


def _nucleo_ausente(plano: PlanoEditorial, claims: dict[str, Claim], prefiltro: Prefiltro) -> list[ProblemaPlano]:
    problemas = []
    for claim_id in plano.nucleo:
        if claim_id not in claims:
            continue
        fora = [n for n in NIVEIS if claim_id not in prefiltro.candidatos[n]]
        if fora:
            problemas.append(
                _problema(
                    "nucleo_ausente", None,
                    f"{claim_id} está no núcleo, mas não é candidato em {', '.join(fora)}; escolha o núcleo entre "
                    "os candidatos nos 3 níveis",
                )
            )
        for p in plano.niveis:
            if claim_id not in {c.claim_id for c in p.claims}:
                problemas.append(
                    _problema("nucleo_ausente", p.nivel, f"{claim_id} está no núcleo e não está nos claims do nível")
                )
    return problemas


def _acima_do_limite(plano: PlanoEditorial, specs: dict[str, LevelSpec]) -> list[ProblemaPlano]:
    return [
        _problema(
            "acima_do_limite", p.nivel,
            f"{len(p.claims)} claims usados, acima do máximo de {specs[p.nivel].max_claims}",
        )
        for p in plano.niveis
        if len(p.claims) > specs[p.nivel].max_claims
    ]


def _cobertos(plano_nivel, glossario: Glossario) -> set[str]:
    cobertos = set()
    for a in plano_nivel.analogias:
        if a.glossario_ref:
            cobertos.add(a.glossario_ref.split("#", 1)[0])
        termo_id = glossario.termo_da_forma(a.termo)
        if termo_id:
            cobertos.add(termo_id)
    return cobertos


def _jargao_sem_analogia(plano: PlanoEditorial, claims: dict[str, Claim], glossario: Glossario) -> list[ProblemaPlano]:
    iniciante = plano.nivel("iniciante")
    cobertos = _cobertos(iniciante, glossario)
    faltam = [
        t for t in termos_dos_claims(glossario, claims, [c.claim_id for c in iniciante.claims], "iniciante")
        if t not in cobertos
    ]
    return [
        _problema(
            "jargao_sem_analogia", "iniciante",
            f"o termo {glossario.termos[t].termo!r} está num claim usado e não tem analogia; escreva uma analogia "
            "nova para ele ou troque o claim",
        )
        for t in faltam
    ]


def _motivo_invalido(plano: PlanoEditorial, claims: dict[str, Claim], glossario: Glossario) -> list[ProblemaPlano]:
    problemas = []
    for p in plano.niveis:
        usados = {c.claim_id for c in p.claims}
        for d in p.descartes:
            if d.motivo == "redundante":
                if not d.relacionado:
                    problemas.append(
                        _problema("motivo_invalido", p.nivel, f"{d.claim_id} redundante sem o claim relacionado")
                    )
                elif d.relacionado not in usados:
                    problemas.append(
                        _problema(
                            "motivo_invalido", p.nivel,
                            f"{d.claim_id} redundante com {d.relacionado}, que não está nos claims do nível",
                        )
                    )
            elif d.motivo == "exige_base_tecnica" and d.claim_id in claims:
                if not termos_dos_claims(glossario, claims, [d.claim_id], p.nivel):
                    problemas.append(
                        _problema(
                            "motivo_invalido", p.nivel,
                            f"{d.claim_id} descartado por exige_base_tecnica, mas não tem termo do glossário acima "
                            "do nível; use outro motivo ou use o claim",
                        )
                    )
    return problemas
