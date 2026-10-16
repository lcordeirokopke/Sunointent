"""Schemas da peça, do redator, do scorecard e do estado da célula (sunontent/schemas.py)."""

import pytest
from pydantic import ValidationError

from sunontent.schemas import (
    METRICAS,
    AnalogyRuleDetalhes,
    CelulaState,
    ChecagemContrato,
    LlmJudgeDetalhes,
    MedidaUnidade,
    MetricaResultado,
    NumericFidelityDetalhes,
    NumeroUsado,
    Peca,
    PlanContractDetalhes,
    SaidaArtigo,
    SaidaCarrossel,
    SaidaRoteiro,
    Scorecard,
    TextSizeDetalhes,
    VeredictoAnalogia,
    VeredictoUnidade,
    Violacao,
)


def secao(texto="A Selic caiu para {{C01.1}} ao ano.", claim_ids=("C01",)):
    return {"texto": texto, "claim_ids": list(claim_ids)}


def artigo(**campos):
    base = {"titulo": "Selic cai para {{C01.1}}", "linha_fina": "O Copom decidiu", "secoes": [secao()]}
    return {**base, **campos}


def slide(papel, claim_ids=("C01",), texto="Texto do slide"):
    return {"papel": papel, "texto": texto, "claim_ids": list(claim_ids)}


def carrossel(slides=None):
    return {"slides": slides or [slide("gancho", ()), slide("corpo"), slide("corpo", ("C02",)), slide("conclusao", ())]}


def cena(inicio, fim, claim_ids=("C01",), fala="Fala", gancho_visual="Gráfico"):
    return {"inicio_s": inicio, "fim_s": fim, "fala": fala, "gancho_visual": gancho_visual, "claim_ids": list(claim_ids)}


def roteiro(cenas=None):
    return {"cenas": cenas or [cena(0, 6, ()), cena(6, 30), cena(30, 54, ("C02",)), cena(54, 60, ())]}


# Artigo


def test_artigo_valido_aceita_numero_com_marcador_no_titulo():
    saida = SaidaArtigo.model_validate(artigo())
    assert saida.claim_ids() == ["C01"] and saida.textos() == ["A Selic caiu para {{C01.1}} ao ano."]


def test_secao_sem_claim_e_recusada():
    with pytest.raises(ValidationError, match="secoes.0.claim_ids"):
        SaidaArtigo.model_validate(artigo(secoes=[secao(claim_ids=())]))


def test_artigo_sem_secoes_e_recusado():
    with pytest.raises(ValidationError, match="secoes"):
        SaidaArtigo.model_validate(artigo(secoes=[]))


def test_linha_fina_nao_aceita_marcador():
    with pytest.raises(ValidationError, match="linha_fina não aceita número com marcador"):
        SaidaArtigo.model_validate(artigo(linha_fina="Selic em {{C01.1}}"))


def test_claim_ids_do_artigo_sao_a_uniao_sem_repetir():
    saida = SaidaArtigo.model_validate(artigo(secoes=[secao(claim_ids=("C01", "C02")), secao(claim_ids=("C02", "C03"))]))
    assert saida.claim_ids() == ["C01", "C02", "C03"]


# Carrossel


def test_carrossel_valido_aceita_gancho_e_conclusao_sem_claim():
    saida = SaidaCarrossel.model_validate(carrossel())
    assert [s.papel for s in saida.slides] == ["gancho", "corpo", "corpo", "conclusao"]
    assert saida.claim_ids() == ["C01", "C02"]


@pytest.mark.parametrize("papeis", [
    ["corpo", "corpo", "conclusao"],  # não começa no gancho
    ["gancho", "corpo", "corpo"],  # não termina na conclusão
    ["gancho", "gancho", "conclusao"],  # dois ganchos
    ["gancho", "conclusao", "conclusao"],  # duas conclusões
    ["gancho", "conclusao", "corpo", "conclusao"],  # conclusão no meio
])
def test_carrossel_com_estrutura_invalida(papeis):
    slides = [slide(p) for p in papeis]
    with pytest.raises(ValidationError, match="exatamente um gancho no início"):
        SaidaCarrossel.model_validate(carrossel(slides))


def test_slide_de_corpo_sem_claim_e_recusado():
    slides = [slide("gancho", ()), slide("corpo", ()), slide("conclusao", ())]
    with pytest.raises(ValidationError, match=r"slide de corpo usa pelo menos um claim \(slides 1\)"):
        SaidaCarrossel.model_validate(carrossel(slides))


def test_carrossel_precisa_de_gancho_corpo_e_conclusao():
    with pytest.raises(ValidationError, match="slides"):
        SaidaCarrossel.model_validate(carrossel([slide("gancho", ()), slide("conclusao", ())]))


# Roteiro


def test_roteiro_valido_aceita_abertura_e_fechamento_sem_claim():
    saida = SaidaRoteiro.model_validate(roteiro())
    assert saida.claim_ids() == ["C01", "C02"] and len(saida.textos()) == 4


def test_roteiro_comeca_em_zero():
    with pytest.raises(ValidationError, match="primeira cena começa em 0 s"):
        SaidaRoteiro.model_validate(roteiro([cena(2, 6, ()), cena(6, 30), cena(30, 60, ())]))


def test_roteiro_com_lacuna():
    with pytest.raises(ValidationError, match="cenas são contíguas: a cena 2 começa em 32 s e a anterior termina em 30"):
        SaidaRoteiro.model_validate(roteiro([cena(0, 6, ()), cena(6, 30), cena(32, 60, ())]))


def test_roteiro_com_sobreposicao():
    with pytest.raises(ValidationError, match="cenas são contíguas"):
        SaidaRoteiro.model_validate(roteiro([cena(0, 6, ()), cena(6, 30), cena(28, 60, ())]))


def test_cena_que_termina_antes_de_comecar():
    with pytest.raises(ValidationError, match="a cena 1 termina em 6 s"):
        SaidaRoteiro.model_validate(roteiro([cena(0, 6, ()), cena(6, 6), cena(6, 60, ())]))


def test_cena_de_claim_sem_claim_e_recusada():
    with pytest.raises(ValidationError, match=r"cena de claim usa pelo menos um claim \(cenas 1\)"):
        SaidaRoteiro.model_validate(roteiro([cena(0, 6, ()), cena(6, 54, ()), cena(54, 60, ())]))


def test_gancho_visual_nao_aceita_marcador():
    with pytest.raises(ValidationError, match="gancho_visual não aceita número com marcador"):
        SaidaRoteiro.model_validate(roteiro([cena(0, 6, (), gancho_visual="Selic {{C01.1}}"), cena(6, 30), cena(30, 60, ())]))


def test_roteiro_precisa_de_abertura_claim_e_fechamento():
    with pytest.raises(ValidationError, match="cenas"):
        SaidaRoteiro.model_validate(roteiro([cena(0, 30, ()), cena(30, 60, ())]))


# NumeroUsado


def test_numero_usado_no_texto_tem_unidade():
    n = NumeroUsado(unidade_indice=0, inicio=10, fim=15, claim_id="C01", ordem=1)
    assert n.campo == "texto"


def test_numero_usado_no_titulo_nao_tem_unidade():
    n = NumeroUsado(campo="titulo", inicio=0, fim=5, claim_id="C01", ordem=1)
    assert n.unidade_indice is None


@pytest.mark.parametrize("campos", [
    {"campo": "titulo", "unidade_indice": 0},
    {"campo": "texto", "unidade_indice": None},
    {"campo": "texto", "unidade_indice": 0, "inicio": 5, "fim": 5},
    {"campo": "texto", "unidade_indice": 0, "ordem": 0},
    {"campo": "linha_fina", "unidade_indice": None},
])
def test_numero_usado_invalido(campos):
    base = {"campo": "texto", "unidade_indice": 0, "inicio": 0, "fim": 5, "claim_id": "C01", "ordem": 1}
    with pytest.raises(ValidationError):
        NumeroUsado(**{**base, **campos})


# Peca


def peca(formato="artigo", payload=None, **campos):
    payloads = {
        "artigo": {"titulo": "Selic cai para 13,75%", "linha_fina": "O Copom decidiu", "secoes": [secao("A Selic caiu.")]},
        "carrossel": carrossel(),
        "roteiro": roteiro(),
    }
    base = {
        "celula_id": f"iniciante__{formato}", "nivel": "iniciante", "formato": formato,
        "payload": payload or payloads[formato], "claim_ids": ["C01", "C02"] if formato != "artigo" else ["C01"],
        "numeros_usados": [], "tentativa": 1,
    }
    return Peca.model_validate({**base, **campos})


@pytest.mark.parametrize("formato, tipo", [("artigo", "ArtigoPayload"), ("carrossel", "CarrosselPayload"), ("roteiro", "RoteiroPayload")])
def test_peca_valida_em_cada_formato(formato, tipo):
    assert type(peca(formato).payload).__name__ == tipo


def test_peca_roundtrip_json_mantem_o_tipo_do_payload():
    original = peca("carrossel")
    assert Peca.model_validate_json(original.model_dump_json()) == original


def test_peca_com_payload_de_outro_formato():
    with pytest.raises(ValidationError, match="não é de uma peça carrossel"):
        peca("carrossel", payload=roteiro(), claim_ids=["C01", "C02"])


def test_peca_com_celula_id_que_nao_confere():
    with pytest.raises(ValidationError, match="não confere"):
        peca("artigo", celula_id="avancado__artigo")


def test_peca_com_claim_ids_diferentes_da_uniao():
    with pytest.raises(ValidationError, match="união dos claim_ids"):
        peca("artigo", claim_ids=["C01", "C09"])


def test_peca_com_numero_em_unidade_inexistente():
    n = NumeroUsado(unidade_indice=3, inicio=0, fim=5, claim_id="C01", ordem=1)
    with pytest.raises(ValidationError, match="unidade inexistente"):
        peca("artigo", numeros_usados=[n])


def test_peca_com_tentativa_zero():
    with pytest.raises(ValidationError, match="tentativa"):
        peca("artigo", tentativa=0)


# Violacao


def violacao(**campos):
    base = {"metrica": "numeric_fidelity", "trecho": "4,75%", "esperado": "marcador", "obtido": "4,75%",
            "instrucao_corretiva": "Use o marcador"}
    return Violacao.model_validate({**base, **campos})


def test_violacao_com_posicao():
    v = violacao(unidade_indice=2, campo="texto", inicio=10, fim=15)
    assert (v.unidade_indice, v.campo, v.inicio, v.fim) == (2, "texto", 10, 15)


def test_violacao_sem_posicao():
    v = violacao()
    assert (v.unidade_indice, v.campo, v.inicio, v.fim) == (None, None, None, None)


@pytest.mark.parametrize("campos", [
    {"inicio": 3},
    {"fim": 3},
    {"inicio": 5, "fim": 5},
    {"campo": "fala"},
    {"metrica": "legibilidade"},
])
def test_violacao_invalida(campos):
    with pytest.raises(ValidationError):
        violacao(**campos)


def test_as_cinco_metricas():
    assert METRICAS == ("numeric_fidelity", "text_size", "analogy_rule", "llm_judge", "plan_contract")


# Detalhes, MetricaResultado e Scorecard


DETALHES = {
    "numeric_fidelity": NumericFidelityDetalhes(numeros_varridos=3, com_marcador=3, datas_conferidas=1, desfechos={}),
    "text_size": TextSizeDetalhes(
        grandeza="palavras", limite="de 350 a 550 palavras no texto das seções",
        unidades=[MedidaUnidade(unidade_indice=0, medida=400, limite_min=350, limite_max=550, dentro=True)]),
    "analogy_rule": AnalogyRuleDetalhes(aplicavel=False, termos=[]),
    "llm_judge": LlmJudgeDetalhes(
        analogias=[VeredictoAnalogia(termo="Selic", justificativa="Explica o preço do dinheiro.", correta=True)],
        unidades=[VeredictoUnidade(unidade_indice=0, justificativa="Os claims dizem isso.", implicada=True)],
        nivel_percebido="iniciante"),
    "plan_contract": PlanContractDetalhes(
        checagens=[ChecagemContrato(nome="quantidade_de_slides", esperado="5 a 7", obtido="6", passou=True)]),
}


def resultado(nome="numeric_fidelity", violacoes=(), **campos):
    base = {"nome": nome, "valor": float(len(violacoes)), "faixa_esperada": "0", "passou": not violacoes,
            "violacoes": list(violacoes), "detalhes": DETALHES[nome]}
    return MetricaResultado.model_validate({**base, **campos})


@pytest.mark.parametrize("nome", METRICAS)
def test_cada_metrica_tem_o_seu_modelo_de_detalhes(nome):
    r = resultado(nome)
    assert r.detalhes.metrica == nome
    # O jsonb lido de volta escolhe o modelo certo pelo nome da métrica.
    assert MetricaResultado.model_validate_json(r.model_dump_json()).detalhes == r.detalhes


def test_detalhes_de_outra_metrica():
    with pytest.raises(ValidationError, match="detalhes de text_size na métrica numeric_fidelity"):
        resultado("numeric_fidelity", detalhes=DETALHES["text_size"])


def test_passou_exige_ausencia_de_violacoes():
    with pytest.raises(ValidationError, match="passou é verdadeiro se, e somente se"):
        resultado(violacoes=[violacao()], passou=True)
    with pytest.raises(ValidationError, match="passou é verdadeiro se, e somente se"):
        resultado(passou=False)


def test_violacao_de_outra_metrica_na_metrica():
    with pytest.raises(ValidationError, match="violação de text_size na métrica numeric_fidelity"):
        resultado(violacoes=[violacao(metrica="text_size")])


def test_valor_booleano_do_juiz():
    r = resultado("llm_judge", valor=True)
    assert r.valor is True


def test_valor_numerico_vira_float():
    assert resultado(violacoes=[violacao()]).valor == 1.0


def scorecard(metricas, veredito, **campos):
    base = {"celula_id": "iniciante__artigo", "tentativa": 1, "metricas": metricas, "veredito": veredito,
            "custo_tokens": 0, "duracao_ms": 12}
    return Scorecard.model_validate({**base, **campos})


def test_scorecard_aprovada_com_todas_as_metricas_dentro_da_faixa():
    s = scorecard([resultado("numeric_fidelity"), resultado("text_size")], "APROVADA")
    assert s.veredito == "APROVADA"


def test_scorecard_aprovada_com_metrica_reprovada():
    with pytest.raises(ValidationError, match="APROVADA exige todas as métricas"):
        scorecard([resultado(violacoes=[violacao()])], "APROVADA")


def test_scorecard_reprovada_precisa_de_metrica_reprovada():
    with pytest.raises(ValidationError, match="REPROVADA exige pelo menos uma métrica fora da faixa"):
        scorecard([resultado()], "REPROVADA")


def test_scorecard_reprovada():
    s = scorecard([resultado(violacoes=[violacao()]), resultado("text_size")], "REPROVADA")
    assert [m.passou for m in s.metricas] == [False, True]


def test_scorecard_erro_infra_sem_regra_sobre_as_metricas():
    assert scorecard([resultado()], "ERRO_INFRA").veredito == "ERRO_INFRA"
    assert scorecard([], "ERRO_INFRA").metricas == []


def test_scorecard_com_metrica_repetida():
    with pytest.raises(ValidationError, match="mais de uma vez"):
        scorecard([resultado(), resultado()], "APROVADA")


def test_scorecard_de_tentativa_zero():
    with pytest.raises(ValidationError, match="tentativa"):
        scorecard([resultado()], "APROVADA", tentativa=0)


# CelulaState


def celula(estado="PENDENTE", **campos):
    base = {"celula_id": "iniciante__artigo", "nivel": "iniciante", "formato": "artigo", "estado": estado}
    return CelulaState.model_validate({**base, **campos})


def test_celula_pendente_sem_peca_nem_scorecard():
    c = celula()
    assert (c.peca, c.scorecard, c.motivo_reprovacao, c.erro_classe, c.erro_mensagem) == (None, None, None, None, None)
    assert c.tentativa == 0


def test_celula_aprovada_com_peca_e_scorecard():
    c = celula("APROVADA", tentativa=1, peca=peca("artigo"), scorecard=scorecard([resultado()], "APROVADA"))
    assert c.peca.tentativa == 1 and c.scorecard.veredito == "APROVADA"


@pytest.mark.parametrize("motivo", ["k_esgotado", "nao_convergencia"])
def test_celula_reprovada_tem_motivo(motivo):
    assert celula("REPROVADA", tentativa=2, motivo_reprovacao=motivo).motivo_reprovacao == motivo


def test_celula_reprovada_sem_motivo():
    with pytest.raises(ValidationError, match="motivo_reprovacao é obrigatório em REPROVADA"):
        celula("REPROVADA")


def test_motivo_de_reprovacao_fora_de_reprovada():
    with pytest.raises(ValidationError, match="proibido nos outros estados"):
        celula("APROVADA", motivo_reprovacao="k_esgotado")


def test_motivo_de_reprovacao_desconhecido():
    with pytest.raises(ValidationError, match="motivo_reprovacao"):
        celula("REPROVADA", motivo_reprovacao="cansou")


def test_celula_erro_infra_tem_classe_e_mensagem():
    c = celula("ERRO_INFRA", erro_classe="retriavel", erro_mensagem="HTTP 503")
    assert (c.erro_classe, c.erro_mensagem) == ("retriavel", "HTTP 503")


@pytest.mark.parametrize("campos", [{}, {"erro_classe": "terminal"}, {"erro_mensagem": "x"}])
def test_celula_erro_infra_incompleta(campos):
    with pytest.raises(ValidationError, match="ERRO_INFRA exige erro_classe e erro_mensagem"):
        celula("ERRO_INFRA", **campos)


def test_erro_fora_de_erro_infra():
    with pytest.raises(ValidationError, match="só valem em ERRO_INFRA"):
        celula("GERANDO", erro_classe="terminal", erro_mensagem="x")


def test_celula_com_peca_de_outra_celula():
    with pytest.raises(ValidationError, match="Peca de iniciante__carrossel na célula iniciante__artigo"):
        celula("AVALIANDO", tentativa=1, peca=peca("carrossel"))


def test_celula_com_scorecard_de_outra_celula():
    outro = scorecard([resultado()], "APROVADA", celula_id="avancado__artigo")
    with pytest.raises(ValidationError, match="Scorecard de avancado__artigo"):
        celula("APROVADA", tentativa=1, scorecard=outro)
