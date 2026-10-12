"""E3: valida cada claim do FactSheet contra o texto limpo e a tabela_numeros. Determinístico.

Compara contra o mesmo DocumentoProcessado que a E2 recebeu, nunca contra o PDF, e confere que o
texto do claim e a sua lista de números dizem o mesmo. Sem nota: cada claim passa ou volta para a E2
com o motivo (docs/fluxos/extracao.md, E3).
"""

from sunontent import numeros
from sunontent.schemas import Claim, DocumentoProcessado, FactSheet


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
    return numeros_do_texto(claim)


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


def validar(documento: DocumentoProcessado, factsheet: FactSheet) -> dict[str, str]:
    """Motivo de cada claim reprovado, por id. Vazio quando todos passam."""
    reprovados: dict[str, str] = {}
    vistos: set[str] = set()
    for claim in factsheet.claims:
        if claim.id in vistos:
            reprovados[claim.id] = "id repetido no FactSheet"
            continue
        vistos.add(claim.id)
        motivo = validar_claim(documento, claim)
        if motivo is not None:
            reprovados[claim.id] = motivo
    return reprovados
