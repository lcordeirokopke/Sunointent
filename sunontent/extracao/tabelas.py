"""E1: unidade das células de tabela, que o texto não traz ao lado do número.

Numa tabela, a unidade fica no cabeçalho ("(Em R$ milhões)") ou no rótulo da linha ("RPC média (R$)",
"ADV (milhares de contratos)"), e a célula é só o número ("3.081,4"), que o parser lê como contagem.
Aqui a célula ganha a unidade declarada acima dela, na mesma página:

- célula: linha que é só um número sem unidade própria (com ou sem parênteses de negativo);
- declaração: linha com um parêntese de unidade reconhecida; vale para as células seguintes até a
  próxima declaração. Parêntese que não é unidade ("(Trillia)", "(+/-)") é ignorado;
- o cabeçalho de colunas de uma nova tabela ("2T26", "2026 2027", "1º tri 2028"), depois de já ter
  havido célula com a unidade declarada, encerra a declaração;
- "exceto LPA" na declaração deixa como estão as células da linha cujo rótulo é o LPA ("LPA" ou
  "Lucro por ação");
- linha de quantidade ("Número de...", "Nº de...", "Quantidade de...") não herda a unidade de cima;
  só usa a que ela mesma declara ("Número de investidores (média em milhares)");
- o rótulo da linha é a última linha com letra antes das células ("-", célula vazia, não conta).

O bruto e a âncora não mudam: só o valor e a unidade.
"""

import re
from dataclasses import replace
from decimal import Decimal

from sunontent.numeros import Encontrado

_PARENTESE = re.compile(r"\(([^()]*)\)")
_CELULA = re.compile(r"\(?-?[\d.,]+\)?")
_COLUNA = re.compile(r"(?:[1-4]T\d{2,4}(?:/[1-4]T\d{2,4})?|(?:19|20)\d{2}|\d{1,2}º\s+tri)")
_EXCETO = re.compile(r"exceto\s+([^\s,)]+)", re.IGNORECASE)
_QUANTIDADE = re.compile(r"(número|nº|n°|quantidade)\s+de\b", re.IGNORECASE)
_SINONIMOS = {"lpa": ("lpa", "lucro por ação")}


def _unidade_declarada(conteudo: str) -> tuple[str, int] | None:
    """(unidade, potência de 10 aplicada ao número) do conteúdo de um parêntese, ou None."""
    texto = conteudo.lower()
    if "r$" in texto:
        depois = texto.split("r$", 1)[1]
        if re.match(r"\s*(mil|milhares)\b", depois):
            return "BRL_mi", -3
        if re.match(r"\s*(milhões|milhão|mi)\b", depois):
            return "BRL_mi", 0
        if re.match(r"\s*(bilhões|bilhão|bi)\b", depois):
            return "BRL_mi", 3
        if not re.search(r"[a-zà-ú]", depois):
            return "BRL", 0
        return None
    if re.fullmatch(r"\s*%(\s*a\.a\.)?\s*", texto):
        return "pct", 0
    if re.fullmatch(r"\s*bps\s*", texto):
        return "pp", -2
    escala = re.search(r"\b(milhares|milhões)\b", texto)
    if escala:
        return "contagem", 3 if escala.group(1) == "milhares" else 6
    return None


def _declaracao(linha: str) -> tuple[tuple[str, int], str | None] | None:
    for parentese in _PARENTESE.finditer(linha):
        unidade = _unidade_declarada(parentese.group(1))
        if unidade:
            exceto = _EXCETO.search(parentese.group(1))
            return unidade, exceto.group(1) if exceto else None
    return None


def aplicar(texto: str, achados: list[Encontrado]) -> list[Encontrado]:
    """Os achados da página, com as células de tabela convertidas para a unidade declarada."""
    por_inicio = {a.inicio: i for i, a in enumerate(achados)}
    saida = list(achados)
    atual, exceto, rotulo, com_celula = None, None, "", False
    posicao = 0
    for linha in texto.split("\n"):
        inicio_linha, posicao = posicao, posicao + len(linha) + 1
        limpa = linha.strip()
        indice = por_inicio.get(inicio_linha + len(linha) - len(linha.lstrip()) + (1 if limpa[:1] == "(" else 0))
        celula = indice is not None and _CELULA.fullmatch(limpa) and achados[indice].unidade == "contagem"
        if celula:
            if atual and not _fica_como_esta(rotulo, exceto):
                unidade, potencia = atual
                achado = achados[indice]
                valor = Decimal(format(achado.valor.scaleb(potencia), "f"))
                saida[indice] = replace(achado, valor=valor, unidade=unidade)
                com_celula = True
            continue
        if limpa and all(_COLUNA.fullmatch(t) for t in re.split(r"\s+(?!tri)", limpa)):
            if com_celula:
                atual, exceto, com_celula = None, None, False
            continue
        declarada = _declaracao(limpa)
        if declarada:
            (atual, exceto), com_celula = declarada, False
        if any(c.isalpha() for c in limpa):
            rotulo = limpa
    return saida


def _fica_como_esta(rotulo: str, exceto: str | None) -> bool:
    if _QUANTIDADE.match(rotulo) and not _declaracao(rotulo):
        return True
    if not exceto:
        return False
    nomes = _SINONIMOS.get(exceto.lower(), (exceto.lower(),))
    return any(nome in rotulo.lower() for nome in nomes)
