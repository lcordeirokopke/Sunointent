"""Métricas determinísticas (sunontent/metrics/): M1, M2 e M5, sem LLM e sem banco."""

import copy
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sunontent import levelspec
from sunontent.glossario import validar
from sunontent.metrics import analogy_rule, numeric_fidelity, readability, resultado, term_density, text_size
from sunontent.schemas import (
    Analogia,
    Ancora,
    Claim,
    LlmJudgeDetalhes,
    Numero,
    NumeroUsado,
    Peca,
    PlanContractDetalhes,
    PlanoNivel,
    VeredictoUnidade,
)
from test_glossario import TERMOS, analogia, termo

RAIZ = Path(__file__).resolve().parent.parent
ANCORA = Ancora(pagina=1, offset_inicio=0, offset_fim=10)
DOCUMENTO = date(2026, 10, 21)
GLOSSARIO = validar({"versao": 1, "termos": copy.deepcopy(TERMOS)})
SPECS = levelspec.carregar_todos(RAIZ)


def numero(valor, unidade, bruto):
    return Numero(valor=Decimal(valor), unidade=unidade, bruto=bruto, ancora=ANCORA)


def claim(id, texto, *numeros):
    return Claim(id=id, texto=texto, numeros=list(numeros), tipo="decisao", tags=["indicador_chave"],
                 ancora=ANCORA, literal_fonte="trecho")


C01 = claim("C01", "O Copom reduziu a Selic para 13,75% a.a. na reunião de 19/10.", numero("13.75", "pct", "13,75%"))
C02 = claim("C02", "A inflação em 12 meses ficou em 5,2% e o PIB cresceu 1,4%.",
            numero("5.2", "pct", "5,2%"), numero("1.4", "pct", "1,4%"))
C03 = claim("C03", "O Copom projeta IPCA de 4,5% em 2027 e no 2T27.", numero("4.5", "pct", "4,5%"))
C04 = claim("C04", "O Banco do Brasil também registrou 13,75% de retorno.", numero("13.75", "pct", "13,75%"))
PLANO = [C01, C02, C03]
TABELA = [numero("13.75", "pct", "13,75%"), numero("5.2", "pct", "5,2%"), numero("1.4", "pct", "1,4%"),
          numero("4.5", "pct", "4,5%"), numero("9.9", "pct", "9,9%")]  # o 9,9% está no documento, não no plano


def usado(texto, trecho, claim_id, ordem, campo="texto", indice=0, ocorrencia=0):
    inicio = [m.start() for m in re.finditer(re.escape(trecho), texto)][ocorrencia]
    return NumeroUsado(campo=campo, unidade_indice=None if campo == "titulo" else indice, inicio=inicio,
                       fim=inicio + len(trecho), claim_id=claim_id, ordem=ordem)


def artigo(secoes, titulo="Selic em queda", linha_fina="O Copom decidiu", usados=(), claim_ids=("C01",), nivel="iniciante"):
    return Peca.model_validate({
        "celula_id": f"{nivel}__artigo", "nivel": nivel, "formato": "artigo",
        "payload": {"titulo": titulo, "linha_fina": linha_fina,
                    "secoes": [{"texto": t, "claim_ids": list(claim_ids)} for t in secoes]},
        "claim_ids": list(claim_ids), "numeros_usados": list(usados), "tentativa": 1,
    })


def m1(peca, claims=PLANO, glossario=GLOSSARIO):
    return numeric_fidelity.avaliar(peca, claims, TABELA, DOCUMENTO, glossario)


# M1: os quatro desfechos


def test_numero_de_marcador_passa():
    texto = "A Selic foi para 13,75% ao ano."
    r = m1(artigo([texto], usados=[usado(texto, "13,75%", "C01", 1)]))
    assert r.passou and r.valor == 0 and r.faixa_esperada == "0" and r.violacoes == []
    assert (r.detalhes.numeros_varridos, r.detalhes.com_marcador) == (1, 1)


def test_numero_sem_numero_passa():
    assert m1(artigo(["A Selic caiu e o crédito deve ficar mais barato."])).passou


def test_numero_escrito_a_mao_com_o_valor_de_um_claim_do_plano():
    texto = "A Selic foi para 13,75% ao ano."
    r = m1(artigo([texto]))
    (v,) = r.violacoes
    assert (v.metrica, v.trecho, v.unidade_indice, v.campo, v.inicio, v.fim) == ("numeric_fidelity", "13,75%", 0, "texto", 17, 23)
    assert "{{C01.1}}" in v.instrucao_corretiva and "13,75%" in v.instrucao_corretiva
    assert r.detalhes.desfechos.fora_de_marcador_do_plano == 1 and r.valor == 1 and not r.passou


def test_o_marcador_sugerido_prefere_o_claim_que_a_peca_usa():
    # 13,75% está no C01 e no C04; a peça usa o C04.
    r = m1(artigo(["Rendeu 13,75% no ano."], claim_ids=("C04",)), claims=[*PLANO, C04])
    assert "{{C04.1}}" in r.violacoes[0].instrucao_corretiva


def test_o_marcador_sugerido_traz_a_ordem_do_numero_no_claim():
    r = m1(artigo(["O PIB cresceu 1,4% no ano."]))
    assert "{{C02.2}}" in r.violacoes[0].instrucao_corretiva


def test_numero_do_documento_fora_do_plano():
    r = m1(artigo(["O índice ficou em 9,9% no período."]))
    (v,) = r.violacoes
    assert "existe no documento, mas não é fato do plano" in v.instrucao_corretiva
    assert r.detalhes.desfechos.fora_do_plano_no_documento == 1


def test_numero_ausente_do_documento_e_alucinacao():
    r = m1(artigo(["O índice ficou em 7,77% no período."]))
    (v,) = r.violacoes
    assert v.instrucao_corretiva == "O número 7,77% não existe no documento: remova-o."
    assert r.detalhes.desfechos.ausente_do_documento == 1


def test_valor_igual_com_outra_unidade_nao_e_o_mesmo_numero():
    r = m1(artigo(["A taxa ficou em 5,2 pontos percentuais."]))
    assert r.detalhes.desfechos.ausente_do_documento == 1  # 5,2 pp, e o claim tem 5,2%


def test_marcador_invalido_que_ficou_no_texto():
    texto = "A Selic foi para {{C99.1}} ao ano."
    r = m1(artigo([texto]))
    (v,) = r.violacoes
    assert (v.trecho, v.unidade_indice, v.inicio, v.fim) == ("{{C99.1}}", 0, 17, 26)
    assert "{{C99.1}} não existe na tabela de marcadores" in v.instrucao_corretiva
    assert r.detalhes.desfechos.marcador_invalido == 1


def test_marcador_valido_ao_lado_de_marcador_invalido():
    texto = "A Selic foi para 13,75% e depois {{C77.2}}."
    r = m1(artigo([texto], usados=[usado(texto, "13,75%", "C01", 1)]))
    assert [v.trecho for v in r.violacoes] == ["{{C77.2}}"]
    assert r.detalhes.com_marcador == 1


def test_numero_colado_a_um_marcador_nao_confunde_a_varredura():
    texto = "A Selic foi para 13,75%, bem acima dos 7,77% de antes."
    r = m1(artigo([texto], usados=[usado(texto, "13,75%", "C01", 1)]))
    assert [v.trecho for v in r.violacoes] == ["7,77%"]


def test_forma_do_glossario_com_digito_e_mascarada():
    texto = "O IPCA-15 de outubro surpreendeu."
    assert m1(artigo([texto])).passou
    sem_ipca_15 = validar({"versao": 1, "termos": [termo("selic", "Selic")]})
    r = m1(artigo([texto]), glossario=sem_ipca_15)
    # Sem a máscara, o "15 de outubro" do IPCA-15 seria lido como uma data que nenhum claim traz.
    assert not r.passou and r.violacoes[0].trecho == "15 de outubro"
    assert r.detalhes.desfechos.data_sem_lastro == 1


def test_numero_de_formato_que_o_parser_nao_le():
    texto = "A cotação foi de US$ 5 no dia e a Selic foi para 13,75%."
    r = m1(artigo([texto], usados=[usado(texto, "13,75%", "C01", 1)]))
    (v,) = r.violacoes
    assert (v.trecho, v.obtido) == ("US$ 5", "formato não previsto")
    assert r.detalhes.desfechos.formato_nao_previsto == 1 and r.detalhes.com_marcador == 1


def test_varias_violacoes_no_mesmo_texto_saem_todas():
    r = m1(artigo(["Subiu 9,9% e depois 7,77%, longe dos 13,75%."]))
    assert [v.trecho for v in r.violacoes] == ["9,9%", "7,77%", "13,75%"]
    assert r.valor == 3


# M1: título, linha fina e gancho visual


def test_numero_com_marcador_vale_no_titulo():
    titulo = "Selic cai para 13,75%"
    r = m1(artigo(["A Selic caiu."], titulo=titulo, usados=[usado(titulo, "13,75%", "C01", 1, campo="titulo")]))
    assert r.passou and r.detalhes.com_marcador == 1


def test_numero_do_titulo_a_mao_e_violacao():
    r = m1(artigo(["A Selic caiu."], titulo="Selic cai para 13,75%"))
    (v,) = r.violacoes
    assert (v.campo, v.unidade_indice) == ("titulo", None) and "{{C01.1}}" in v.instrucao_corretiva


def test_numero_na_linha_fina_nunca_e_aceito():
    r = m1(artigo(["A Selic caiu."], linha_fina="Selic a 13,75% ao ano"))
    (v,) = r.violacoes
    assert (v.campo, v.unidade_indice, v.trecho) == ("linha_fina", None, "13,75%")
    assert v.instrucao_corretiva == "Remova o número 13,75% da linha fina: número só vale no título e no texto, por marcador."


def test_numero_no_gancho_visual_nunca_e_aceito():
    cenas = [
        {"inicio_s": 0, "fim_s": 6, "fala": "Olha a Selic.", "gancho_visual": "Gráfico com 13,75%", "claim_ids": []},
        {"inicio_s": 6, "fim_s": 54, "fala": "O Copom decidiu.", "gancho_visual": "Sala", "claim_ids": ["C01"]},
        {"inicio_s": 54, "fim_s": 60, "fala": "Até a próxima.", "gancho_visual": "Logo", "claim_ids": []},
    ]
    peca = Peca.model_validate({"celula_id": "iniciante__roteiro", "nivel": "iniciante", "formato": "roteiro",
                                "payload": {"cenas": cenas}, "claim_ids": ["C01"], "numeros_usados": [], "tentativa": 1})
    (v,) = m1(peca).violacoes
    assert (v.campo, v.unidade_indice, v.trecho) == ("gancho_visual", 0, "13,75%")
    assert "do gancho visual" in v.instrucao_corretiva


def test_numero_da_fala_da_cena_com_marcador_passa():
    fala = "O Copom levou a Selic a 13,75%."
    cenas = [
        {"inicio_s": 0, "fim_s": 6, "fala": "Olha a Selic.", "gancho_visual": "Gráfico", "claim_ids": []},
        {"inicio_s": 6, "fim_s": 54, "fala": fala, "gancho_visual": "Sala", "claim_ids": ["C01"]},
        {"inicio_s": 54, "fim_s": 60, "fala": "Até a próxima.", "gancho_visual": "Logo", "claim_ids": []},
    ]
    peca = Peca.model_validate({"celula_id": "iniciante__roteiro", "nivel": "iniciante", "formato": "roteiro",
                                "payload": {"cenas": cenas}, "claim_ids": ["C01"], "tentativa": 1,
                                "numeros_usados": [usado(fala, "13,75%", "C01", 1, indice=1)]})
    assert m1(peca).passou


def test_marcador_em_outra_unidade_nao_cobre_o_numero():
    a, b = "A Selic foi para 13,75%.", "Outra vez 13,75%."
    r = m1(artigo([a, b], usados=[usado(a, "13,75%", "C01", 1, indice=0)]))
    assert [(v.unidade_indice, v.trecho) for v in r.violacoes] == [(1, "13,75%")]


# M1: datas, anos e trimestres


@pytest.mark.parametrize("texto", [
    "Na reunião de 19/10 a Selic caiu.",
    "Em 19 de outubro a Selic caiu.",
    "Em 19/10/2026 a Selic caiu.",  # o claim não traz o ano: vale o ano do documento
    "Em 19 de outubro de 2026 a Selic caiu.",
    "O IPCA deve fechar 2027 abaixo do teto.",  # ano de um claim
    "A decisão vale para 2026.",  # ano do documento, que nenhum claim cita
    "A projeção vai até o 2T27.",
])
def test_data_com_lastro_passa(texto):
    r = m1(artigo([texto]))
    assert r.passou, [v.instrucao_corretiva for v in r.violacoes]
    assert r.detalhes.datas_conferidas >= 1


@pytest.mark.parametrize("texto, trecho", [
    ("Na reunião de 20/10 a Selic caiu.", "20/10"),
    ("Em 25 de dezembro a Selic caiu.", "25 de dezembro"),
    ("Em 19/10/2025 a Selic caiu.", "19/10/2025"),  # o ano do documento é 2026
    ("O IPCA deve fechar 2028 abaixo do teto.", "2028"),
    ("A projeção vai até o 3T27.", "3"),
])
def test_data_sem_lastro_e_violacao(texto, trecho):
    r = m1(artigo([texto]))
    assert r.violacoes and r.violacoes[0].trecho == trecho
    assert r.detalhes.desfechos.data_sem_lastro >= 1
    assert "Descarte" not in r.violacoes[0].instrucao_corretiva


def test_data_nao_conta_como_numero_varrido():
    r = m1(artigo(["Em 19/10 a Selic caiu."]))
    assert r.detalhes.numeros_varridos == 0 and r.detalhes.datas_conferidas == 1


def test_data_fora_do_plano_na_linha_fina():
    r = m1(artigo(["A Selic caiu."], linha_fina="Decisão de 20/10"))
    assert r.violacoes[0].campo == "linha_fina"


# M2


def texto_de(palavras):
    return " ".join(["palavra"] * palavras)


def m2(peca, nivel="iniciante"):
    return text_size.avaliar(peca, SPECS[nivel])


def test_artigo_dentro_da_faixa():
    r = m2(artigo([texto_de(200), texto_de(200)]))  # 400, e a faixa do Iniciante é 350 a 550
    assert r.passou and r.valor == 0
    assert r.faixa_esperada == "0 unidades fora do limite (de 350 a 550 palavras no texto das seções)"
    assert r.detalhes.grandeza == "palavras"
    (medida,) = r.detalhes.unidades
    assert (medida.medida, medida.limite_min, medida.limite_max, medida.dentro) == (400, 350, 550, True)


@pytest.mark.parametrize("palavras", [350, 550])
def test_artigo_nos_limites_da_faixa(palavras):
    assert m2(artigo([texto_de(palavras)])).passou


def test_artigo_curto():
    r = m2(artigo([texto_de(100)]))
    (v,) = r.violacoes
    assert (v.unidade_indice, v.campo) == (None, None)
    assert v.instrucao_corretiva.startswith("Acrescente texto às seções: de 100 para pelo menos 350 palavras")
    assert (v.esperado, v.obtido) == ("de 350 a 550 palavras", "100 palavras")


def test_artigo_longo():
    r = m2(artigo([texto_de(600)]))
    assert r.violacoes[0].instrucao_corretiva.startswith("Reduza o texto das seções: de 600 para no máximo 550")
    assert r.valor == 1 and not r.passou


def test_titulo_e_linha_fina_nao_entram_na_contagem():
    r = m2(artigo([texto_de(340)], titulo=texto_de(10), linha_fina=texto_de(10)))
    assert not r.passou and r.detalhes.unidades[0].medida == 340


def test_a_faixa_do_artigo_e_a_do_nivel():
    assert m2(artigo([texto_de(400)], nivel="avancado"), "avancado").passou is False  # 600 a 900
    assert m2(artigo([texto_de(700)], nivel="avancado"), "avancado").passou


def carrossel(textos, papeis=None):
    papeis = papeis or ["gancho", *["corpo"] * (len(textos) - 2), "conclusao"]
    slides = [{"papel": p, "texto": t, "claim_ids": [] if p != "corpo" else ["C01"]} for p, t in zip(papeis, textos)]
    return Peca.model_validate({"celula_id": "iniciante__carrossel", "nivel": "iniciante", "formato": "carrossel",
                                "payload": {"slides": slides}, "claim_ids": ["C01"], "numeros_usados": [], "tentativa": 1})


def test_carrossel_dentro_do_limite_de_cada_papel():
    r = m2(carrossel(["g" * 120, "c" * 220, "c" * 220, "f" * 160, "f" * 160][:3] + ["x" * 160]))
    assert r.passou
    assert r.faixa_esperada.endswith("(até 120 caracteres no gancho, 220 em cada slide de corpo e 160 na conclusão)")
    assert [(m.papel, m.limite_max) for m in r.detalhes.unidades] == [("gancho", 120), ("corpo", 220), ("corpo", 220), ("conclusao", 160)]
    assert r.detalhes.grandeza == "caracteres"


def test_carrossel_com_um_slide_acima_do_limite():
    r = m2(carrossel(["g" * 121, "c" * 220, "x" * 161]))
    assert [(v.unidade_indice, v.inicio, v.fim) for v in r.violacoes] == [(0, 0, 121), (2, 0, 161)]
    assert r.violacoes[0].instrucao_corretiva == "Reduza o slide 1 (gancho) de 121 para no máximo 120 caracteres, sem mudar os números."
    assert r.valor == 2 and r.violacoes[0].campo == "texto"


def test_carrossel_conta_o_caractere_acentuado_uma_vez():
    assert m2(carrossel(["ã" * 120, "ç" * 220, "é" * 160])).passou


def roteiro(falas, duracoes=(6, 48, 6)):
    cenas, t = [], 0
    for i, (fala, d) in enumerate(zip(falas, duracoes)):
        cenas.append({"inicio_s": t, "fim_s": t + d, "fala": fala, "gancho_visual": "Cena",
                      "claim_ids": [] if i in (0, len(falas) - 1) else ["C01"]})
        t += d
    return Peca.model_validate({"celula_id": "iniciante__roteiro", "nivel": "iniciante", "formato": "roteiro",
                                "payload": {"cenas": cenas}, "claim_ids": ["C01"], "numeros_usados": [], "tentativa": 1})


def test_roteiro_dentro_do_limite_de_cada_cena_e_do_total():
    r = m2(roteiro([texto_de(15), texto_de(120), texto_de(15)]))  # 15 em 6 s, 120 em 48 s: 150 no total
    assert r.passou
    assert r.faixa_esperada.endswith("(até 2,5 palavras por segundo em cada cena e até 150 palavras em 60 segundos)")
    assert [(m.unidade_indice, m.medida, m.limite_max) for m in r.detalhes.unidades] == [
        (0, 15, 15), (1, 120, 120), (2, 15, 15), (None, 150, 150)]


def test_roteiro_com_cena_acima_da_duracao():
    r = m2(roteiro([texto_de(16), texto_de(100), texto_de(10)]))
    (v,) = r.violacoes
    assert (v.unidade_indice, v.trecho) == (0, texto_de(16))
    assert v.instrucao_corretiva == "Reduza a fala da cena 1 de 16 para no máximo 15 palavras, que cabem em 6 s ao falar."


def test_roteiro_com_total_acima_do_limite_mesmo_com_as_cenas_dentro():
    r = m2(roteiro([texto_de(15), texto_de(120), texto_de(15)], duracoes=(6, 48, 6)))
    assert r.passou
    r = m2(roteiro([texto_de(10), texto_de(120), texto_de(15)], duracoes=(6, 48, 6)))
    assert r.passou
    # Cenas mais longas que o roteiro: cada uma cabe na sua duração, mas o total passa de 150 palavras.
    r = m2(roteiro([texto_de(15), texto_de(150), texto_de(15)], duracoes=(6, 60, 6)))
    assert [v.unidade_indice for v in r.violacoes] == [None]
    assert r.violacoes[0].instrucao_corretiva.startswith("Reduza o roteiro de 180 para no máximo 150 palavras no total")
    assert r.detalhes.unidades[-1].dentro is False


# M5


UNIDADE_SELIC = "A Selic é a taxa básica. Ela é como o preço do dinheiro emprestado."
ANALOGIA_SELIC = Analogia(termo="Selic", explicacao="o preço do dinheiro", origem="glossario",
                          glossario_ref="selic#1", revisada=False)


def plano_nivel(*analogias, nivel="iniciante"):
    return PlanoNivel(nivel=nivel, descartes=[], claims=[], tese="", tese_claim_ids=[], analogias=list(analogias))


def m5(secoes, plano=None, nivel="iniciante", glossario=GLOSSARIO):
    plano = plano if plano is not None else plano_nivel(ANALOGIA_SELIC)
    return analogy_rule.avaliar(artigo(secoes, nivel=nivel), SPECS[nivel], plano, glossario)


def test_termo_com_analogia_no_plano_e_marcador_na_mesma_unidade():
    r = m5([UNIDADE_SELIC])
    assert r.passou and r.valor == 0
    (t,) = r.detalhes.termos
    assert (t.termo, t.glossario_id, t.unidade_indice, t.analogia_no_plano, t.marcador_na_unidade) == ("Selic", "selic", 0, True, True)
    assert r.detalhes.aplicavel


def test_termo_sem_marcador_de_analogia_na_unidade():
    r = m5(["A Selic caiu para o menor nível do ano."])
    (v,) = r.violacoes
    assert (v.trecho, v.unidade_indice, v.campo, v.inicio, v.fim) == ("Selic", 0, "texto", 2, 7)
    assert v.instrucao_corretiva.startswith("Explique «Selic» na seção 1, na primeira vez que o usar, com a analogia do plano (o preço do dinheiro)")
    assert '"é como", "funciona como", "imagine que", "na prática"' in v.instrucao_corretiva


def test_termo_sem_analogia_no_plano():
    r = m5(["O CDI rende é como a poupança."], plano=plano_nivel(ANALOGIA_SELIC))
    (v,) = r.violacoes
    assert v.trecho == "CDI" and v.instrucao_corretiva.startswith("O termo «CDI» aparece na seção 1 sem analogia no plano")
    assert r.detalhes.termos[0].analogia_no_plano is False


def test_marcador_so_na_unidade_seguinte_nao_vale():
    r = m5(["A Selic caiu.", "A Selic é como o preço do dinheiro."])
    assert [v.unidade_indice for v in r.violacoes] == [0]


def test_so_a_primeira_ocorrencia_conta():
    r = m5(["A Selic é como o preço do dinheiro. A Selic caiu.", "Mais uma vez a Selic."])
    assert r.passou and len(r.detalhes.termos) == 1


def test_marcador_ignora_maiuscula_e_acento():
    assert m5(["A Selic. FUNCIONA COMO um termostato, E COMO a poupança."]).passou
    assert m5(["A Selic. É como um termostato."]).passou
    assert m5(["A Selic. E como um termostato."]).passou


def test_marcador_dentro_de_outra_palavra_nao_vale():
    assert not m5(["A Selic. Sempre é comovente, imagine queijo."]).passou


def test_termo_de_nivel_iniciante_nao_pede_analogia():
    assert m5(["Os juros caíram."]).passou


def test_termo_so_no_titulo_nao_e_procurado():
    peca = artigo(["Os preços cederam."], titulo="Selic em queda", linha_fina="A Selic e o CDI")
    r = analogy_rule.avaliar(peca, SPECS["iniciante"], plano_nivel(), GLOSSARIO)
    assert r.passou and r.detalhes.termos == []


def test_analogia_nova_e_ligada_ao_termo_pela_forma_escrita():
    nova = Analogia(termo="taxa Selic", explicacao="o preço do dinheiro", origem="nova", revisada=False)
    assert m5([UNIDADE_SELIC], plano=plano_nivel(nova)).passou
    assert not m5(["A Selic caiu."], plano=plano_nivel(nova)).passou


def test_cada_termo_acima_de_iniciante_e_conferido_na_sua_unidade():
    r = m5(["A Selic é como o preço do dinheiro.", "O CDI acompanha a Selic."], plano=plano_nivel(ANALOGIA_SELIC))
    assert [(v.trecho, v.unidade_indice) for v in r.violacoes] == [("CDI", 1)]


def test_a_regra_nao_vale_no_avancado():
    r = m5(["A Selic caiu e o CDI subiu."], nivel="avancado")
    assert r.passou and r.detalhes.aplicavel is False and r.detalhes.termos == []


HIATO = Analogia(termo="hiato do produto", explicacao="Definição simples.", origem="glossario", revisada=False)


def m5_intermediario(secoes, *explicacoes):
    return m5(secoes, plano=plano_nivel(*explicacoes, nivel="intermediario"), nivel="intermediario")


def test_intermediario_pede_explicacao_so_do_termo_avancado():
    r = m5_intermediario(["A Selic e o CDI subiram, ou seja, houve alta."], HIATO)
    assert r.passou and r.detalhes.aplicavel and r.detalhes.termos == []


def test_intermediario_termo_avancado_com_explicacao_e_marcador():
    r = m5_intermediario(["O hiato do produto, ou seja, a folga da economia, fechou."], HIATO)
    assert r.passou
    (t,) = r.detalhes.termos
    assert (t.termo, t.analogia_no_plano, t.marcador_na_unidade) == ("hiato do produto", True, True)


def test_intermediario_termo_avancado_sem_marcador():
    (v,) = m5_intermediario(["O hiato do produto fechou."], HIATO).violacoes
    assert v.trecho == "hiato do produto" and v.esperado == "marcador de explicação na seção 1"
    assert v.instrucao_corretiva.startswith(
        "Explique «hiato do produto» na seção 1, na primeira vez que o usar, com a explicação do plano (Definição simples.)")
    assert '"ou seja", "isto é", "quer dizer", "em outras palavras"' in v.instrucao_corretiva


def test_intermediario_termo_avancado_sem_explicacao_no_plano():
    (v,) = m5_intermediario(["O hiato do produto, ou seja, a folga, fechou."]).violacoes
    assert v.obtido == "termo sem explicação no plano"
    assert v.instrucao_corretiva.startswith("O termo «hiato do produto» aparece na seção 1 sem explicação no plano")


def test_carrossel_nomeia_a_unidade_como_slide():
    slides = [{"papel": "gancho", "texto": "Olha isso", "claim_ids": []},
              {"papel": "corpo", "texto": "A Selic caiu.", "claim_ids": ["C01"]},
              {"papel": "conclusao", "texto": "Fim", "claim_ids": []}]
    peca = Peca.model_validate({"celula_id": "iniciante__carrossel", "nivel": "iniciante", "formato": "carrossel",
                                "payload": {"slides": slides}, "claim_ids": ["C01"], "numeros_usados": [], "tentativa": 1})
    (v,) = analogy_rule.avaliar(peca, SPECS["iniciante"], plano_nivel(ANALOGIA_SELIC), GLOSSARIO).violacoes
    assert v.unidade_indice == 1 and v.instrucao_corretiva.startswith("Explique «Selic» no slide 2,")


# Valor, faixa e detalhes de cada métrica


def test_resultado_numerico_vale_o_numero_de_violacoes():
    r = m1(artigo(["Subiu 9,9% e 7,77%."]))
    assert (r.nome, r.valor, r.faixa_esperada, r.passou) == ("numeric_fidelity", 2.0, "0", False)
    assert r.detalhes.metrica == "numeric_fidelity"


def test_resultado_da_llm_judge_vale_true_ou_false():
    detalhes = LlmJudgeDetalhes(analogias=[], unidades=[VeredictoUnidade(unidade_indice=0, justificativa="ok", implicada=True)],
                                justificativa_nivel="j", nivel_percebido="iniciante")
    aprovado = resultado.montar(detalhes, [])
    assert (aprovado.valor, aprovado.faixa_esperada, aprovado.passou) == (True, "true", True)
    from sunontent.schemas import Violacao
    reprovado = resultado.montar(detalhes, [Violacao(metrica="llm_judge", trecho="x", esperado="a", obtido="b", instrucao_corretiva="c")])
    assert (reprovado.valor, reprovado.passou) == (False, False)


def test_resultado_do_plan_contract():
    detalhes = PlanContractDetalhes(checagens=[])
    r = resultado.montar(detalhes, [])
    assert (r.nome, r.valor, r.faixa_esperada, r.passou) == ("plan_contract", 0.0, "0", True)


# M3. readability

FACIL = "A taxa cai. O juro sobe."  # 6 palavras, 9 sílabas, 2 frases
# 2 frases de 30 palavras, todas de 3 sílabas: o IFL fica abaixo de zero
LONGA = " ".join(["Operação societária deliberada"] * 10) + "."


def m3(peca, nivel="iniciante"):
    return readability.avaliar(peca, SPECS[nivel], SPECS["iniciante"], SPECS)


def test_ifl_calculado_a_mao():
    r = m3(artigo([FACIL]))
    # 248,835 - 1,015 x (6 / 2) - 84,6 x (9 / 6) = 118,89
    assert r.valor == 118.9 and r.detalhes.ifl == 118.9
    assert (r.detalhes.palavras, r.detalhes.silabas, r.detalhes.frases) == (6, 9, 2)
    assert r.passou and r.violacoes == []


def test_numero_e_marcador_ficam_fora_da_leitura():
    com = m3(artigo([FACIL + " A Selic ficou em 13,75% ao ano, ou {{C01.1}}."]))
    sem = m3(artigo([FACIL + " A Selic ficou em ao ano, ou."]))
    assert com.detalhes.palavras == sem.detalhes.palavras


def test_so_o_texto_das_unidades_entra_na_leitura():
    r = m3(artigo([FACIL], titulo=LONGA, linha_fina=LONGA))
    assert r.detalhes.ifl == 118.9


def test_iniciante_abaixo_do_piso_reprova_com_a_frase_mais_longa():
    r = m3(artigo([FACIL, LONGA]))
    assert not r.passou and r.valor == r.detalhes.ifl < 30
    assert r.faixa_esperada == "IFL de pelo menos 30" and r.detalhes.ifl_minimo == 30
    (v,) = r.violacoes
    assert v.metrica == "readability" and v.trecho == LONGA and (v.unidade_indice, v.campo) == (1, "texto")
    assert (v.inicio, v.fim) == (0, len(LONGA)) and v.esperado == "IFL de pelo menos 30"
    assert "frase na seção 2 (30 palavras)" in v.instrucao_corretiva and "de até 18 palavras" in v.instrucao_corretiva


def test_a_violacao_aponta_ate_tres_frases_acima_da_meta():
    longa = "Operação societária deliberada " * 7 + "formalmente."  # 22 palavras, acima da meta de 18
    r = m3(artigo([longa, longa, longa, longa]))
    assert [v.unidade_indice for v in r.violacoes] == [0, 1, 2]


def test_intermediario_e_avancado_so_medem():
    for nivel in ("intermediario", "avancado"):
        r = m3(artigo([LONGA], nivel=nivel), nivel)
        assert r.passou and r.violacoes == [] and r.detalhes.ifl < 0
        assert r.faixa_esperada == "sem limite (só mede)" and r.detalhes.ifl_minimo is None


def como(peca, nivel):
    return peca.model_copy(update={"nivel": nivel})


def test_nivel_estatistico_do_carrossel_junta_o_piso_do_ifl_e_a_analogia():
    com_analogia = FACIL + " A taxa é como um preço."
    r = m3(carrossel([com_analogia, FACIL, FACIL]))
    assert r.detalhes.analogia_na_peca and r.detalhes.nivel_estatistico == "iniciante"
    # IFL alto, mas sem analogia: frase curta de outro nível não é estilo de Iniciante
    r = m3(como(carrossel([FACIL, FACIL, FACIL]), "intermediario"), "intermediario")
    assert not r.detalhes.analogia_na_peca and r.detalhes.nivel_estatistico == "acima_de_iniciante"
    # analogia, mas IFL abaixo do piso
    r = m3(como(carrossel([LONGA + " Imagine que sim.", LONGA, LONGA]), "avancado"), "avancado")
    assert r.detalhes.analogia_na_peca and r.detalhes.nivel_estatistico == "acima_de_iniciante"


MEDIA = "A decisão do comitê sobre a taxa básica de juros considerou a inflação corrente e as expectativas dos analistas para os próximos meses."


def test_nivel_do_artigo_vem_da_faixa_de_ifl_e_nao_da_analogia():
    # IFL 41,6: entre 27 e 45, a faixa do Intermediário, mesmo pedido como Avançado e sem analogia
    r = m3(artigo([MEDIA], nivel="avancado"), "avancado")
    assert 27 <= r.detalhes.ifl < 45 and r.detalhes.nivel_estatistico == "intermediario"
    assert not r.detalhes.analogia_na_peca
    assert m3(artigo([FACIL])).detalhes.nivel_estatistico == "iniciante"
    assert m3(artigo([LONGA]), "intermediario").detalhes.nivel_estatistico == "avancado"


def test_o_sinal_do_artigo_nao_reprova_nenhum_nivel_alem_do_piso_do_iniciante():
    for nivel in ("intermediario", "avancado"):
        for texto in (FACIL, MEDIA, LONGA):
            r = m3(artigo([texto], nivel=nivel), nivel)
            assert r.passou and r.violacoes == [] and r.faixa_esperada == "sem limite (só mede)"
    assert not m3(artigo([FACIL, LONGA])).passou  # o piso do Iniciante continua reprovando


def test_sem_as_faixas_o_artigo_fica_sem_sinal():
    r = readability.avaliar(artigo([FACIL]), SPECS["iniciante"], SPECS["iniciante"])
    assert r.detalhes.nivel_estatistico is None


def test_nivel_pelo_ifl_do_artigo_nos_cortes():
    nivel = lambda v: readability.nivel_pelo_ifl_do_artigo(v, SPECS)
    assert [nivel(v) for v in (80, 45, 44.9, 27, 26.9, -6)] == [
        "iniciante", "iniciante", "intermediario", "intermediario", "avancado", "avancado"]


def test_piso_do_carrossel_e_do_roteiro_vem_do_formato():
    facil = m3(carrossel([FACIL, FACIL, FACIL]))
    assert facil.passou and facil.faixa_esperada == "IFL de pelo menos 45"
    dificil = m3(roteiro([LONGA, LONGA, LONGA]))
    assert not dificil.passou and dificil.faixa_esperada == "IFL de pelo menos 45"
    assert "na cena 1" in dificil.violacoes[0].instrucao_corretiva


def test_fim_da_unidade_fecha_a_frase():
    r = m3(carrossel(["A taxa cai", "O juro sobe", "Fim"]))
    assert r.detalhes.frases == 3


# M4. term_density


def test_densidade_conta_so_termos_acima_de_iniciante_por_cem_palavras():
    # 20 palavras: Selic (intermediário) duas vezes, hiato do produto (avançado) uma vez, juros (iniciante) fora
    texto = "A Selic subiu e os juros também; a taxa Selic e o hiato do produto mudam, mas o mercado segue."
    r = term_density.avaliar(artigo([texto]), GLOSSARIO)
    assert r.detalhes.palavras == 20 and r.detalhes.ocorrencias == 3 and r.detalhes.ocorrencias_avancado == 1
    assert r.valor == r.detalhes.densidade == 15.0 and r.detalhes.densidade_avancado == 5.0
    assert {(t.glossario_id, t.ocorrencias) for t in r.detalhes.termos} == {("selic", 2), ("hiato", 1)}


def test_densidade_so_mede_e_nunca_reprova():
    r = term_density.avaliar(artigo(["Selic CDI IPCA Selic CDI IPCA"]), GLOSSARIO)
    assert r.passou and r.violacoes == [] and r.valor == 100.0
    assert r.faixa_esperada == "sem limite (só mede)"


def test_densidade_ignora_titulo_e_linha_fina():
    r = term_density.avaliar(artigo(["A taxa cai."], titulo="Selic CDI", linha_fina="IPCA"), GLOSSARIO)
    assert r.valor == 0.0 and r.detalhes.termos == []
