"""E4: pré-filtro, complemento e gate do plano (docs/arquiteturas.md, E4.1 e E4.3), como funções puras, e o
planejador (E4.2) com o LLM simulado."""

import json
from datetime import date
from pathlib import Path

import pytest

from conftest import RepositorioMemoria, cliente_falso
from sunontent import glossario as modulo_glossario
from sunontent import persistencia
from sunontent.adaptacao import complemento, gate, limpeza, planejador, prefiltro
from sunontent.levelspec import LevelSpec
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import (
    FORMATOS,
    NIVEIS,
    Ancora,
    CelulaState,
    Claim,
    FactSheet,
    Numero,
    ProblemaPlano,
    SaidaPlanejador,
    celula_id,
)
from test_glossario import analogia, termo

def spec(nivel, foco, priorizadas, descartaveis, max_claims):
    return LevelSpec.model_validate({
        "nivel": nivel, "versao": 1, "foco": foco, "tags_priorizadas": priorizadas, "tags_descartaveis": descartaveis,
        "peso_papel": {"principal": 3, "apoio": 2, "contexto": 1},
        "formatos": {
            "artigo": {"max_claims": max_claims, "palavras": {"min": 300, "max": 500}},
            "carrossel": {"max_claims": 3, "slides": {"min": 5, "max": 7},
                          "caracteres": {"gancho": 120, "corpo": 220, "conclusao": 160}},
            "roteiro": {"max_claims": 3, "duracao_s": 60, "palavras_por_segundo": 2.5, "abertura_s": 6,
                        "fechamento_s": 6},
        },
    })


SPECS = {
    "iniciante": spec("iniciante", "bolso", ["impacto_pratico"], ["metodologico"], 3),
    "intermediario": spec("intermediario", "alocação", [], [], 4),
    "avancado": spec("avancado", "análise", [], [], 5),
}

GLOSSARIO = modulo_glossario.validar({"versao": 1, "termos": [
    termo("juros", "juros", nivel="iniciante"),
    termo("selic", "Selic", ["taxa Selic"], analogias=[analogia("A Selic é o preço do dinheiro.")]),
    termo("ipca", "IPCA", []),  # sem analogia: vai para a lista do planejador
    termo("hiato", "hiato do produto", [], nivel="avancado"),
    termo("spread", "spread", ["spread bancário"], ambiguo=True),  # sozinho, "spread" não casa
    termo("ipca_15", "IPCA-15", []),
]})


def numero(bruto, valor, unidade):
    return Numero(valor=valor, unidade=unidade, bruto=bruto, ancora=Ancora(pagina=1, offset_inicio=0, offset_fim=1))


def claim(id, texto, tipo="resultado", tags=("indicador_chave",), numeros=()):
    return Claim(id=id, texto=texto, numeros=list(numeros), tipo=tipo, tags=list(tags),
                 ancora=Ancora(pagina=1, offset_inicio=0, offset_fim=1), literal_fonte="x")


CLAIMS = {c.id: c for c in [
    claim("C01", "O Copom reduziu a Selic.", "decisao", ["indicador_chave", "impacto_pratico"]),
    claim("C02", "O IPCA subiu.", "resultado", ["indicador_chave"]),
    claim("C03", "O Copom projeta inflação menor.", "projecao", ["contexto_macro"]),
    claim("C04", "A premissa de câmbio é estável.", "condicionante", ["metodologico"]),
    claim("C05", "O hiato do produto está positivo.", "condicionante", ["contexto_macro", "metodologico"]),
    claim("C06", "Os juros caíram.", "risco", ["impacto_pratico"]),
]}
PREFILTRO = prefiltro.filtrar(list(CLAIMS.values()), SPECS)


def nivel(nome, claims, descartes=(), analogias=(), tese_ids=None, prioridades=None, tese=None):
    prioridades = prioridades or range(1, len(claims) + 1)
    return {
        "nivel": nome,
        "descartes": [dict(zip(("claim_id", "motivo", "relacionado"), d)) for d in descartes],
        "claims": [{"claim_id": c, "papel": "principal" if i == 0 else "apoio", "prioridade": p}
                   for i, (c, p) in enumerate(zip(claims, prioridades))],
        "tese": f"Tese do {nome}." if tese is None else tese,
        "tese_claim_ids": tese_ids if tese_ids is not None else list(claims[:1]),
        "analogias": [{"termo": t, "explicacao": e} for t, e in analogias],
    }


def saida(nucleo=("C01",), ini=None, inter=None, ava=None):
    return SaidaPlanejador.model_validate({
        "nucleo": list(nucleo),
        "niveis": [
            ini or nivel("iniciante", ["C01", "C02"], analogias=[("IPCA", "Um termômetro dos preços.")]),
            inter or nivel("intermediario", ["C01", "C02", "C03"]),
            ava or nivel("avancado", ["C01", "C02", "C03", "C05"]),
        ],
    })


def plano(s=None, rodadas=1):
    return complemento.completar(s or saida(), CLAIMS, PREFILTRO, GLOSSARIO, rodadas=rodadas)


def problemas(p, claims=CLAIMS, filtro=PREFILTRO):
    return [(x.regra, x.nivel) for x in gate.conferir(p, claims, filtro, SPECS, GLOSSARIO)]


# Pré-filtro


def test_prefiltro_descarta_so_quem_tem_todas_as_tags_descartaveis():
    assert PREFILTRO.candidatos["iniciante"] == ["C01", "C02", "C03", "C05", "C06"]
    assert [(d.claim_id, d.motivo, d.origem) for d in PREFILTRO.descartes["iniciante"]] == [
        ("C04", "metodologico", "prefiltro"),
    ]
    assert PREFILTRO.candidatos["avancado"] == list(CLAIMS)
    assert PREFILTRO.descartes["intermediario"] == []


def test_claim_com_tag_descartavel_e_outra_continua_candidato():
    assert "C05" in PREFILTRO.candidatos["iniciante"]


def test_candidatos_nos_3_niveis():
    assert PREFILTRO.candidatos_nos_3 == ["C01", "C02", "C03", "C05", "C06"]


# Complemento


def test_atribuir_fonte_pelo_tipo():
    p = plano(saida(inter=nivel("intermediario", ["C01", "C03", "C06", "C02"])))
    assert {c.claim_id: c.atribuir_fonte for c in p.nivel("intermediario").claims} == {
        "C01": False, "C03": True, "C06": True, "C02": False,
    }


def test_descartes_do_prefiltro_do_planejador_e_secundario():
    s = saida(ini=nivel("iniciante", ["C01", "C02"], [("C05", "exige_base_tecnica", None)],
                        analogias=[("IPCA", "Termômetro.")]))
    descartes = [(d.claim_id, d.motivo, d.origem) for d in plano(s).nivel("iniciante").descartes]
    assert descartes == [
        ("C04", "metodologico", "prefiltro"),
        ("C05", "exige_base_tecnica", "planejador"),
        ("C03", "secundario", "padrao"),
        ("C06", "secundario", "padrao"),
    ]


def test_analogias_do_glossario_e_nova():
    analogias = plano().nivel("iniciante").analogias
    assert [(a.termo, a.origem, a.glossario_ref, a.revisada, a.explicacao) for a in analogias] == [
        ("Selic", "glossario", "selic#1", False, "A Selic é o preço do dinheiro."),
        ("IPCA", "nova", None, False, "Um termômetro dos preços."),
    ]
    assert plano().nivel("intermediario").analogias == []


def test_analogia_nova_casada_pela_forma_sem_caixa_nem_acento():
    s = saida(ini=nivel("iniciante", ["C01", "C02"], analogias=[("ipca", "Termômetro.")]))
    assert [a.termo for a in plano(s).nivel("iniciante").analogias] == ["Selic", "IPCA"]


def test_analogia_nova_fora_da_lista_ou_fora_dos_claims_sai():
    s = saida(ini=nivel("iniciante", ["C01"], analogias=[
        ("Selic", "Outra analogia."),  # já tem analogia no glossário
        ("IPCA", "Termômetro."),  # IPCA não está nos claims usados
        ("dólar", "Moeda."),  # fora do glossário
    ]))
    assert [(a.termo, a.origem) for a in plano(s).nivel("iniciante").analogias] == [("Selic", "glossario")]


def test_tese_vai_como_o_planejador_mandou():
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C03", "fora_do_foco", None)],
                          tese_ids=["C01", "C03", "C05", "C99"]))
    assert plano(s).nivel("intermediario").tese_claim_ids == ["C01", "C03", "C05", "C99"]


def test_prioridade_vai_para_o_plano():
    s = saida(inter=nivel("intermediario", ["C01", "C02", "C03"], prioridades=[2, 3, 1]))
    assert [c.prioridade for c in plano(s).nivel("intermediario").claims] == [2, 3, 1]


def test_lista_de_termos_sem_analogia_para_o_planejador():
    assert complemento.termos_sem_analogia(GLOSSARIO, CLAIMS, PREFILTRO.candidatos["iniciante"]) == ["ipca", "hiato"]




# Gate


def test_plano_correto_passa():
    assert problemas(plano()) == []


def test_id_inexistente():
    s = saida(nucleo=("C01", "C99"),
              inter=nivel("intermediario", ["C01", "C98"], [("C97", "fora_do_foco", None),
                                                           ("C03", "redundante", "C96")], tese_ids=["C98"]))
    achados = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    inexistentes = [(x.nivel, x.detalhe) for x in achados if x.regra == "id_inexistente"]
    assert inexistentes == [
        (None, "o núcleo cita C99, que não existe no FactSheet"),
        ("intermediario", "C98 nos claims não existe no FactSheet"),
        ("intermediario", "C97 nos descartes não existe no FactSheet"),
        ("intermediario", "C96 como relacionado do descarte de C03 não existe no FactSheet"),
        ("intermediario", "C98 na tese não existe no FactSheet"),
    ]


def test_contraditorio_claim_usado_e_descartado():
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C02", "fora_do_foco", None)]))
    assert ("contraditorio", "intermediario") in problemas(plano(s))


def test_contraditorio_descartado_pelo_prefiltro_e_pelo_planejador():
    s = saida(ini=nivel("iniciante", ["C01", "C02"], [("C04", "fora_do_foco", None)],
                        analogias=[("IPCA", "Termômetro.")]))
    assert problemas(plano(s)) == [("contraditorio", "iniciante")]


def test_contraditorio_claim_usado_que_o_prefiltro_descartou():
    s = saida(ini=nivel("iniciante", ["C01", "C04"]))
    assert problemas(plano(s)) == [("contraditorio", "iniciante")]


def test_contraditorio_claim_repetido_nos_claims():
    s = saida(inter=nivel("intermediario", ["C01", "C02", "C02"]))
    assert problemas(plano(s)) == [("contraditorio", "intermediario")]


def test_nucleo_ausente_de_um_nivel():
    s = saida(ava=nivel("avancado", ["C02", "C03"]))
    assert problemas(plano(s)) == [("nucleo_ausente", "avancado")]


def test_nucleo_que_nao_e_candidato_nos_3():
    s = saida(nucleo=("C01", "C04"),
              ini=nivel("iniciante", ["C01", "C02"], analogias=[("IPCA", "Termômetro.")]),
              inter=nivel("intermediario", ["C01", "C04"]), ava=nivel("avancado", ["C01", "C04"]))
    assert problemas(plano(s)) == [("nucleo_ausente", None), ("nucleo_ausente", "iniciante")]


def test_acima_do_limite():
    s = saida(ini=nivel("iniciante", ["C01", "C02", "C03", "C06"], analogias=[("IPCA", "Termômetro.")]))
    assert problemas(plano(s)) == [("acima_do_limite", "iniciante")]
    s = saida(ini=nivel("iniciante", ["C01", "C02", "C03"], analogias=[("IPCA", "Termômetro.")]))
    assert problemas(plano(s)) == []


def test_jargao_sem_analogia():
    s = saida(ini=nivel("iniciante", ["C01", "C02"]))
    achados = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    assert [(x.regra, x.nivel) for x in achados] == [("jargao_sem_analogia", "iniciante")]
    assert "'IPCA'" in achados[0].detalhe


def test_termo_iniciante_nao_pede_analogia():
    s = saida(nucleo=("C06",), ini=nivel("iniciante", ["C06"]), inter=nivel("intermediario", ["C06"]),
              ava=nivel("avancado", ["C06"]))
    assert problemas(plano(s)) == []


def test_jargao_so_no_iniciante():
    s = saida(inter=nivel("intermediario", ["C01", "C02", "C05"]))
    assert problemas(plano(s)) == []


def test_redundante_sem_relacionado_ou_com_relacionado_fora_dos_claims():
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C03", "redundante", None),
                                                           ("C05", "redundante", "C06")]))
    assert problemas(plano(s)) == [("motivo_invalido", "intermediario")] * 2
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C03", "redundante", "C02")]))
    assert problemas(plano(s)) == []


def test_exige_base_tecnica_so_com_termo_acima_do_nivel():
    s = saida(ini=nivel("iniciante", ["C01", "C02"], [("C06", "exige_base_tecnica", None)],
                        analogias=[("IPCA", "Termômetro.")]))
    assert problemas(plano(s)) == [("motivo_invalido", "iniciante")]  # "juros" é iniciante
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C05", "exige_base_tecnica", None)]))
    assert problemas(plano(s)) == []  # hiato do produto é avancado
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C03", "exige_base_tecnica", None)]))
    assert problemas(plano(s)) == [("motivo_invalido", "intermediario")]


def test_exige_base_tecnica_nunca_vale_no_avancado():
    s = saida(ava=nivel("avancado", ["C01", "C02"], [("C05", "exige_base_tecnica", None)]))
    assert problemas(plano(s)) == [("motivo_invalido", "avancado")]


def test_tese_com_id_nao_usado_vazia_ou_sem_claim():
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C03", "fora_do_foco", None)], tese_ids=["C01", "C03"]))
    achados = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    assert [(x.regra, x.nivel) for x in achados] == [("tese_invalida", "intermediario")]
    assert achados[0].detalhe.startswith("C03 está em tese_claim_ids, mas não está nos claims do nível")
    s = saida(inter=nivel("intermediario", ["C01", "C02"], tese_ids=[], tese=" "))
    assert problemas(plano(s)) == [("tese_invalida", "intermediario")] * 2
    # Id que não existe é id_inexistente, não tese_invalida.
    s = saida(inter=nivel("intermediario", ["C01", "C02"], tese_ids=["C01", "C99"]))
    assert problemas(plano(s)) == [("id_inexistente", "intermediario")]


def test_tese_com_numero_ou_jargao_no_iniciante():
    s = saida(ava=nivel("avancado", ["C01", "C02"], tese="O Copom cortou a Selic para 13,75%."))
    achados = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    assert [(x.regra, x.nivel) for x in achados] == [("tese_invalida", "avancado")] and "'13,75%'" in achados[0].detalhe
    s = saida(ava=nivel("avancado", ["C01", "C02"], tese="Em 17 de setembro de 2026, no 2T26, a Selic caiu."))
    assert problemas(plano(s)) == []  # data não é número da tese, e o avançado pode ter termo técnico
    s = saida(ini=nivel("iniciante", ["C01", "C02"], analogias=[("IPCA", "Termômetro.")],
                        tese="A Selic caiu e o IPCA subiu."))
    achados = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    assert [(x.regra, x.nivel) for x in achados] == [("tese_invalida", "iniciante")]
    assert "'Selic', 'IPCA'" in achados[0].detalhe
    s = saida(ini=nivel("iniciante", ["C01", "C02"], analogias=[("IPCA", "Termômetro.")], tese="Os juros caíram."))
    assert problemas(plano(s)) == []  # "juros" é termo de iniciante
    s = saida(ava=nivel("avancado", ["C01", "C02"], tese="O IPCA-15 subiu."))
    assert problemas(plano(s)) == []  # o 15 do IPCA-15 é parte do termo, não número da tese


def test_tese_vazia_da_limpeza_so_e_aceita_sem_ids():
    s = saida(inter=nivel("intermediario", ["C01", "C02"], tese_ids=[], tese=""))
    assert gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO, aceitar_tese_vazia=True) == []
    s = saida(inter=nivel("intermediario", ["C01", "C02"], tese_ids=["C01"], tese=""))
    achados = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO, aceitar_tese_vazia=True)
    assert [(x.regra, x.nivel) for x in achados] == [("tese_invalida", "intermediario")]


@pytest.mark.parametrize("explicacao, motivo", [
    ("É como subir 10% ao ano.", "tem número"),
    ("É como {{marcador}} a mais.", "tem marcador"),
    ("É como o IPCA.", "só repete o termo"),
    ("ipca", "só repete o termo"),
])
def test_analogia_nova_invalida(explicacao, motivo):
    s = saida(ini=nivel("iniciante", ["C01", "C02"], analogias=[("IPCA", explicacao)]))
    achados = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    assert [(x.regra, x.nivel) for x in achados] == [("analogia_invalida", "iniciante")]
    assert motivo in achados[0].detalhe and "'É como...'" in achados[0].detalhe


def test_so_repete_o_termo_com_forma_composta_e_variante():
    hiato = GLOSSARIO.termos["hiato"]
    assert gate.so_repete_o_termo("É como o hiato do produto.", hiato)
    assert gate.so_repete_o_termo("A taxa Selic é a Selic.", GLOSSARIO.termos["selic"])
    assert not gate.so_repete_o_termo("É como o IPCA, um termômetro dos preços.", GLOSSARIO.termos["ipca"])


def test_analogia_nova_valida_e_a_do_glossario_nao_sao_conferidas():
    s = saida(ini=nivel("iniciante", ["C01", "C02"], analogias=[("IPCA", "É como o termômetro dos preços no mercado.")]))
    assert problemas(plano(s)) == []


@pytest.mark.parametrize("campo, valor", [("termo", ""), ("explicacao", "")])
def test_analogia_do_planejador_sem_termo_ou_explicacao_e_recusada_no_schema(campo, valor):
    dados = saida().model_dump()
    dados["niveis"][0]["analogias"][0][campo] = valor
    with pytest.raises(ValueError, match="at least 1 character"):
        SaidaPlanejador.model_validate(dados)


@pytest.mark.parametrize("prioridades", [[1, 1, 2], [1, 2, 4], [0, 1, 2]])
def test_prioridade_invalida(prioridades):
    s = saida(inter=nivel("intermediario", ["C01", "C02", "C03"], prioridades=[max(p, 1) for p in prioridades]))
    if 0 in prioridades:  # o schema recusa prioridade menor que 1
        dados = s.model_dump()
        dados["niveis"][1]["claims"][0]["prioridade"] = 0
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            SaidaPlanejador.model_validate(dados)
        return
    achados = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    assert [(x.regra, x.nivel) for x in achados] == [("prioridade_invalida", "intermediario")]
    assert f"são {prioridades}" in achados[0].detalhe


RECEITA = {c.id: c for c in [
    claim("C01", "A receita da B3 alcançou R$ 3,1 bilhões no 2T26.", numeros=[numero("R$ 3,1 bilhões", 3100, "BRL_mi")]),
    claim("C02", "A receita da B3 cresceu 12,2% no 2T26.", numeros=[numero("12,2%", "12.2", "pct")]),
    claim("C03", "A receita da B3 foi de R$ 3.100 milhões no 2T26, alta de 12,2%.",
          numeros=[numero("R$ 3.100 milhões", 3100, "BRL_mi"), numero("12,2%", "12.2", "pct")]),
    claim("C04", "O prazo de retirada começa em 18/09.", "evento_corporativo", ["governanca"]),
    claim("C05", "O prazo de retirada termina em 19/10.", "evento_corporativo", ["governanca"]),
    claim("C06", "O prazo de retirada termina em 19 de outubro.", "evento_corporativo", ["governanca"]),
    claim("C07", "O prazo de retirada começa em 18 de setembro de 2026.", "evento_corporativo", ["governanca"]),
    claim("C08", "O prazo de retirada termina em 18 de outubro.", "evento_corporativo", ["governanca"]),
    claim("C09", "A receita subiu 12,2% e o lucro, 12,2%.",
          numeros=[numero("12,2%", "12.2", "pct"), numero("12,2%", "12.2", "pct")]),
]}


def test_redundante_so_com_todos_os_numeros_e_datas_no_relacionado():
    assert gate.faltam_no_relacionado(RECEITA["C01"], RECEITA["C02"]) == ["R$ 3,1 bilhões"]
    assert gate.faltam_no_relacionado(RECEITA["C01"], RECEITA["C03"]) == []  # 3,1 bilhões = 3.100 milhões
    assert gate.faltam_no_relacionado(RECEITA["C04"], RECEITA["C05"]) == ["18/09"]
    assert gate.faltam_no_relacionado(RECEITA["C05"], RECEITA["C06"]) == []  # 19/10 é 19 de outubro
    assert gate.faltam_no_relacionado(RECEITA["C07"], RECEITA["C08"]) == ["18 de setembro de 2026"]
    assert gate.faltam_no_relacionado(RECEITA["C08"], RECEITA["C07"]) == ["18 de outubro"]
    assert gate.faltam_no_relacionado(RECEITA["C01"], RECEITA["C01"]) == []  # o ano 2T26 é o mesmo
    assert gate.faltam_no_relacionado(RECEITA["C09"], RECEITA["C02"]) == ["12,2%"]  # o número repetido conta duas vezes
    filtro = prefiltro.filtrar(list(RECEITA.values()), SPECS)
    s = saida(nucleo=("C02",), ini=nivel("iniciante", ["C02"]), inter=nivel("intermediario", ["C02"]),
              ava=nivel("avancado", ["C02", "C03"], [("C01", "redundante", "C02"), ("C04", "redundante", "C03")]))
    p = complemento.completar(s, RECEITA, filtro, GLOSSARIO, rodadas=1)
    achados = gate.conferir(p, RECEITA, filtro, SPECS, GLOSSARIO)
    assert [(x.regra, x.nivel) for x in achados] == [("motivo_invalido", "avancado")] * 2
    assert "C02 não tem 'R$ 3,1 bilhões'" in achados[0].detalhe


def test_sigla_em_outra_caixa_e_termo_ambiguo_no_complemento_e_no_gate():
    claims = {c.id: c for c in [
        claim("C01", "O Copom reduziu os juros.", "decisao", ["impacto_pratico"]),
        claim("C02", "A inflação medida pelo ipca subiu, e o spread caiu."),  # nem sigla em minúscula nem "spread"
        claim("C03", "O spread bancário subiu."),
    ]}
    filtro = prefiltro.filtrar(list(claims.values()), SPECS)
    assert complemento.termos_sem_analogia(GLOSSARIO, claims, filtro.candidatos["iniciante"]) == ["spread"]
    s = saida(ini=nivel("iniciante", ["C01", "C02"]), inter=nivel("intermediario", ["C01"]),
              ava=nivel("avancado", ["C01"]))
    assert problemas(complemento.completar(s, claims, filtro, GLOSSARIO, rodadas=1), claims, filtro) == []
    s = saida(ini=nivel("iniciante", ["C01", "C03"]), inter=nivel("intermediario", ["C01"]),
              ava=nivel("avancado", ["C01"]))
    assert problemas(complemento.completar(s, claims, filtro, GLOSSARIO, rodadas=1), claims, filtro) == [
        ("jargao_sem_analogia", "iniciante")]
    s = saida(ini=nivel("iniciante", ["C01", "C03"], analogias=[("Spread Bancário", "É como a margem da loja.")]),
              inter=nivel("intermediario", ["C01"]), ava=nivel("avancado", ["C01"]))
    p = complemento.completar(s, claims, filtro, GLOSSARIO, rodadas=1)
    assert [(a.termo, a.origem) for a in p.nivel("iniciante").analogias] == [("spread", "nova")]
    assert problemas(p, claims, filtro) == []


def test_mensagens_do_contraditorio_falam_do_que_o_planejador_ve():
    s = saida(ini=nivel("iniciante", ["C01", "C02"], [("C04", "fora_do_foco", None)],
                        analogias=[("IPCA", "Termômetro.")]))
    (achado,) = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    assert achado.detalhe == "C04 não é candidato neste nível; tire dos descartes"
    (achado,) = gate.conferir(plano(saida(ini=nivel("iniciante", ["C01", "C04"]))), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    assert achado.detalhe == "C04 não é candidato neste nível; tire dos claims"
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C02", "fora_do_foco", None)]))
    (achado,) = gate.conferir(plano(s), CLAIMS, PREFILTRO, SPECS, GLOSSARIO)
    assert achado.detalhe == "C02 está nos claims e nos descartes (fora_do_foco); deixe em um só"


# Limpeza depois da última rodada


def limpar(s, claims=CLAIMS, filtro=PREFILTRO):
    p = complemento.completar(s, claims, filtro, GLOSSARIO, rodadas=3)
    restantes = gate.conferir(p, claims, filtro, SPECS, GLOSSARIO)
    return limpeza.limpar(p, claims, filtro, SPECS, GLOSSARIO, restantes)


def test_limpeza_de_plano_sem_problema_nao_muda_nada():
    assert limpar(saida()) == plano(rodadas=3)


def test_limpeza_retira_ids_inexistentes_e_redundante_sem_relacionado_vira_secundario():
    s = saida(nucleo=("C01", "C99"), ini=nivel("iniciante", ["C01", "C98", "C02"],
                                                [("C97", "fora_do_foco", None), ("C03", "redundante", "C96")],
                                                analogias=[("IPCA", "Termômetro.")], tese_ids=["C01", "C95"]))
    p = limpar(s)
    ini = p.nivel("iniciante")
    assert p.nucleo == ["C01"]
    assert [(c.claim_id, c.prioridade) for c in ini.claims] == [("C01", 1), ("C02", 2)]
    assert ("C03", "secundario", "padrao", None) in [(d.claim_id, d.motivo, d.origem, d.relacionado) for d in ini.descartes]
    assert "C97" not in [d.claim_id for d in ini.descartes]
    assert ini.tese_claim_ids == ["C01"]
    assert "[id_inexistente] plano: C99 saiu do núcleo" in p.aviso
    assert "[id_inexistente] iniciante: C95 saiu da tese" in p.aviso
    assert "[id_inexistente] iniciante: C03 passou a secundario (relacionado C96 não existe)" in p.aviso
    assert gate.conferir(p, CLAIMS, PREFILTRO, SPECS, GLOSSARIO) == []


def test_limpeza_resolve_o_contraditorio():
    s = saida(ini=nivel("iniciante", ["C01", "C04", "C02"], analogias=[("IPCA", "Termômetro.")]),
              inter=nivel("intermediario", ["C01", "C02", "C02"], [("C02", "fora_do_foco", None),
                                                                   ("C03", "fora_do_foco", None),
                                                                   ("C03", "redundante", "C01")]))
    p = limpar(s)
    assert [c.claim_id for c in p.nivel("iniciante").claims] == ["C01", "C02"]
    inter = p.nivel("intermediario")
    assert [c.claim_id for c in inter.claims] == ["C01", "C02"]
    assert [(d.claim_id, d.motivo) for d in inter.descartes if d.origem == "planejador"] == [("C03", "fora_do_foco")]


def test_limpeza_inclui_o_nucleo_ausente_e_tira_o_que_nao_e_candidato():
    s = saida(nucleo=("C01", "C04"), ini=nivel("iniciante", ["C02"], [("C01", "fora_do_foco", None)],
                                                analogias=[("IPCA", "Termômetro.")]))
    p = limpar(s)
    assert p.nucleo == ["C01"]
    assert [(c.claim_id, c.papel, c.prioridade) for c in p.nivel("iniciante").claims] == [
        ("C02", "principal", 1), ("C01", "apoio", 2)]
    assert "C01" not in [d.claim_id for d in p.nivel("iniciante").descartes]
    with pytest.raises(FalhaTerminal, match="o núcleo ficou vazio"):
        limpar(saida(nucleo=("C04",)))


def test_limpeza_tira_a_analogia_invalida_e_o_claim_do_iniciante():
    s = saida(ini=nivel("iniciante", ["C01", "C02"], analogias=[("IPCA", "É como subir 10%.")], tese_ids=["C01"]))
    p = limpar(s)
    ini = p.nivel("iniciante")
    assert [c.claim_id for c in ini.claims] == ["C01"]
    assert ("C02", "exige_base_tecnica", "padrao") in [(d.claim_id, d.motivo, d.origem) for d in ini.descartes]
    assert [a.termo for a in ini.analogias] == ["Selic"]
    assert "a analogia nova de 'IPCA' saiu (tem número)" in p.aviso
    with pytest.raises(FalhaTerminal, match="C02, do núcleo, tem termo sem analogia no iniciante"):
        limpar(saida(nucleo=("C01", "C02"), ini=nivel("iniciante", ["C01", "C02"])))


def test_limpeza_corta_pela_prioridade_e_mantem_o_nucleo():
    s = saida(ini=nivel("iniciante", ["C02", "C03", "C06", "C01"], prioridades=[1, 2, 3, 4],
                        analogias=[("IPCA", "Termômetro.")], tese_ids=["C06"]))
    p = limpar(s)
    ini = p.nivel("iniciante")
    assert [(c.claim_id, c.prioridade) for c in ini.claims] == [("C02", 1), ("C03", 2), ("C01", 3)]
    assert ("C06", "secundario", "padrao") in [(d.claim_id, d.motivo, d.origem) for d in ini.descartes]
    assert (ini.tese, ini.tese_claim_ids) == ("", [])  # a tese citava só o C06, que saiu
    assert "[acima_do_limite] iniciante: C06 saiu pela prioridade (máximo de 3)" in p.aviso
    assert "[tese_invalida] iniciante: a tese saiu (sem claim)" in p.aviso


def test_limpeza_tira_a_analogia_do_termo_que_saiu_pelo_limite():
    s = saida(ini=nivel("iniciante", ["C01", "C02", "C03", "C05"], analogias=[("IPCA", "Termômetro.")]))
    ini = limpar(s).nivel("iniciante")
    assert [c.claim_id for c in ini.claims] == ["C01", "C02", "C03"]
    assert [a.termo for a in ini.analogias] == ["Selic", "IPCA"]  # sem o hiato do produto do C05


def test_limpeza_redundante_com_relacionado_cortado_vira_secundario():
    s = saida(ini=nivel("iniciante", ["C01", "C02", "C03", "C06"], [("C05", "redundante", "C06")],
                        analogias=[("IPCA", "Termômetro.")]))
    ini = limpar(s).nivel("iniciante")
    assert "C06" not in [c.claim_id for c in ini.claims]
    assert ("C05", "secundario", "padrao") in [(d.claim_id, d.motivo, d.origem) for d in ini.descartes]


def test_limpeza_tira_a_tese_do_iniciante_com_termo_tecnico():
    s = saida(ini=nivel("iniciante", ["C01", "C02"], analogias=[("IPCA", "Termômetro.")], tese="A Selic caiu."))
    p = limpar(s)
    assert (p.nivel("iniciante").tese, p.nivel("iniciante").tese_claim_ids) == ("", [])
    assert "[tese_invalida] iniciante: a tese saiu (com termo técnico)" in p.aviso


def test_limpeza_motivo_invalido_vira_secundario_e_tese_com_numero_sai():
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C03", "exige_base_tecnica", None),
                                                            ("C05", "redundante", "C06")],
                          tese="A Selic caiu 0,5 p.p."))
    inter = limpar(s).nivel("intermediario")
    assert [(d.claim_id, d.motivo) for d in inter.descartes] == [
        ("C03", "secundario"), ("C05", "secundario"), ("C04", "secundario"), ("C06", "secundario")]
    assert (inter.tese, inter.tese_claim_ids) == ("", [])


def test_limpeza_renumera_as_prioridades():
    s = saida(inter=nivel("intermediario", ["C01", "C02", "C03"], prioridades=[5, 2, 2]))
    p = limpar(s)
    assert [c.prioridade for c in p.nivel("intermediario").claims] == [3, 1, 2]
    assert p.aviso == "[prioridade_invalida] intermediario: prioridades renumeradas de 1 a n"


def test_texto_do_problema():
    p = ProblemaPlano(regra="acima_do_limite", nivel="iniciante", detalhe="4 claims usados, acima do máximo de 3")
    assert p.texto() == "[acima_do_limite] iniciante: 4 claims usados, acima do máximo de 3"
    assert ProblemaPlano(regra="id_inexistente", detalhe="x").texto() == "[id_inexistente] plano: x"


@pytest.mark.parametrize("campo, valor, mensagem", [
    ("nucleo", [], "at least 1"),
    ("nucleo", ["C01", "C02", "C03", "C05"], "at most 3"),
])
def test_saida_do_planejador_limita_o_nucleo(campo, valor, mensagem):
    dados = saida().model_dump()
    dados[campo] = valor
    with pytest.raises(ValueError, match=mensagem):
        SaidaPlanejador.model_validate(dados)


def test_saida_do_planejador_exige_3_niveis_e_analogia_so_no_iniciante():
    dados = saida().model_dump()
    with pytest.raises(ValueError, match="niveis precisa ter"):
        SaidaPlanejador.model_validate({**dados, "niveis": dados["niveis"][:2]})
    dados["niveis"][1]["analogias"] = [{"termo": "IPCA", "explicacao": "x"}]
    with pytest.raises(ValueError, match="analogias só no iniciante"):
        SaidaPlanejador.model_validate(dados)


def test_saida_do_planejador_recusa_motivo_do_codigo():
    dados = saida().model_dump()
    dados["niveis"][0]["descartes"] = [{"claim_id": "C03", "motivo": "secundario", "relacionado": None}]
    with pytest.raises(ValueError, match="motivo"):
        SaidaPlanejador.model_validate(dados)


# Planejador (E4.2), com o LLM simulado


RAIZ = Path(__file__).resolve().parent.parent
FACTSHEET = FactSheet(doc_id="copom_ata_281", tipo_documento="copom_ata", emissor="Banco Central do Brasil",
                      data_documento=date(2026, 9, 17), claims=list(CLAIMS.values()), tabela_numeros=[],
                      assinado=True)
TERMOS_NOVOS = complemento.termos_sem_analogia(GLOSSARIO, CLAIMS, PREFILTRO.candidatos["iniciante"])


def chamar(llm, **extra):
    spans = []
    resultado = planejador.planejar(llm, RAIZ, FACTSHEET, SPECS, PREFILTRO, GLOSSARIO, TERMOS_NOVOS,
                                    run_id="r", registrar_span=spans.append, **extra)
    return resultado, spans


def test_prompt_traz_o_levelspec_dos_3_niveis_e_os_claims():
    prompt = planejador.montar_prompt(RAIZ, FACTSHEET, SPECS, PREFILTRO, GLOSSARIO, TERMOS_NOVOS)
    assert "versao" not in prompt
    assert "ata de reunião do Comitê de Política Monetária (Banco Central do Brasil, 2026-09-17)" in prompt
    for spec in SPECS.values():
        assert f"## {spec.nivel}\n- Foco: {spec.foco}." in prompt
        assert f"No máximo {spec.max_claims} claims usados." in prompt
    assert "Tags priorizadas: impacto_pratico." in prompt
    assert "só com as tags metodologico saíram deste nível por regra" in prompt
    assert ("C01 | decisao | indicador_chave, impacto_pratico | candidato nos 3 níveis |"
            " Selic (intermediario, com analogia) | O Copom reduziu a Selic.") in prompt
    assert "C04 | condicionante | metodologico | candidato só em intermediario, avancado | nenhum |" in prompt
    assert ("C05 | condicionante | contexto_macro, metodologico | candidato nos 3 níveis |"
            " hiato do produto (avancado, sem analogia) |") in prompt
    assert "C06 | risco | impacto_pratico | candidato nos 3 níveis | nenhum |" in prompt  # "juros" é de iniciante
    assert "- IPCA: Definição simples." in prompt and "- hiato do produto: Definição simples." in prompt
    assert prompt.lstrip().startswith("Você é o planejador")
    assert "não tem números (datas podem ficar)" in prompt and "Nunca vale no avançado" in prompt
    assert 'A explicação começa com "É como..."' in prompt and "prioridade: numere os claims" in prompt
    assert "tem todos os números e todas as datas deste" in prompt
    assert "No iniciante, prefira usar o claim de impacto prático cujo termo tem analogia" in prompt
    assert "Numa ata do Copom, o que mais importa ao leitor é a decisão de juros" in prompt


@pytest.mark.parametrize("tipo, trecho", [
    ("b3_release", "Num release de resultados, o que mais importa"),
    ("cvm_fato_relevante", "Num fato relevante, o que mais importa"),
])
def test_prompt_diz_o_que_vem_primeiro_no_tipo_de_documento(tipo, trecho):
    factsheet = FACTSHEET.model_copy(update={"tipo_documento": tipo})
    prompt = planejador.montar_prompt(RAIZ, factsheet, SPECS, PREFILTRO, GLOSSARIO, TERMOS_NOVOS)
    assert trecho in prompt and "Numa ata do Copom" not in prompt
    assert "- Selic:" not in prompt  # já tem analogia no glossário
    assert "# Correção" not in prompt


def test_prompt_sem_termos_novos():
    prompt = planejador.montar_prompt(RAIZ, FACTSHEET, SPECS, PREFILTRO, GLOSSARIO, [])
    assert "Nenhum: todos os termos técnicos" in prompt


def test_rodada_1_usa_o_raciocinio_padrao():
    llm = cliente_falso([saida().model_dump_json()])
    resultado, spans = chamar(llm)
    assert resultado == saida()
    assert llm._provider.raciocinios == [None]
    assert [(s.no, s.nivel, s.celula_id, s.rodada) for s in spans] == [("adapter", None, None, 1)]


def test_correcao_traz_so_os_problemas_e_o_plano_enviado():
    llm = cliente_falso([saida().model_dump_json()])
    anterior = saida(ini=nivel("iniciante", ["C01", "C02", "C03", "C06"]))
    problema = "[acima_do_limite] iniciante: 4 claims usados, acima do máximo de 3"
    _, spans = chamar(llm, anterior=anterior, problemas=[problema], rodada=2)
    assert [s.rodada for s in spans] == [2]
    (prompt,) = llm._provider.prompts
    correcao = prompt.split("# Correção", 1)[1]
    assert f"## Problemas\n\n- {problema}\n" in correcao
    assert '"claim_id": "C06"' in correcao.split("## Plano enviado", 1)[1]
    assert llm._provider.raciocinios == ["low"]


def test_resposta_fora_do_schema_e_repetida_com_o_erro():
    dados = saida().model_dump()
    dados["niveis"][0]["descartes"] = [{"claim_id": "C03", "motivo": "secundario", "relacionado": None}]
    llm = cliente_falso([json.dumps(dados), saida().model_dump_json()])
    resultado, spans = chamar(llm)
    assert resultado == saida()
    assert [s.falha_classe for s in spans] == ["retriavel", None]
    assert "niveis.0.descartes.0.motivo" in llm._provider.prompts[1]


def test_tres_respostas_fora_do_schema_sao_terminais():
    llm = cliente_falso(['{"nucleo": []}'] * 3)
    with pytest.raises(FalhaTerminal, match="adapter .modelo-falso.: 3 respostas fora do schema"):
        chamar(llm)


# Gravação (persistencia.py), com o repositório em memória


RUN = "copom_20261004T142205Z_9f2c1a"


def celulas():
    return [CelulaState(celula_id=celula_id(n, f), nivel=n, formato=f) for n in NIVEIS for f in FORMATOS]


@pytest.fixture
def repo_com_factsheet():
    repo = RepositorioMemoria()
    repo.registrar_run(RUN)
    repo.factsheets[RUN] = FACTSHEET.model_dump(mode="json", exclude={"tabela_numeros"})
    return repo


def test_rodadas_do_gate_no_banco_e_na_copia_local(repo_com_factsheet, tmp_path):
    problemas = [ProblemaPlano(regra="acima_do_limite", nivel="iniciante", detalhe="4 claims"),
                 ProblemaPlano(regra="id_inexistente", detalhe="C99")]
    s = saida()
    persistencia.gravar_validacao_plano(repo_com_factsheet, tmp_path, RUN, 1, problemas, s)
    persistencia.gravar_validacao_plano(repo_com_factsheet, tmp_path, RUN, 1, problemas, s)  # repetir não duplica
    persistencia.gravar_validacao_plano(repo_com_factsheet, tmp_path, RUN, 2, [], s)
    assert sorted(repo_com_factsheet.validacoes_plano) == [(RUN, 1, 1), (RUN, 1, 2)]
    assert sorted(repo_com_factsheet.rodadas_plano) == [(RUN, 1), (RUN, 2)]  # a rodada que passou também
    local = json.loads((tmp_path / "runs" / RUN / "validacoes_plano" / "1.json").read_text(encoding="utf-8"))
    assert local == {"rodada": 1, "problemas": [
        {"regra": "acima_do_limite", "nivel": "iniciante", "detalhe": "4 claims"},
        {"regra": "id_inexistente", "nivel": None, "detalhe": "C99"},
    ], "saida": s.model_dump(mode="json")}
    passou = json.loads((tmp_path / "runs" / RUN / "validacoes_plano" / "2.json").read_text(encoding="utf-8"))
    assert passou == {"rodada": 2, "problemas": [], "saida": s.model_dump(mode="json")}


def test_reiniciar_apaga_as_rodadas_do_run(repo_com_factsheet, tmp_path):
    problema = [ProblemaPlano(regra="id_inexistente", detalhe="C99")]
    persistencia.gravar_validacao_plano(repo_com_factsheet, tmp_path, RUN, 1, problema, saida())
    repo_com_factsheet.validacoes_plano[("outro_run", 1, 1)] = problema[0]
    persistencia.reiniciar_validacoes_plano(repo_com_factsheet, tmp_path, RUN)
    assert list(repo_com_factsheet.validacoes_plano) == [("outro_run", 1, 1)]
    assert not repo_com_factsheet.rodadas_plano
    assert list((tmp_path / "runs" / RUN / "validacoes_plano").glob("*.json")) == []
    persistencia.reiniciar_validacoes_plano(repo_com_factsheet, tmp_path / "sem_pasta", RUN)  # sem cópia local


def test_plano_e_9_celulas_pendentes(repo_com_factsheet, tmp_path):
    p = plano()
    persistencia.gravar_plano(repo_com_factsheet, tmp_path, RUN, p, celulas())
    assert repo_com_factsheet.plano_gravado(RUN) == p
    assert len(repo_com_factsheet.celulas) == 9
    assert {(c.estado, c.tentativa) for c in repo_com_factsheet.celulas.values()} == {("PENDENTE", 0)}
    pasta = tmp_path / "runs" / RUN
    assert json.loads((pasta / "plano.json").read_text(encoding="utf-8")) == p.model_dump(mode="json")
    estado = json.loads((pasta / "celulas" / "iniciante__carrossel" / "estado.json").read_text(encoding="utf-8"))
    assert estado == {"celula_id": "iniciante__carrossel", "nivel": "iniciante", "formato": "carrossel",
                      "tentativa": 0, "estado": "PENDENTE"}


def test_plano_sem_factsheet_e_recusado(tmp_path):
    repo = RepositorioMemoria()
    repo.registrar_run(RUN)
    with pytest.raises(FalhaTerminal, match="não tem FactSheet"):
        persistencia.gravar_plano(repo, tmp_path, RUN, plano(), celulas())
    assert not (tmp_path / "runs" / RUN / "plano.json").exists()


def test_carregar_plano_regrava_a_copia_local_que_falta(repo_com_factsheet, tmp_path):
    assert persistencia.carregar_plano(repo_com_factsheet, tmp_path, RUN) is None
    persistencia.gravar_plano(repo_com_factsheet, tmp_path, RUN, plano(), celulas())
    (tmp_path / "runs" / RUN / "plano.json").unlink()
    (tmp_path / "runs" / RUN / "celulas" / "avancado__roteiro" / "estado.json").unlink()
    p, cs = persistencia.carregar_plano(repo_com_factsheet, tmp_path, RUN)
    assert p == plano() and len(cs) == 9
    assert (tmp_path / "runs" / RUN / "plano.json").exists()
    assert (tmp_path / "runs" / RUN / "celulas" / "avancado__roteiro" / "estado.json").exists()


def test_gravar_o_mesmo_plano_de_novo_nao_e_erro_e_outro_plano_e(repo_com_factsheet, tmp_path):
    persistencia.gravar_plano(repo_com_factsheet, tmp_path, RUN, plano(), celulas())
    persistencia.gravar_plano(repo_com_factsheet, tmp_path, RUN, plano(), celulas())  # resposta perdida
    with pytest.raises(FalhaTerminal, match="já tem outro plano"):
        persistencia.gravar_plano(repo_com_factsheet, tmp_path, RUN, plano(rodadas=2), celulas())
    assert repo_com_factsheet.plano_gravado(RUN).rodadas == 1
    assert json.loads((tmp_path / "runs" / RUN / "plano.json").read_text(encoding="utf-8"))["rodadas"] == 1


def test_plano_carregado_e_uma_copia_reconstruida(repo_com_factsheet, tmp_path):
    persistencia.gravar_plano(repo_com_factsheet, tmp_path, RUN, plano(), celulas())
    p, _ = persistencia.carregar_plano(repo_com_factsheet, tmp_path, RUN)
    p.niveis[0].claims.clear()
    assert repo_com_factsheet.plano_gravado(RUN) == plano()


def test_atualizar_celula(repo_com_factsheet, tmp_path):
    persistencia.gravar_plano(repo_com_factsheet, tmp_path, RUN, plano(), celulas())
    gerando = CelulaState(celula_id="avancado__roteiro", nivel="avancado", formato="roteiro", tentativa=1,
                          estado="GERANDO")
    persistencia.atualizar_celula(repo_com_factsheet, tmp_path, RUN, gerando)
    _, cs = persistencia.carregar_plano(repo_com_factsheet, tmp_path, RUN)
    assert {c.celula_id: (c.estado, c.tentativa) for c in cs}["avancado__roteiro"] == ("GERANDO", 1)
    local = tmp_path / "runs" / RUN / "celulas" / "avancado__roteiro" / "estado.json"
    assert json.loads(local.read_text(encoding="utf-8"))["estado"] == "GERANDO"
    outro = RepositorioMemoria()
    outro.registrar_run(RUN)
    with pytest.raises(FalhaTerminal, match="não existe em celulas"):
        persistencia.atualizar_celula(outro, tmp_path, RUN, gerando)


def test_carregar_factsheet_com_os_claims_da_quarentena_em_ordem_de_id(repo_com_factsheet):
    humano = claim("C100", "O Copom manteve o ritmo.", "decisao")
    repo_com_factsheet.claims_humanos[(RUN, "C100")] = humano
    repo_com_factsheet.claims_humanos[("outro_run", "C07")] = claim("C07", "Outro.")
    factsheet = persistencia.carregar_factsheet(repo_com_factsheet, RUN)
    assert [c.id for c in factsheet.claims] == [*CLAIMS, "C100"]
    assert factsheet.claims[-1] == humano and factsheet.assinado
    with pytest.raises(FalhaTerminal, match="não tem FactSheet"):
        persistencia.carregar_factsheet(RepositorioMemoria(), RUN)


def test_celula_state_confere_o_id():
    with pytest.raises(ValueError, match="não confere"):
        CelulaState(celula_id="iniciante__artigo", nivel="avancado", formato="artigo")
