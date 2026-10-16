"""Contagem de palavras e de caracteres do texto final da peça (sunontent/texto.py)."""

import pytest

from sunontent.texto import contar_caracteres, contar_palavras


@pytest.mark.parametrize("texto, palavras", [
    ("", 0),
    ("   \n\t ", 0),
    ("O Copom reduziu a Selic", 5),
    ("R$ 1,2 bi", 3),  # a contagem é por espaço: cada pedaço do número escrito vale uma palavra
    ("IPCA-15", 1),
    ("IPCA-15 subiu 0,5%", 3),
    ("5,2% a.a.", 2),
    ("Selic — 13,75% ao ano", 4),  # o travessão solto não é palavra
    ("... ! ?", 0),
    ("{{C27.1}}", 1),  # marcador que ficou no texto vale uma palavra
    ("a taxa foi {{C27.1}} em 2T26", 6),
    ("juros-base e meio-termo", 3),
    ("vírgula,colada", 1),
    ("duas\nlinhas\tdistintas", 3),
])
def test_contar_palavras(texto, palavras):
    assert contar_palavras(texto) == palavras


def test_marcador_trocado_conta_como_o_trecho_do_claim():
    com_marcador = "A taxa foi para {{C27.1}} ao ano"
    trocado = "A taxa foi para 13,75% ao ano"
    assert contar_palavras(com_marcador) == contar_palavras(trocado) == 7


@pytest.mark.parametrize("texto, caracteres", [
    ("", 0),
    ("  oi  ", 2),
    ("R$ 1,2 bi", 9),  # os espaços do meio contam
    ("IPCA-15", 7),
    ("ação", 4),
    ("ã", 1),  # "ã" decomposto vale um caractere
    ("linha 1\nlinha 2", 15),
])
def test_contar_caracteres(texto, caracteres):
    assert contar_caracteres(texto) == caracteres
