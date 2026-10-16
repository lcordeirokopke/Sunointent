"""Contagem de palavras e de caracteres do texto da peça (docs/arquiteturas.md, E5.1 e M2).

Uma regra só, usada pelo orçamento da E5.1, pelo prompt do redator e pela M2, sobre o texto final: com os
marcadores já trocados pelo trecho do claim. Marcador que ficou no texto conta como uma palavra.
"""

import re
import unicodedata

# Palavra: sequência sem espaço que tenha pelo menos uma letra ou um dígito. "R$ 1,2 bi" tem 3 palavras e
# "IPCA-15" tem 1; o travessão solto e a pontuação sozinha não contam.
_PALAVRA = re.compile(r"\S*[^\W_]\S*")


def contar_palavras(texto: str) -> int:
    return len(_PALAVRA.findall(texto))


def contar_caracteres(texto: str) -> int:
    """Caracteres do texto sem os espaços das pontas, contando os espaços do meio; letra acentuada vale um."""
    return len(unicodedata.normalize("NFC", texto).strip())
