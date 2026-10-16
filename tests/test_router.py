"""E6 (evaluate) e E7 (router) de uma célula e o refinement loop no subgrafo da célula, com o LLM simulado, o
repositório em memória e o plano e o FactSheet de test_sintese."""

import json

import pytest

from conftest import cliente_falso
from sunontent import persistencia
from sunontent.graph import construir_grafo_celula
from sunontent.metrics import resultado
from sunontent.nodes.router import nao_convergiu, relatorio_corretivo, router
from sunontent.retentativa import FalhaRetriavel, FalhaTerminal
from sunontent.schemas import (
    FORMATOS,
    NIVEIS,
    CelulaGrafoState,
    CelulaState,
    Chunk,
    LlmJudgeDetalhes,
    PlanContractDetalhes,
    SaidaCarrossel,
    Scorecard,
    Violacao,
    VeredictoUnidade,
    celula_id,
)
from test_sintese import FACTSHEET, PLANO, RAIZ

CELULA = "intermediario__carrossel"  # C01 a C05 no plano; de 5 a 7 slides; até 220 caracteres no corpo
LONGO = "O lucro foi de {{C04.1}}, " + "e a empresa explicou o resultado em detalhes " * 6


def carrossel(c01="O Copom reduziu a Selic para {{C01.1}}.", c04="O lucro foi de {{C04.1}}.") -> str:
    slides = [{"papel": "gancho", "texto": "Juros em queda", "claim_ids": []}]
    slides += [{"papel": "corpo", "texto": texto, "claim_ids": [c]} for c, texto in [
        ("C01", c01),
        ("C02", "O IPCA subiu {{C02.1}} no trimestre."),
        ("C03", "Segundo o Copom, a inflação deve ficar em {{C03.1}}."),
        ("C04", c04),
        ("C05", "A taxa ficou em {{C05.1}} e a meta em {{C05.2}}."),
    ]]
    slides += [{"papel": "conclusao", "texto": "Acompanhe as próximas reuniões.", "claim_ids": []}]
    return json.dumps(SaidaCarrossel.model_validate({"slides": slides}).model_dump(), ensure_ascii=False)


# Tentativa 1: número escrito à mão no slide do C01 (M1) e slide do C04 acima de 220 caracteres (M2).
REPROVADA_1 = carrossel(c01="O Copom reduziu a Selic para 13,75%.", c04=LONGO)


@pytest.fixture
def com_plano(repositorio, run_id, tmp_path):
    repositorio.factsheets[run_id] = FACTSHEET.model_dump(mode="json", exclude={"tabela_numeros"})
    # Documento processado mínimo: o juiz recebe os chunks dele.
    repositorio.documentos_processados[run_id] = {
        "doc_id": FACTSHEET.doc_id, "doc_sha256": "0" * 64, "tipo_documento": FACTSHEET.tipo_documento,
        "emissor": FACTSHEET.emissor, "data_documento": FACTSHEET.data_documento,
    }
    repositorio.paginas[(run_id, 1)] = {"texto_limpo": "Copom", "ocr": False}
    repositorio.chunks[(run_id, "K01")] = Chunk(
        chunk_id="K01", pagina_inicio=1, offset_inicio=0, pagina_fim=1, offset_fim=5, texto="Copom").model_dump()
    celulas = [CelulaState(celula_id=celula_id(n, f), nivel=n, formato=f) for n in NIVEIS for f in FORMATOS]
    persistencia.gravar_plano(repositorio, tmp_path, run_id, PLANO, celulas)
    return repositorio


def rodar(repositorio, run_id, tmp_path, respostas, juiz=None):
    llm = cliente_falso(respostas)
    configuravel = {"repositorio": repositorio, "raiz_dados": tmp_path, "raiz": RAIZ, "llm": llm}
    if juiz is not None:
        configuravel["juiz"] = juiz
    celula = CelulaState(celula_id=CELULA, nivel="intermediario", formato="carrossel")
    final = construir_grafo_celula().invoke(
        CelulaGrafoState(run_id=run_id, celula=celula), {"configurable": configuravel}
    )
    return CelulaGrafoState.model_validate(final), llm._provider


# Refinement loop


def test_correcao_acontece_na_tentativa_2(com_plano, run_id, tmp_path):
    final, provider = rodar(com_plano, run_id, tmp_path, [REPROVADA_1, carrossel()])

    assert (final.celula.estado, final.celula.tentativa, final.celula.motivo_reprovacao) == ("APROVADA", 2, None)
    assert final.relatorio is None and final.falha is None
    gravada = com_plano.celulas[(run_id, CELULA)]
    assert (gravada.estado, gravada.tentativa) == ("APROVADA", 2)

    primeiro = Scorecard.model_validate(com_plano.scorecards[(run_id, CELULA, 1)])
    segundo = Scorecard.model_validate(com_plano.scorecards[(run_id, CELULA, 2)])
    assert primeiro.veredito == "REPROVADA" and segundo.veredito == "APROVADA"
    assert [(m.nome, m.passou) for m in primeiro.metricas] == [
        ("numeric_fidelity", False), ("text_size", False), ("readability", True), ("term_density", True),
        ("analogy_rule", True), ("plan_contract", True)]
    assert primeiro.custo_tokens == 1200 and segundo.custo_tokens == 1200  # 1000 de entrada e 200 de saída

    # O retry leva a peça anterior com o número escrito à mão, o que está certo e as instruções corretivas.
    assert provider.raciocinios == [None, "low"]
    retry = provider.prompts[1]
    assert "# Correção" in retry and "O Copom reduziu a Selic para 13,75%." in retry
    assert "- analogy_rule: 0 (esperado: 0)" in retry and "- plan_contract: 0 (esperado: 0)" in retry
    assert "## slide 1\n" in retry and "Use o marcador {{C01.1}} no lugar do número 13,75%." in retry
    assert "## slide 4\n" in retry and "Reduza o slide 5 (corpo)" in retry

    pasta = tmp_path / "runs" / run_id / "celulas" / CELULA
    assert json.loads((pasta / "estado.json").read_text(encoding="utf-8"))["estado"] == "APROVADA"
    assert json.loads((pasta / "scorecard_2.json").read_text(encoding="utf-8"))["veredito"] == "APROVADA"
    assert (pasta / "scorecard_1.json").exists() and (pasta / "peca_2.json").exists()


def test_sem_melhora_na_tentativa_2_reprova_por_nao_convergencia(com_plano, run_id, tmp_path):
    final, provider = rodar(com_plano, run_id, tmp_path, [REPROVADA_1, REPROVADA_1])

    assert (final.celula.estado, final.celula.motivo_reprovacao, final.celula.tentativa) == (
        "REPROVADA", "nao_convergencia", 2)
    assert len(provider.prompts) == 2
    gravada = com_plano.celulas[(run_id, CELULA)]
    assert (gravada.estado, gravada.motivo_reprovacao) == ("REPROVADA", "nao_convergencia")
    assert final.celula.scorecard.veredito == "REPROVADA"


def test_aprovada_na_primeira_tentativa_nao_chama_o_redator_de_novo(com_plano, run_id, tmp_path):
    final, provider = rodar(com_plano, run_id, tmp_path, [carrossel()])
    assert (final.celula.estado, final.celula.tentativa) == ("APROVADA", 1)
    assert len(provider.prompts) == 1


# Falhas da E5 e da E6


def test_falha_do_redator_vira_erro_infra_sem_scorecard(com_plano, run_id, tmp_path):
    final, _ = rodar(com_plano, run_id, tmp_path, [FalhaTerminal("HTTP 400: pedido inválido")])

    assert (final.celula.estado, final.celula.tentativa, final.celula.erro_classe) == ("ERRO_INFRA", 0, "terminal")
    assert "HTTP 400" in final.celula.erro_mensagem
    gravada = com_plano.celulas[(run_id, CELULA)]
    assert (gravada.estado, gravada.erro_classe) == ("ERRO_INFRA", "terminal")
    assert not com_plano.scorecards and not com_plano.pecas


def test_falha_na_gravacao_do_scorecard_vira_erro_infra_com_a_peca_gravada(com_plano, run_id, tmp_path):
    com_plano.falhas["gravar_scorecard"] = [FalhaTerminal("gravação recusada")]
    final, _ = rodar(com_plano, run_id, tmp_path, [carrossel()])

    assert (final.celula.estado, final.celula.tentativa, final.celula.erro_classe) == ("ERRO_INFRA", 1, "terminal")
    assert "gravação recusada" in final.celula.erro_mensagem
    assert (run_id, CELULA, 1) in com_plano.pecas and not com_plano.scorecards


def test_falha_no_retry_vira_erro_infra_na_tentativa_da_ultima_peca(com_plano, run_id, tmp_path):
    final, _ = rodar(com_plano, run_id, tmp_path, [REPROVADA_1, FalhaTerminal("HTTP 400")])
    assert (final.celula.estado, final.celula.tentativa) == ("ERRO_INFRA", 1)


def test_excecao_que_nao_e_falha_sobe(com_plano, run_id, tmp_path):
    with pytest.raises(IndexError):
        rodar(com_plano, run_id, tmp_path, [])  # o provider falso sem resposta é bug do teste, não Falha


def test_sem_plano_e_erro_infra(repositorio, run_id, tmp_path):
    repositorio.celulas[(run_id, CELULA)] = CelulaState(celula_id=CELULA, nivel="intermediario", formato="carrossel")
    final, _ = rodar(repositorio, run_id, tmp_path, [])
    assert (final.celula.estado, final.celula.erro_classe) == ("ERRO_INFRA", "terminal")
    assert "plano editorial" in final.celula.erro_mensagem


# Juiz (M6), quando o config traz um


def m6(*implicadas: bool):
    unidades = [VeredictoUnidade(unidade_indice=i, justificativa="ok", implicada=ok) for i, ok in enumerate(implicadas)]
    violacoes = [Violacao(metrica="llm_judge", trecho="slide", esperado="implicada", obtido="não implicada",
                          instrucao_corretiva="Tire a causa que o claim não diz.", unidade_indice=u.unidade_indice)
                 for u in unidades if not u.implicada]
    return resultado.montar(LlmJudgeDetalhes(analogias=[], unidades=unidades, justificativa_nivel="j",
                                             nivel_percebido="intermediario"),
                            violacoes)


def test_juiz_so_roda_quando_as_deterministicas_passam_e_pode_reprovar(com_plano, run_id, tmp_path):
    chamadas = []

    def juiz(peca, plano_nivel, factsheet, chunks, glossario, *, run_id, registrar_span):
        assert [c.chunk_id for c in chunks] == ["K01"]
        chamadas.append(peca.tentativa)
        return m6(True, False) if peca.tentativa == 1 else m6(True, True)

    final, provider = rodar(com_plano, run_id, tmp_path, [carrossel(), carrossel(c04="O resultado foi de {{C04.1}}.")],
                            juiz)
    assert chamadas == [1, 2] and final.celula.estado == "APROVADA"
    primeiro = Scorecard.model_validate(com_plano.scorecards[(run_id, CELULA, 1)])
    assert [m.nome for m in primeiro.metricas][-1] == "llm_judge" and primeiro.veredito == "REPROVADA"
    assert "Tire a causa que o claim não diz." in provider.prompts[1]


def test_juiz_nao_roda_com_deterministica_reprovada(com_plano, run_id, tmp_path):
    chamadas = []
    juiz = lambda peca, *a, **k: chamadas.append(peca.tentativa) or m6(True)  # noqa: E731
    final, _ = rodar(com_plano, run_id, tmp_path, [REPROVADA_1, carrossel()], juiz)
    assert chamadas == [2] and final.celula.estado == "APROVADA"


def test_falha_do_juiz_grava_scorecard_erro_infra(com_plano, run_id, tmp_path):
    def juiz(*a, **k):
        raise FalhaRetriavel("HTTP 503: nenhum provedor")

    final, _ = rodar(com_plano, run_id, tmp_path, [carrossel()], juiz)
    assert (final.celula.estado, final.celula.erro_classe) == ("ERRO_INFRA", "retriavel")
    gravado = Scorecard.model_validate(com_plano.scorecards[(run_id, CELULA, 1)])
    assert gravado.veredito == "ERRO_INFRA" and len(gravado.metricas) == 6


# Router: relatório corretivo e não convergência


def violacao(metrica, n):
    return Violacao(metrica=metrica, trecho=f"t{n}", esperado="e", obtido="o", instrucao_corretiva=f"faça {n}")


def contrato(n):
    return resultado.montar(PlanContractDetalhes(checagens=[]), [violacao("plan_contract", i) for i in range(n)])


def scorecard(tentativa, *metricas):
    veredito = "APROVADA" if all(m.passou for m in metricas) else "REPROVADA"
    return Scorecard(celula_id=CELULA, tentativa=tentativa, metricas=list(metricas), veredito=veredito,
                     custo_tokens=0, duracao_ms=0)


def test_relatorio_limita_5_violacoes_por_metrica_e_conta_o_resto():
    rel = relatorio_corretivo(scorecard(1, contrato(7)))
    assert [v.trecho for v in rel.violacoes] == ["t0", "t1", "t2", "t3", "t4"]
    assert rel.restantes == {"plan_contract": 2} and rel.certos == []
    rel = relatorio_corretivo(scorecard(1, contrato(0)))
    assert rel.certos == ["plan_contract: 0 (esperado: 0)"] and not rel.violacoes and not rel.restantes


@pytest.mark.parametrize("antes, agora, esperado", [
    (3, 3, True),   # mesma métrica, mesma contagem
    (3, 4, True),   # piorou
    (3, 2, False),  # diminuiu
    (3, 0, False),  # passou
])
def test_nao_convergencia(antes, agora, esperado):
    assert nao_convergiu(scorecard(2, contrato(agora)), scorecard(1, contrato(antes))) is esperado


def estado_avaliado(tentativa, sc):
    celula = CelulaState(celula_id=CELULA, nivel="intermediario", formato="carrossel", estado="AVALIANDO",
                         tentativa=tentativa, scorecard=sc)
    return CelulaGrafoState(run_id="copom_20261004T142205Z_9f2c1a", celula=celula)


def test_regra_5_so_age_com_k_maior_ou_igual_a_3(com_plano, run_id, tmp_path):
    configuracao = {"configurable": {"repositorio": com_plano, "raiz_dados": tmp_path}}
    anterior = scorecard(1, contrato(2))
    leituras = []
    com_plano.carregar_scorecard = lambda r, c, t: leituras.append(t) or anterior

    # K = 2: a tentativa 2 que não melhorou é reprovada por K esgotado, sem ler a anterior.
    saida = router(estado_avaliado(2, scorecard(2, contrato(2))), configuracao, K=2)
    assert (saida["celula"].estado, saida["celula"].motivo_reprovacao) == ("REPROVADA", "k_esgotado")
    assert leituras == []

    # K = 3: a mesma tentativa encerra a célula por não convergência.
    saida = router(estado_avaliado(2, scorecard(2, contrato(2))), configuracao, K=3)
    assert (saida["celula"].estado, saida["celula"].motivo_reprovacao) == ("REPROVADA", "nao_convergencia")
    assert leituras == [1]

    # K = 3 e a tentativa melhorou: volta à E5 com o relatório, sem gravar estado.
    gravacoes = com_plano.chamadas.get("atualizar_celula", 0)
    saida = router(estado_avaliado(2, scorecard(2, contrato(1))), configuracao, K=3)
    assert "celula" not in saida and saida["relatorio"].violacoes[0].trecho == "t0"
    assert com_plano.chamadas.get("atualizar_celula", 0) == gravacoes


def test_k_esgotado_na_tentativa_3_mesmo_com_melhora(com_plano, run_id, tmp_path):
    configuracao = {"configurable": {"repositorio": com_plano, "raiz_dados": tmp_path}}
    com_plano.carregar_scorecard = lambda r, c, t: scorecard(2, contrato(2))

    saida = router(estado_avaliado(3, scorecard(3, contrato(1))), configuracao, K=3)
    assert (saida["celula"].estado, saida["celula"].motivo_reprovacao) == ("REPROVADA", "k_esgotado")


def test_router_sem_scorecard_da_tentativa_e_bug(com_plano, tmp_path):
    configuracao = {"configurable": {"repositorio": com_plano, "raiz_dados": tmp_path}}
    with pytest.raises(ValueError, match="sem o scorecard"):
        router(estado_avaliado(2, scorecard(1, contrato(1))), configuracao, K=2)
