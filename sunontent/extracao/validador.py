"""E3: valida cada claim do FactSheet contra o texto limpo e a tabela_numeros. Determinístico.

Compara contra o mesmo DocumentoProcessado que a E2 recebeu, nunca contra o PDF, e confere que o
texto do claim e a sua lista de números dizem o mesmo. Sem nota: cada claim passa ou volta para a E2
com o motivo (docs/fluxos/extracao.md, E3).
"""

import re

from sunontent import numeros
from sunontent.schemas import Claim, DocumentoProcessado, FactSheet

# Palavra: duas letras ou mais seguidas. Escala e unidade ao lado do número não contam como texto.
_PALAVRA = re.compile(r"[^\W\d_]{2,}")
_SO_UNIDADE = frozenset(
    "bps mil milhão milhões milhao milhoes bilhão bilhões bilhao bilhoes trilhão trilhões trilhao trilhoes".split()
)


def _tem_palavra(literal: str) -> bool:
    return any(p.lower() not in _SO_UNIDADE for p in _PALAVRA.findall(literal))


def validar_claim(documento: DocumentoProcessado, claim: Claim) -> str | None:
    """None se o claim passa; senão, o motivo."""
    a = claim.ancora
    if a.pagina > len(documento.paginas):
        return f"âncora na página {a.pagina}, que não existe (o documento tem {len(documento.paginas)})"
    texto = documento.paginas[a.pagina - 1].texto_limpo
    if a.offset_fim > len(texto):
        return f"âncora termina no offset {a.offset_fim}, além do fim da página {a.pagina}"
    if texto[a.offset_inicio : a.offset_fim] != claim.literal_fonte:
        return "literal_fonte não é o texto limpo no intervalo da âncora"
    if not claim.literal_fonte.strip():
        return "literal_fonte vazio"
    if not _tem_palavra(claim.literal_fonte):
        return (
            "literal_fonte só tem números, sem o texto que diz a que eles se referem; numa tabela, comece o "
            "trecho no rótulo da linha (por exemplo, 'IPCA livres' seguido dos valores) e liste só os números "
            "do próprio fato"
        )
    tabela = documento.tabela_numeros
    for numero in claim.numeros:
        if numero not in tabela:
            mesmo_bruto = [n for n in tabela if n.bruto == numero.bruto and n.ancora == numero.ancora]
            if mesmo_bruto:
                n = mesmo_bruto[0]
                return (
                    f"número {numero.bruto!r} registrado como {numero.valor} {numero.unidade}, "
                    f"mas a tabela_numeros tem {n.valor} {n.unidade}"
                )
            return f"número {numero.bruto!r} não está na tabela_numeros nessa posição"
        na = numero.ancora
        if na.pagina != a.pagina or na.offset_inicio < a.offset_inicio or na.offset_fim > a.offset_fim:
            return f"número {numero.bruto!r} está fora do literal_fonte"
    return numeros_do_texto(claim) or datas_do_texto(claim)


def datas_do_texto(claim: Claim) -> str | None:
    """Cada data com dia do texto do claim está no literal_fonte: mesmo dia e mês e, se o texto traz o
    ano, o mesmo ano no literal ("19 de outubro de 2026" não passa contra "19/10"). None se batem."""
    no_literal = numeros.datas(claim.literal_fonte)
    fora = [
        d.bruto for d in numeros.datas(claim.texto)
        if not any(l.dia == d.dia and l.mes == d.mes and (d.ano is None or l.ano == d.ano) for l in no_literal)
    ]
    if fora:
        return (
            f"datas no texto do claim que não estão no literal_fonte: {', '.join(dict.fromkeys(fora))}; "
            "escreva a data com o dia, o mês e o ano que o trecho traz, sem acrescentar o que ele não diz"
        )
    return None


def numeros_do_texto(claim: Claim) -> str | None:
    """O texto e a lista de números dizem o mesmo: cada número listado aparece no texto (mesmo valor e
    unidade) e cada número do texto, exceto datas, está na lista. None se batem; senão, o motivo."""
    try:
        no_texto = [n for n in numeros.varrer(claim.texto) if n.unidade != "data"]
    except numeros.NumeroNaoPrevisto as erro:
        return f"o texto tem número em formato não previsto ({erro}); escreva-o como no documento"
    restantes = [(n.valor, n.unidade) for n in no_texto]
    ausentes = []
    for numero in claim.numeros:
        if numero.unidade == "data":
            continue
        chave = (numero.valor, numero.unidade)
        if chave in restantes:
            restantes.remove(chave)
        else:
            ausentes.append(numero.bruto)
    if ausentes:
        return (
            f"números listados que o texto do claim não menciona: {', '.join(ausentes)}; liste só os números "
            "do próprio fato e escreva-os no texto, ou crie um claim para cada fato"
        )
    if restantes:
        sobras = [n.bruto for n in no_texto if (n.valor, n.unidade) in restantes]
        return (
            f"números no texto do claim que não estão na lista: {', '.join(sobras)}; todo número do texto "
            "precisa vir do literal_fonte e estar listado em numeros"
        )
    return None


def datas_na_lista(claim: Claim) -> str | None:
    """Datas não entram em numeros: a E5 troca cada número listado por um marcador, e data fica no texto.
    None se a lista não tem data; senão, o motivo."""
    datas = [n.bruto for n in claim.numeros if n.unidade == "data"]
    if datas:
        return (
            f"datas listadas em numeros: {', '.join(datas)}; anos, datas, trimestres e numeração ficam só no texto"
        )
    return None


def validar(documento: DocumentoProcessado, factsheet: FactSheet) -> dict[str, str]:
    """Motivo de cada claim reprovado, por id. Vazio quando todos passam."""
    reprovados: dict[str, str] = {}
    vistos: set[str] = set()
    for claim in factsheet.claims:
        if claim.id in vistos:
            reprovados[claim.id] = "id repetido no FactSheet"
            continue
        vistos.add(claim.id)
        motivo = validar_claim(documento, claim) or datas_na_lista(claim)
        if motivo is not None:
            reprovados[claim.id] = motivo
    return reprovados
