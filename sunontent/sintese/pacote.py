"""E5.1 Pacote da célula (docs/arquiteturas.md, E5.1): tudo o que o redator recebe, calculado por código.

1. Corte por formato: o artigo usa os claims do plano do nível; o carrossel e o roteiro, até o `max_claims` do
   formato, com o núcleo e, nas vagas que sobram, os claims de maior prioridade, na ordem de leitura do plano.
2. Marcadores: cada número do texto do claim que não seja data (`numeros.varrer`) vira `{{<claim_id>.<ordem>}}`,
   casado com a primeira entrada livre da lista de números do claim com o mesmo valor e unidade. Achado sem
   entrada ou entrada sem achado é FalhaTerminal de dado. Os marcadores são calculados para todos os claims do
   plano do nível, que são os válidos na montagem; o redator vê só os do formato.
3. Orçamento: o contrato do formato em frases (`levelspec.instrucoes_redator`) e a divisão por claim pelo
   `peso_papel`, sempre arredondada para baixo, para a soma não passar do limite da M2. No artigo, a faixa de
   palavras; no roteiro, os segundos entre a abertura e o fechamento (o resto vai à última cena de claim) e as
   palavras de cada cena (segundos vezes `palavras_por_segundo`). O carrossel não tem divisão por claim.
4. Plano: tese, papel, `atribuir_fonte` e as analogias presas aos claims do formato em que o termo aparece,
   e a `data_documento`.
"""

import math
from dataclasses import dataclass
from datetime import date

from sunontent import numeros
from sunontent.glossario import Glossario
from sunontent.levelspec import InstrucoesRedator, LevelSpec, instrucoes_redator
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import Claim, ClaimPlanejado, FactSheet, PlanoEditorial, TipoClaim, celula_id


@dataclass(frozen=True)
class Marcador:
    marcador: str  # "{{C27.1}}"
    claim_id: str
    ordem: int  # posição do número na lista do claim, 1-indexada
    trecho: str  # o trecho do texto do claim que o marcador cobre ("5,2%")


@dataclass(frozen=True)
class ClaimDoPacote:
    id: str
    tipo: TipoClaim
    papel: str
    prioridade: int
    atribuir_fonte: bool
    nucleo: bool
    texto: str  # o texto do claim com os marcadores no lugar dos números
    marcadores: tuple[Marcador, ...]
    orcamento: str  # a parte do orçamento deste claim; vazio no carrossel


@dataclass(frozen=True)
class AnalogiaDoPacote:
    termo: str
    explicacao: str
    claim_ids: tuple[str, ...]  # os claims do formato em que o termo aparece


@dataclass(frozen=True)
class Pacote:
    celula_id: str
    nivel: str
    formato: str
    tipo_documento: str
    emissor: str
    data_documento: date
    tese: str  # vazia quando a limpeza da E4 a tirou
    claims: tuple[ClaimDoPacote, ...]  # os claims do formato, na ordem de leitura do plano
    analogias: tuple[AnalogiaDoPacote, ...]
    instrucoes: InstrucoesRedator
    orcamento_abertura: str  # só no roteiro
    orcamento_fechamento: str
    plano_claim_ids: tuple[str, ...]  # os claims do plano do nível
    marcadores: dict[str, Marcador]  # os válidos na montagem: todos os do plano do nível, pelo texto do marcador

    @property
    def claim_ids(self) -> list[str]:
        return [c.id for c in self.claims]


def cortar(planejados: list[ClaimPlanejado], nucleo: list[str], max_claims: int) -> list[ClaimPlanejado]:
    """O núcleo e os de maior prioridade até `max_claims`, na ordem de leitura do plano."""
    escolhidos = {c.claim_id for c in planejados if c.claim_id in nucleo}
    for c in sorted(planejados, key=lambda c: c.prioridade):
        if len(escolhidos) >= max_claims:
            break
        escolhidos.add(c.claim_id)
    return [c for c in planejados if c.claim_id in escolhidos]


def marcar(claim: Claim) -> tuple[str, list[Marcador]]:
    """O texto do claim com cada número que não seja data trocado pelo seu marcador, e os marcadores."""
    try:
        achados = [a for a in numeros.varrer(claim.texto) if a.unidade != "data"]
    except numeros.NumeroNaoPrevisto as erro:
        raise FalhaTerminal(f"E5.1: claim {claim.id} com número em formato não previsto ({erro})") from erro
    livres = list(enumerate(claim.numeros, start=1))
    marcadores: list[Marcador] = []
    partes: list[str] = []
    cursor = 0
    for achado in achados:
        par = next(((o, n) for o, n in livres if (n.valor, n.unidade) == (achado.valor, achado.unidade)), None)
        if par is None:
            raise FalhaTerminal(
                f"E5.1: o número {achado.bruto!r} do texto do claim {claim.id} não está na lista de números do claim"
            )
        livres.remove(par)
        marcador = Marcador(f"{{{{{claim.id}.{par[0]}}}}}", claim.id, par[0], claim.texto[achado.inicio:achado.fim])
        marcadores.append(marcador)
        partes += [claim.texto[cursor:achado.inicio], marcador.marcador]
        cursor = achado.fim
    if livres:
        raise FalhaTerminal(
            f"E5.1: números do claim {claim.id} que o texto não menciona: {', '.join(n.bruto for _, n in livres)}"
        )
    partes.append(claim.texto[cursor:])
    return "".join(partes), sorted(marcadores, key=lambda m: m.ordem)


def _dividir(total: int, pesos: list[int]) -> list[int]:
    soma = sum(pesos)
    return [math.floor(total * p / soma) for p in pesos]


def _orcamentos(spec: LevelSpec, formato: str, papeis: list[str]) -> tuple[list[str], str, str]:
    """A parte do orçamento de cada claim, da abertura e do fechamento."""
    pesos = [getattr(spec.peso_papel, p) for p in papeis]
    if formato == "artigo":
        faixa = spec.formatos.artigo.palavras
        minimos, maximos = _dividir(faixa.min, pesos), _dividir(faixa.max, pesos)
        return [f"de {a} a {b} palavras" for a, b in zip(minimos, maximos)], "", ""
    if formato == "carrossel":
        return ["" for _ in papeis], "", ""
    r = spec.formatos.roteiro
    segundos = _dividir(r.duracao_s - r.abertura_s - r.fechamento_s, pesos)
    segundos[-1] += r.duracao_s - r.abertura_s - r.fechamento_s - sum(segundos)

    def cena(s: int) -> str:
        return f"{s} s, até {math.floor(s * r.palavras_por_segundo)} palavras"

    return [cena(s) for s in segundos], cena(r.abertura_s), cena(r.fechamento_s)


def montar(
    plano: PlanoEditorial, nivel: str, formato: str, factsheet: FactSheet, spec: LevelSpec, glossario: Glossario
) -> Pacote:
    plano_nivel = plano.nivel(nivel)
    claims = {c.id: c for c in factsheet.claims}
    ausentes = [c.claim_id for c in plano_nivel.claims if c.claim_id not in claims]
    if ausentes:
        raise FalhaTerminal(f"E5.1: claims do plano de {nivel} fora do FactSheet: {', '.join(ausentes)}")

    marcados = {c.claim_id: marcar(claims[c.claim_id]) for c in plano_nivel.claims}
    validos = {m.marcador: m for _, lista in marcados.values() for m in lista}

    if formato == "artigo":
        planejados = list(plano_nivel.claims)
    else:
        planejados = cortar(plano_nivel.claims, plano.nucleo, getattr(spec.formatos, formato).max_claims)
    partes, abertura, fechamento = _orcamentos(spec, formato, [c.papel for c in planejados])

    do_formato = tuple(
        ClaimDoPacote(
            id=c.claim_id, tipo=claims[c.claim_id].tipo, papel=c.papel, prioridade=c.prioridade,
            atribuir_fonte=c.atribuir_fonte, nucleo=c.claim_id in plano.nucleo,
            texto=marcados[c.claim_id][0], marcadores=tuple(marcados[c.claim_id][1]), orcamento=parte,
        )
        for c, parte in zip(planejados, partes)
    )

    analogias = []
    for analogia in plano_nivel.analogias:
        termo_id = glossario.termo_da_forma(analogia.termo)
        presos = tuple(
            c.id for c in do_formato
            if termo_id is not None and termo_id in glossario.termos_em(claims[c.id].texto)
        )
        if presos:  # a analogia acompanha os claims do formato
            analogias.append(AnalogiaDoPacote(analogia.termo, analogia.explicacao, presos))

    return Pacote(
        celula_id=celula_id(nivel, formato), nivel=nivel, formato=formato,
        tipo_documento=factsheet.tipo_documento, emissor=factsheet.emissor, data_documento=factsheet.data_documento,
        tese=plano_nivel.tese, claims=do_formato, analogias=tuple(analogias),
        instrucoes=instrucoes_redator(spec, formato),
        orcamento_abertura=abertura, orcamento_fechamento=fechamento,
        plano_claim_ids=tuple(c.claim_id for c in plano_nivel.claims), marcadores=validos,
    )
