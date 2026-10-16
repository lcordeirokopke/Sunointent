"""Quarentena do loop E2/E3 e a revisão humana: domínio sobre o documento de referência do Copom e o
caminho completo (grafo, quarentena, revisão) com o repositório em memória."""

import json
from decimal import Decimal

import pytest

from sunontent import persistencia
from sunontent.extracao import leitura, quarentena
from sunontent.extracao.ancoragem import NaoResolvido
from sunontent.nodes import leitura as no_leitura
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import ClaimProposto, FactSheet
from test_extractor import documento_fonte
from test_main_graph import DECISAO, ERRADO, VOTACAO, raiz, repo, rodar  # noqa: F401 (fixtures)
from conftest import cliente_falso, resposta_plano

pytestmark = pytest.mark.usefixtures("celulas_aprovadas")  # as 9 células aprovadas sem LLM

SELIC = "13,75%"


@pytest.fixture(scope="module")
def copom():
    return leitura.processar(documento_fonte("copom"), "copom")


def sem_selic(documento):
    """O documento como se a E1 não tivesse lido o 13,75%."""
    return documento.model_copy(update={"tabela_numeros": [n for n in documento.tabela_numeros if n.bruto != SELIC]})


def proposto(dados) -> ClaimProposto:
    return ClaimProposto(**dados)


# Domínio


def test_separar_assina_os_validos_e_poe_os_reprovados_em_quarentena(copom):
    validos = quarentena.preparar(copom, proposto(DECISAO))
    claim, _ = quarentena.aceitar(copom, proposto(DECISAO), [s.numero for s in validos.numeros])
    factsheet = FactSheet(
        doc_id=copom.doc_id, tipo_documento=copom.tipo_documento, emissor=copom.emissor,
        data_documento=copom.data_documento, claims=[claim], tabela_numeros=copom.tabela_numeros,
    )
    propostas = {"C01": proposto(DECISAO), "C02": proposto(VOTACAO)}
    assinado, itens = quarentena.separar(factsheet, propostas, {"C02": "motivo"}, 3)
    assert assinado.assinado and [c.id for c in assinado.claims] == ["C01"]
    assert [(i.proposto.id, i.motivo, i.rodada) for i in itens] == [("C02", "motivo", 3)]


def test_numero_que_a_e1_nao_leu_e_interpretado_para_confirmar(copom):
    documento = sem_selic(copom)
    preparo = quarentena.preparar(documento, proposto(DECISAO))
    (sugerido,) = preparo.numeros
    assert sugerido.novo and sugerido.numero.valor == Decimal("13.75") and sugerido.numero.unidade == "pct"
    pagina = documento.paginas[preparo.ancora.pagina - 1].texto_limpo
    assert pagina[sugerido.ancora.offset_inicio : sugerido.ancora.offset_fim] == SELIC
    claim, novos = quarentena.aceitar(documento, proposto(DECISAO), [sugerido.numero])
    assert novos == [sugerido.numero] and claim.numeros == [sugerido.numero]
    assert claim.literal_fonte == preparo.literal_fonte


def test_numero_ja_lido_pela_e1_e_reaproveitado(copom):
    (sugerido,) = quarentena.preparar(copom, proposto(DECISAO)).numeros
    assert not sugerido.novo and sugerido.numero in copom.tabela_numeros
    _, novos = quarentena.aceitar(copom, proposto(DECISAO), [sugerido.numero])
    assert novos == []


def test_formato_nao_interpretado_pede_valor_e_unidade(copom):
    documento = sem_selic(copom)
    (sugerido,) = quarentena.preparar(documento, proposto(DECISAO), brutos=["13,75% a.a."]).numeros
    assert sugerido.numero is None and sugerido.novo and sugerido.motivo
    numero = quarentena.numero_informado(sugerido, Decimal("13.75"), "pct")
    claim, novos = quarentena.aceitar(documento, proposto(DECISAO), [numero])
    assert novos == [numero] and numero.bruto == "13,75% a.a."


def test_trecho_corrigido_ancora_de_novo(copom):
    errado = proposto({**DECISAO, "literal_fonte": "O Copom cortou a Selic para 13,75%"})
    with pytest.raises(NaoResolvido, match="não foi encontrado"):
        quarentena.preparar(copom, errado)
    preparo = quarentena.preparar(copom, errado, literal_fonte=DECISAO["literal_fonte"])
    claim, _ = quarentena.aceitar(copom, errado, [s.numero for s in preparo.numeros],
                                  literal_fonte=DECISAO["literal_fonte"])
    assert claim.literal_fonte == DECISAO["literal_fonte"]


def test_aceitar_recusa_numero_fora_do_trecho_bruto_trocado_e_sobreposicao(copom):
    documento = sem_selic(copom)
    (sugerido,) = quarentena.preparar(documento, proposto(DECISAO)).numeros
    a = sugerido.ancora
    fora = sugerido.numero.model_copy(update={"ancora": a.model_copy(update={"pagina": a.pagina + 1})})
    with pytest.raises(NaoResolvido, match="fora do trecho"):
        quarentena.aceitar(documento, proposto(DECISAO), [fora])
    trocado = sugerido.numero.model_copy(update={"bruto": "13,7%"})
    with pytest.raises(NaoResolvido, match="não confere"):
        quarentena.aceitar(documento, proposto(DECISAO), [trocado])
    # No documento completo, a E1 leu 13,75% nessa posição: o humano não registra outra leitura.
    com_outro_valor = sugerido.numero.model_copy(update={"valor": Decimal("1375")})
    with pytest.raises(NaoResolvido, match="a E1 já leu"):
        quarentena.aceitar(copom, proposto(DECISAO), [com_outro_valor])


def test_aceitar_passa_pelas_regras_da_e3(copom):
    with pytest.raises(NaoResolvido, match="não estão na lista"):
        quarentena.aceitar(copom, proposto(DECISAO), [])


def test_bruto_fora_do_trecho_nao_prepara(copom):
    with pytest.raises(NaoResolvido, match="não está dentro do trecho"):
        quarentena.preparar(sem_selic(copom), proposto(DECISAO), brutos=["4,9%"])


# Grafo e revisão


@pytest.fixture
def e1_sem_selic(monkeypatch):
    original = no_leitura.extracao.processar
    monkeypatch.setattr(no_leitura.extracao, "processar", lambda fonte, nome: sem_selic(original(fonte, nome)))


def rodar_com_quarentena(raiz, repo, primeiro, correcao):
    resposta = json.dumps({"claims": [primeiro, VOTACAO]}, ensure_ascii=False)
    correcao = json.dumps({"claims": [correcao]}, ensure_ascii=False)
    assert rodar(raiz, repo, dev=True, llm=cliente_falso([resposta, correcao, correcao, resposta_plano("C02")])) == 0
    (run_id,) = repo.runs
    assert (run_id, "C01") in repo.quarentena
    return run_id


def test_numero_que_a_e1_nao_leu_vai_para_a_quarentena_e_o_humano_aceita(raiz, repo, e1_sem_selic):
    run_id = rodar_com_quarentena(raiz, repo, DECISAO, DECISAO)
    assert "13,75%" in repo.quarentena[(run_id, "C01")].motivo
    raiz_dados = raiz / "data"
    preparo = quarentena.preparar_revisao(repo, run_id, "C01")
    (sugerido,) = preparo.numeros
    assert sugerido.novo and sugerido.numero.unidade == "pct"
    claim = quarentena.aceitar_da_quarentena(repo, raiz_dados, run_id, "C01", [sugerido.numero], autor=" Ana ")
    assert repo.claims_humanos[(run_id, "C01")] == claim
    assert repo.decisoes_quarentena[(run_id, "C01")] == {"decisao": "aceitar", "autor": "Ana"}
    a = sugerido.ancora
    assert repo.numeros[(run_id, a.pagina, a.offset_inicio, a.offset_fim)]["origem"] == "humano"
    local = json.loads((raiz_dados / "runs" / run_id / "revisoes" / "C01.json").read_text(encoding="utf-8"))
    assert local["decisao"] == "aceitar" and local["claim"]["id"] == "C01"
    assert local["numeros_novos"][0]["bruto"] == SELIC
    # A revisão seguinte já lê o número humano como parte da tabela.
    assert sugerido.numero in persistencia.carregar_documento_processado(repo, run_id).tabela_numeros
    with pytest.raises(FalhaTerminal, match="já foi revisado"):
        quarentena.descartar_da_quarentena(repo, raiz_dados, run_id, "C01", autor="Ana")



def test_humano_corrige_o_trecho_de_claim_sem_ancora(raiz, repo):
    sem_ancora = {**DECISAO, "literal_fonte": "O Copom cortou a Selic para 13,75%"}
    run_id = rodar_com_quarentena(raiz, repo, sem_ancora, sem_ancora)
    assert "não foi encontrado" in repo.quarentena[(run_id, "C01")].motivo
    correto = DECISAO["literal_fonte"]
    preparo = quarentena.preparar_revisao(repo, run_id, "C01", literal_fonte=correto)
    claim = quarentena.aceitar_da_quarentena(
        repo, raiz / "data", run_id, "C01", [s.numero for s in preparo.numeros], autor="Ana", literal_fonte=correto
    )
    assert claim.literal_fonte == correto and not any(n.get("origem") == "humano" for n in repo.numeros.values())


def test_descartar_registra_a_decisao_sem_claim(raiz, repo):
    run_id = rodar_com_quarentena(raiz, repo, ERRADO, ERRADO)
    quarentena.descartar_da_quarentena(repo, raiz / "data", run_id, "C01", autor="Ana")
    assert repo.decisoes_quarentena[(run_id, "C01")]["decisao"] == "descartar"
    assert not repo.claims_humanos
    assert [i.decisao for i in persistencia.carregar_quarentena(repo, run_id)] == ["descartar"]
    with pytest.raises(FalhaTerminal, match="já foi revisado"):
        quarentena.preparar_revisao(repo, run_id, "C01")


def test_revisao_exige_autor_e_claim_em_quarentena(raiz, repo):
    run_id = rodar_com_quarentena(raiz, repo, ERRADO, ERRADO)
    with pytest.raises(FalhaTerminal, match="nome de quem revisou"):
        quarentena.descartar_da_quarentena(repo, raiz / "data", run_id, "C01", autor="  ")
    with pytest.raises(FalhaTerminal, match="não está na quarentena"):
        quarentena.descartar_da_quarentena(repo, raiz / "data", run_id, "C02", autor="Ana")
    assert not repo.decisoes_quarentena


def test_aceitar_que_nao_passa_na_e3_nao_grava_nada(raiz, repo):
    run_id = rodar_com_quarentena(raiz, repo, ERRADO, ERRADO)
    with pytest.raises(NaoResolvido):
        quarentena.aceitar_da_quarentena(repo, raiz / "data", run_id, "C01", [], autor="Ana")
    assert not repo.decisoes_quarentena and not repo.claims_humanos


def test_run_abortado_nao_aceita_claim_da_quarentena(raiz, repo):
    errado = json.dumps({"claims": [ERRADO]}, ensure_ascii=False)
    assert rodar(raiz, repo, dev=True, llm=cliente_falso([errado] * 3)) == 1
    (run_id,) = repo.runs
    correto = DECISAO["literal_fonte"]
    preparo = quarentena.preparar_revisao(repo, run_id, "C01", literal_fonte=correto)
    with pytest.raises(FalhaTerminal, match="não tem FactSheet assinado"):
        quarentena.aceitar_da_quarentena(
            repo, raiz / "data", run_id, "C01", [s.numero for s in preparo.numeros], autor="Ana", literal_fonte=correto
        )
    assert not repo.decisoes_quarentena
