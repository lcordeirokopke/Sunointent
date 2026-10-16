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


# Leitura da peça: palavras, sílabas e frases (M3)

from sunontent.texto import contar_silabas, palavras_de_leitura, segmentar_frases  # noqa: E402


# Convenção: o ditongo crescente (ia, ie, io, ua, uo: série, história, ordinárias) conta uma sílaba.
@pytest.mark.parametrize("palavra, silabas", [
    ("juros", 2), ("taxa", 2), ("Selic", 2), ("inflação", 3), ("ações", 2), ("mãe", 1), ("pão", 1),
    ("economia", 4), ("balanço", 3), ("que", 1), ("quilo", 2), ("guerra", 2), ("aquisição", 4),
    ("incorporação", 5), ("acionistas", 4), ("participação", 5), ("ordinárias", 4), ("saúde", 3),
    ("Itaú", 3), ("voo", 2), ("EMAE", 3), ("CVM", 1), ("pós-fixado", 4), ("resultado", 4),
    ("dividendos", 4), ("rentabilidade", 6), ("mercado", 3), ("série", 2), ("história", 3),
])
def test_contar_silabas(palavra, silabas):
    assert contar_silabas(palavra) == silabas


def test_palavras_de_leitura_ignoram_numero_marcador_e_pontuacao():
    texto = "A Selic ficou em 13,75% ao ano, ou {{C01.1}} (pós-fixado)."
    assert palavras_de_leitura(texto) == ["A", "Selic", "ficou", "em", "ao", "ano", "ou", "pós-fixado"]


def trechos(texto):
    return [texto[a:b] for a, b in segmentar_frases(texto)]


def test_frases_quebram_em_ponto_interrogacao_e_exclamacao():
    assert trechos("A Selic caiu. Subiu depois? Sim! Fim.") == ["A Selic caiu.", "Subiu depois?", "Sim!", "Fim."]


def test_frase_nao_quebra_em_decimal_abreviacao_nem_minuscula():
    assert trechos("A Selic ficou em 13,75% ao ano. O lucro subiu 2 p.p. no trimestre.") == [
        "A Selic ficou em 13,75% ao ano.", "O lucro subiu 2 p.p. no trimestre."]
    assert trechos("A Sabesp S.A. anunciou a compra. Depois, vendeu.") == ["A Sabesp S.A. anunciou a compra.", "Depois, vendeu."]
    assert trechos("Aprovado, ex. o plano. Segue.") == ["Aprovado, ex. o plano.", "Segue."]


def test_dois_pontos_e_ponto_e_virgula_nao_quebram():
    assert trechos("Três pontos: a Selic; o IPCA; o PIB. Fim.") == ["Três pontos: a Selic; o IPCA; o PIB.", "Fim."]


def test_texto_sem_ponto_final_e_uma_frase_e_texto_sem_letra_nao_e_frase():
    assert trechos("Selic em queda") == ["Selic em queda"]
    assert segmentar_frases("... ! ?") == []
    assert segmentar_frases("") == []


def test_intervalos_da_frase_apontam_para_o_texto():
    texto = "  Primeira frase.   Segunda frase."
    assert [texto[a:b] for a, b in segmentar_frases(texto)] == ["Primeira frase.", "Segunda frase."]
