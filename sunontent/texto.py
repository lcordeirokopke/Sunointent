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


# Leitura da peça (M3, docs/metricas.md): palavras, sílabas e frases do português.

_MARCADOR = re.compile(r"\{\{[^{}]*\}\}")
_VOGAIS = "aeiouáéíóúâêôãõàü"
_FORTES = "aeoáéóâêô"  # duas seguidas fazem hiato; ã e õ formam ditongo com o e o o seguinte (ão, ões, mãe)
_FIM_DE_FRASE = re.compile(r"[.!?…]+[\"”»')\]]*(?=\s|$)")
# Abreviações que terminam em ponto sem fechar a frase.
_ABREVIACOES = {"p.p", "s.a", "sr", "sra", "dr", "dra", "ltda", "nº", "ex", "cia", "inc", "ref", "pág", "fl"}


def palavras_de_leitura(texto: str) -> list[str]:
    """As palavras que entram no índice de legibilidade: o token com letra, sem dígito e sem a pontuação das pontas.
    Número e marcador de número ficam de fora, porque vêm dos claims."""
    palavras = []
    for token in _MARCADOR.sub(" ", texto).split():
        if any(c.isdigit() for c in token):
            continue
        palavra = re.sub(r"^\W+|\W+$", "", token)
        if any(c.isalpha() for c in palavra):
            palavras.append(palavra)
    return palavras


def contar_silabas(palavra: str) -> int:
    """Sílabas de uma palavra portuguesa, por regra: cada grupo de vogais é uma sílaba, com hiato entre duas vogais
    fortes seguidas e entre uma vogal e `í` ou `ú`, e o `u` mudo de que, qui, gue e gui fora da conta. A palavra
    com hífen soma as partes. Sigla sem vogal vale uma."""
    total = 0
    for parte in palavra.lower().split("-"):
        parte = re.sub(r"(?<=[qg])u(?=[eiéíêî])", "", unicodedata.normalize("NFC", parte))
        for grupo in re.findall(f"[{_VOGAIS}]+", parte):
            total += 1 + sum(1 for a, b in zip(grupo, grupo[1:]) if (a in _FORTES and b in _FORTES) or b in "íú")
    return max(total, 1)


def segmentar_frases(texto: str) -> list[tuple[int, int]]:
    """Intervalos (início, fim exclusivo) das frases do texto. A frase termina em `.`, `!`, `?` ou `…` seguido de
    espaço ou do fim, e a seguinte começa em maiúscula, dígito ou abertura; não quebra em abreviação (`p.p.`, `S.A.`),
    em inicial de nome nem antes de minúscula. O `;` e os `:` não quebram. Trecho sem letra nem dígito não é frase."""
    cortes = []
    for m in _FIM_DE_FRASE.finditer(texto):
        antes = texto[: m.start()].split()
        ultimo = antes[-1].lower() if antes else ""
        resto = texto[m.end():].lstrip()
        if resto and (ultimo in _ABREVIACOES or (len(ultimo) == 1 and ultimo.isalpha()) or resto[0].islower()):
            continue
        cortes.append(m.end())
    frases, inicio = [], 0
    for fim in [*cortes, len(texto)]:
        bruto = texto[inicio:fim]
        if any(c.isalnum() for c in bruto):
            espacos = len(bruto) - len(bruto.lstrip())
            frases.append((inicio + espacos, inicio + len(bruto.rstrip())))
        inicio = fim
    return frases
