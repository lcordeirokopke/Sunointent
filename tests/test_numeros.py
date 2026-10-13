"""Parser de número BR (sunontent/numeros.py). Igualdade exata, em Decimal."""

from decimal import Decimal

import pytest

from sunontent.numeros import Data, NumeroNaoPrevisto, datas, interpretar, varrer


def d(texto: str) -> Decimal:
    return Decimal(texto)


@pytest.mark.parametrize(
    "bruto, valor, unidade",
    [
        ("12,5%", "12.5", "pct"),
        ("12 %", "12", "pct"),
        ("13,75%", "13.75", "pct"),
        ("-3,8%", "-3.8", "pct"),
        ("−3,8%", "-3.8", "pct"),
        ("11% ", "11", "pct"),
        ("0,50 p.p.", "0.50", "pp"),
        ("0,7 p.p.", "0.7", "pp"),
        ("2 pontos percentuais", "2", "pp"),
        ("47 bps", "0.47", "pp"),
        ("-1.476 bps", "-14.76", "pp"),
        ("25 pontos-base", "0.25", "pp"),
        ("R$ 1,2 bi", "1200", "BRL_mi"),
        ("R$1,2 bilhão", "1200", "BRL_mi"),
        ("R$3,1 bilhões", "3100", "BRL_mi"),
        ("R$8,9 trilhões", "8900000", "BRL_mi"),
        ("R$ 975,6 milhões", "975.6", "BRL_mi"),
        ("R$1.106,0 milhões", "1106.0", "BRL_mi"),
        ("R$ 500 mil", "0.5", "BRL_mi"),
        ("R$ 3 mi", "3", "BRL_mi"),
        ("R$ 390.000.000,00", "390000000.00", "BRL"),
        ("R$0,28", "0.28", "BRL"),
        ("R$ 1.234,56", "1234.56", "BRL"),
        ("R$5,15/US$", "5.15", "BRL"),
        ("2,5x", "2.5", "x"),
        ("3 vezes", "3", "x"),
        ("11,1 milhões", "11100000", "contagem"),
        ("3,5 milhões", "3500000", "contagem"),
        ("30 mil", "30000", "contagem"),
        ("1.938,6", "1938.6", "contagem"),
        ("3.592", "3592", "contagem"),
        ("0,085", "0.085", "contagem"),
        ("3,074", "3.074", "contagem"),
        ("0099", "99", "contagem"),
        ("8º", "8", "contagem"),
        ("281ª reunião", None, None),  # número e palavra: não é um único número
        ("2026", "2026", "data"),
    ],
)
def test_interpretar(bruto, valor, unidade):
    if valor is None:
        with pytest.raises(NumeroNaoPrevisto):
            interpretar(bruto)
        return
    achado = interpretar(bruto)
    assert achado.valor == d(valor)
    assert achado.unidade == unidade
    assert achado.bruto == bruto.strip()


def test_valor_e_decimal_e_nao_tem_notacao_cientifica():
    achado = interpretar("R$ 1,2 bi")
    assert isinstance(achado.valor, Decimal)
    assert str(achado.valor) == "1200"
    assert str(interpretar("11,1 milhões").valor) == "11100000"


def test_pct_e_pp_sao_unidades_diferentes():
    pct, pp = varrer("subiu 0,50% e depois 0,50 p.p.")
    assert (pct.unidade, pp.unidade) == ("pct", "pp")
    assert pct.valor == pp.valor == d("0.50")


@pytest.mark.parametrize(
    "texto",
    [
        "1,234.56",  # formato americano
        "12.5%",  # ponto decimal
        "1.93",  # milhar truncado
        "3.1",  # numeração de seção
        "0.500",
        "1,2,3",
        "1234.567",
        "US$ 5 milhões",
        "€ 3",
    ],
)
def test_formato_nao_previsto_falha_alto(texto):
    with pytest.raises(NumeroNaoPrevisto):
        varrer(texto)


def test_mensagem_de_erro_aponta_o_trecho():
    with pytest.raises(NumeroNaoPrevisto, match=r"1,234\.56"):
        varrer("a taxa foi de 1,234.56 no período")


def test_datas_viram_um_numero_por_componente():
    texto = "reunião de 15 e 16 de setembro de 2026"
    assert [(a.bruto, a.valor, a.unidade) for a in varrer(texto)] == [
        ("15", d("15"), "data"),
        ("16", d("16"), "data"),
        ("2026", d("2026"), "data"),
    ]
    assert [a.bruto for a in varrer("30/06/2026 e 15/9")] == ["30", "06", "2026", "15", "9"]
    assert [(a.bruto, a.valor) for a in varrer("2T26/2T25")] == [
        ("2", d("2")), ("26", d("26")), ("2", d("2")), ("25", d("25")),
    ]
    assert [(a.bruto, a.unidade) for a in varrer("a 281ª reunião e o 1º semestre")] == [
        ("281ª", "data"), ("1º", "data"),
    ]


def test_ano_isolado_e_data_mas_milhar_nao():
    assert [(a.bruto, a.unidade) for a in varrer("em 2026, 2.026 contratos")] == [
        ("2026", "data"), ("2.026", "contagem"),
    ]
    assert [(a.bruto, a.unidade) for a in varrer("ciclo 2026-2030")] == [
        ("2026", "data"), ("2030", "data"),
    ]


def test_offsets_apontam_para_o_bruto():
    texto = "Receita de R$3,1 bilhões (+12% vs. 2T25), margem de 70,2%."
    achados = varrer(texto)
    assert achados
    for a in achados:
        assert texto[a.inicio : a.fim] == a.bruto
    assert [a.bruto for a in achados] == ["R$3,1 bilhões", "12%", "2", "25", "70,2%"]


def test_identificadores_e_marcadores_nao_sao_numeros():
    texto = (
        "CNPJ nº 04.992.714/0001-84, NIRE 33.3.0026999-1, CEP 22.210-901, "
        "ri.b3.com.br, https://www.bcb.gov.br/x/2026.pdf, +55 (11) 4680-6788, "
        "às 10h06 e 10:00h, do Copom1, na B3, SNG2."
    )
    assert varrer(texto) == []


def test_numero_colado_a_letra_e_ignorado_mas_unidade_nao():
    assert varrer("5G e B3RL e COVID19") == []
    assert [a.bruto for a in varrer("7%a")] == ["7%"]


def test_sinal_so_vale_quando_nao_e_intervalo():
    assert [(a.bruto, a.valor) for a in varrer("de 4680-6788")] == [
        ("4680", d("4680")), ("6788", d("6788")),
    ]
    assert varrer("queda de -0,25 p.p.")[0].valor == d("-0.25")


def test_pontuacao_final_nao_vira_decimal():
    assert [a.bruto for a in varrer("foi 10,5. Depois 3,2, e 4.")] == ["10,5", "3,2", "4"]


def test_parenteses_de_valor_negativo_ficam_fora_do_bruto():
    assert [(a.bruto, a.valor) for a in varrer("Despesas (975,6) 15,5%")] == [
        ("975,6", d("975.6")), ("15,5%", d("15.5")),
    ]


def test_escala_com_quebra_de_linha_no_meio():
    achado = varrer("R$1.701,2\nmilhões")[0]
    assert (achado.valor, achado.unidade) == (d("1701.2"), "BRL_mi")


def test_datas_com_dia_em_cada_formato():
    texto = "Em 15 e 16 de setembro de 2026, depois em 19/10, 01/10/26 e 1º de julho."
    assert datas(texto) == [
        Data(19, 10, None, "19/10"), Data(1, 10, 2026, "01/10/26"),
        Data(15, 9, 2026, "15 e 16 de setembro de 2026"), Data(16, 9, 2026, "15 e 16 de setembro de 2026"),
        Data(1, 7, None, "1º de julho"),
    ]


def test_sem_dia_trimestre_lei_e_data_invalida_nao_sao_datas():
    assert datas("dez/25, junho de 2026, 2T26/2T25, Lei 224/2025, 6.404 e 32/13") == []


def test_ordinal_de_periodo_abreviado_e_data():
    assert [(a.bruto, a.unidade) for a in varrer("1º tri 2028, 1º trimestre e 3º lugar")] == [
        ("1º", "data"), ("2028", "data"), ("1º", "data"), ("3º", "contagem"),
    ]
