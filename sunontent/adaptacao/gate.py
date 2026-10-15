"""E4.3 Gate do plano (docs/arquiteturas.md, E4.3). Determinístico.

Confere o plano completo pelas 9 regras e devolve a lista de problemas, cada um com a regra, o nível
(None quando vale para o plano inteiro) e o detalhe, que é a mensagem devolvida ao planejador.
`fora_do_foco`, `secundario`, a escolha do núcleo, o papel e o sentido da tese não têm conferência por
código: quem confere é o revisor humano na matriz do dashboard.
"""

import re
from collections import Counter

from sunontent import numeros
from sunontent.adaptacao.complemento import termos_dos_claims
from sunontent.adaptacao.prefiltro import Prefiltro
from sunontent.glossario import Glossario, Termo, dobrar
from sunontent.levelspec import LevelSpec
from sunontent.schemas import NIVEIS, Claim, PlanoEditorial, ProblemaPlano

# Palavras que sobram quando a explicação só repete o termo ("O IPCA é o IPCA", "É como o IPCA").
_LIGACAO = {
    "e", "como", "o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das", "que", "se",
    "em", "no", "na", "nos", "nas", "por", "para", "ao", "aos",
}


def _problema(regra: str, nivel: str | None, detalhe: str) -> ProblemaPlano:
    return ProblemaPlano(regra=regra, nivel=nivel, detalhe=detalhe)


def conferir(
    plano: PlanoEditorial,
    claims: dict[str, Claim],
    prefiltro: Prefiltro,
    specs: dict[str, LevelSpec],
    glossario: Glossario,
    *,
    aceitar_tese_vazia: bool = False,
) -> list[ProblemaPlano]:
    """`aceitar_tese_vazia`: a tese vazia e sem ids, que só a limpeza do plano produz, não é problema."""
    problemas: list[ProblemaPlano] = []
    problemas += _id_inexistente(plano, claims)
    problemas += _contraditorio(plano)
    problemas += _nucleo_ausente(plano, claims, prefiltro)
    problemas += _acima_do_limite(plano, specs)
    problemas += _prioridade_invalida(plano)
    problemas += _jargao_sem_analogia(plano, claims, glossario)
    problemas += _analogia_invalida(plano, glossario)
    problemas += _motivo_invalido(plano, claims, glossario)
    problemas += _tese_invalida(plano, claims, glossario, aceitar_tese_vazia)
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
        descartes: dict[str, list] = {}
        for d in p.descartes:
            descartes.setdefault(d.claim_id, []).append(d)
        for claim_id, lista in descartes.items():
            fora_por_regra = any(d.origem == "prefiltro" for d in lista)
            motivos = ", ".join(d.motivo for d in lista if d.origem != "prefiltro")
            if claim_id in usados:
                detalhe = (
                    f"{claim_id} não é candidato neste nível; tire dos claims" if fora_por_regra
                    else f"{claim_id} está nos claims e nos descartes ({motivos}); deixe em um só"
                )
                problemas.append(_problema("contraditorio", p.nivel, detalhe))
            if len(lista) > 1:
                detalhe = (
                    f"{claim_id} não é candidato neste nível; tire dos descartes" if fora_por_regra
                    else f"{claim_id} está em mais de um descarte ({motivos}); deixe um só"
                )
                problemas.append(_problema("contraditorio", p.nivel, detalhe))
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


def _prioridade_invalida(plano: PlanoEditorial) -> list[ProblemaPlano]:
    problemas = []
    for p in plano.niveis:
        prioridades = [c.prioridade for c in p.claims]
        if sorted(prioridades) != list(range(1, len(prioridades) + 1)):
            problemas.append(
                _problema(
                    "prioridade_invalida", p.nivel,
                    f"as prioridades dos claims são {prioridades}; numere os {len(prioridades)} claims do nível "
                    f"de 1 a {len(prioridades)}, sem repetir (1 é o mais importante)",
                )
            )
    return problemas


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


def so_repete_o_termo(explicacao: str, termo: Termo) -> bool:
    """Sem as formas do termo, a pontuação e as palavras de ligação, a explicação fica sem nenhuma palavra."""
    texto = dobrar(explicacao)
    for forma in sorted(termo.formas, key=len, reverse=True):
        texto = re.sub(r"(?<!\w)" + re.escape(dobrar(forma)) + r"(?!\w)", " ", texto)
    return not [p for p in re.findall(r"\w+", texto) if p not in _LIGACAO]


def motivo_da_analogia_invalida(explicacao: str, termo: Termo | None) -> str | None:
    """Por que a explicação de uma analogia `nova` não pode ir para a peça, ou None se pode."""
    if re.search(r"\d", explicacao):
        return "tem número"
    if "{{" in explicacao:
        return "tem marcador {{...}}"
    if termo is not None and so_repete_o_termo(explicacao, termo):
        return "só repete o termo"
    return None


def _analogia_invalida(plano: PlanoEditorial, glossario: Glossario) -> list[ProblemaPlano]:
    problemas = []
    for a in plano.nivel("iniciante").analogias:
        if a.origem != "nova":
            continue
        termo_id = glossario.termo_da_forma(a.termo)
        motivo = motivo_da_analogia_invalida(a.explicacao, glossario.termos[termo_id] if termo_id else None)
        if motivo:
            problemas.append(
                _problema(
                    "analogia_invalida", "iniciante",
                    f"a analogia de {a.termo!r} {motivo}; escreva uma comparação cotidiana que comece com "
                    "'É como...', sem números",
                )
            )
    return problemas


def _contem(maior: Counter, menor: list) -> list:
    """Os itens de `menor` que `maior` não cobre, contando repetição."""
    restante, faltam = Counter(maior), []
    for chave, rotulo in menor:
        if restante[chave] > 0:
            restante[chave] -= 1
        else:
            faltam.append(rotulo)
    return faltam


def _datas(texto: str) -> tuple[list[numeros.Data], list[tuple]]:
    """As datas com dia (dia, mês e ano, como em numeros.datas) e, no resto do texto, os componentes de data sem
    dia (ano, trimestre, reunião), por valor."""
    com_dia = numeros.datas(texto)
    resto = texto
    for d in com_dia:
        resto = resto.replace(d.bruto, " " * len(d.bruto))
    return com_dia, [(e.valor, e.bruto) for e in numeros.varrer(resto) if e.unidade == "data"]


def _faltam_datas(descartado: str, relacionado: str) -> list[str]:
    """Datas do texto descartado que o relacionado não tem. O ano ausente de um lado vale para qualquer ano."""
    com_dia, sem_dia = _datas(descartado)
    livres, componentes = _datas(relacionado)
    faltam = []
    for d in com_dia:
        par = next((r for r in livres
                    if (r.dia, r.mes) == (d.dia, d.mes) and (r.ano == d.ano or r.ano is None or d.ano is None)), None)
        if par is None:
            faltam.append(d.bruto)
        else:
            livres.remove(par)
    return faltam + _contem(Counter(valor for valor, _ in componentes), sem_dia)


def faltam_no_relacionado(descartado: Claim, relacionado: Claim) -> list[str]:
    """Números (por valor e unidade) e datas do claim descartado que o relacionado não tem."""
    faltam = _contem(
        Counter((n.valor, n.unidade) for n in relacionado.numeros),
        [((n.valor, n.unidade), n.bruto) for n in descartado.numeros],
    )
    try:
        faltam += _faltam_datas(descartado.texto, relacionado.texto)
    except numeros.NumeroNaoPrevisto:
        faltam.append("as datas do texto, que não puderam ser lidas")
    return faltam


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
                elif d.claim_id in claims and d.relacionado in claims:
                    faltam = faltam_no_relacionado(claims[d.claim_id], claims[d.relacionado])
                    if faltam:
                        problemas.append(
                            _problema(
                                "motivo_invalido", p.nivel,
                                f"{d.claim_id} redundante com {d.relacionado}, mas {d.relacionado} não tem "
                                f"{', '.join(repr(f) for f in faltam)}; use o claim ou outro motivo",
                            )
                        )
            elif d.motivo == "exige_base_tecnica" and d.claim_id in claims:
                if not termos_dos_claims(glossario, claims, [d.claim_id], p.nivel):
                    problemas.append(
                        _problema(
                            "motivo_invalido", p.nivel,
                            f"{d.claim_id} descartado por exige_base_tecnica, mas não tem termo do glossário acima "
                            "do nível (no avançado, nunca tem); use outro motivo ou use o claim",
                        )
                    )
    return problemas


def numeros_da_tese(tese: str, glossario: Glossario) -> list[str]:
    """Os números da tese que não são data, como estão escritos. Os termos do glossário ficam fora: o 15 de
    IPCA-15 não é número da tese."""
    sem_termos = list(tese)
    for o in glossario.casar(tese):
        sem_termos[o.inicio:o.fim] = " " * (o.fim - o.inicio)
    try:
        return [e.bruto for e in numeros.varrer("".join(sem_termos)) if e.unidade != "data"]
    except numeros.NumeroNaoPrevisto as erro:
        return [str(erro)]


def jargao_da_tese(tese: str, glossario: Glossario) -> list[str]:
    """Os termos do glossário acima de iniciante na tese."""
    return [glossario.termos[t].termo for t in glossario.acima_de(glossario.termos_em(tese), "iniciante")]


def _tese_invalida(
    plano: PlanoEditorial, claims: dict[str, Claim], glossario: Glossario, aceitar_tese_vazia: bool
) -> list[ProblemaPlano]:
    problemas = []
    for p in plano.niveis:
        if aceitar_tese_vazia and not p.tese.strip() and not p.tese_claim_ids:
            continue
        usados = {c.claim_id for c in p.claims}
        if not p.tese.strip():
            problemas.append(
                _problema("tese_invalida", p.nivel, "a tese está vazia; escreva uma frase que parafraseie os claims "
                          "de tese_claim_ids")
            )
        if not p.tese_claim_ids:
            problemas.append(
                _problema("tese_invalida", p.nivel, "a tese não cita nenhum claim; liste em tese_claim_ids os claims "
                          "usados que ela parafraseia")
            )
        for claim_id in p.tese_claim_ids:
            if claim_id in claims and claim_id not in usados:
                problemas.append(
                    _problema(
                        "tese_invalida", p.nivel,
                        f"{claim_id} está em tese_claim_ids, mas não está nos claims do nível; tire da tese ou use "
                        "o claim",
                    )
                )
        achados = numeros_da_tese(p.tese, glossario)
        if achados:
            problemas.append(
                _problema(
                    "tese_invalida", p.nivel,
                    f"a tese tem número ({', '.join(repr(a) for a in achados)}); tire os números da tese (datas "
                    "podem ficar)",
                )
            )
        if p.nivel == "iniciante":
            termos = jargao_da_tese(p.tese, glossario)
            if termos:
                problemas.append(
                    _problema(
                        "tese_invalida", p.nivel,
                        f"a tese do iniciante usa termo técnico ({', '.join(repr(t) for t in termos)}); reescreva "
                        "sem ele",
                    )
                )
    return problemas
