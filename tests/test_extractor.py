"""E2 (extractor e ancoragem), E3 (validador) e o cliente LLM.

Os testes comuns usam o ProviderFalso (conftest.py): sem rede e sem custo. A ancoragem e a
validação rodam sobre a saída real da E1 dos documentos de tests/fixtures/referencia/.

O teste de cobertura com a Gemini API de verdade tem o marker `gemini` e só roda com
`pytest -m gemini`; ele lê só a GOOGLE_API_KEY do .env.
"""

import json
import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel
from langgraph.checkpoint.memory import InMemorySaver

from conftest import FIXTURES, ProviderFalso, RepositorioMemoria, cliente_falso
from sunontent import __main__ as principal
from sunontent import llm, persistencia
from sunontent.extracao import ancoragem, extractor, leitura, validador
from sunontent.llm import ClienteLLM, Preco, RespostaBruta
from sunontent.retentativa import FalhaRetriavel, FalhaTerminal
from sunontent.schemas import Ancora, Claim, ClaimProposto, DocumentoFonte, FactSheet, Numero

RAIZ = Path(__file__).resolve().parent.parent
REFERENCIA = FIXTURES / "referencia"
FONTES = ["copom", "cvm", "b3"]


def documento_fonte(fonte: str) -> DocumentoFonte:
    meta = json.loads((REFERENCIA / f"{fonte}.json").read_text(encoding="utf-8"))
    return DocumentoFonte(caminho=REFERENCIA / f"{fonte}.pdf", **meta)


@pytest.fixture(scope="module")
def documentos():
    return {fonte: leitura.processar(documento_fonte(fonte), fonte) for fonte in FONTES}


def proposto(literal, numeros=(), chunk_id="K19", claim_id="C01", tipo="decisao", tags=("indicador_chave",),
             texto="Um fato."):
    return ClaimProposto(
        id=claim_id, chunk_id=chunk_id, texto=texto, literal_fonte=literal,
        numeros=list(numeros), tipo=tipo, tags=list(tags),
    )


def factsheet_de(documento, claims) -> FactSheet:
    return FactSheet(
        doc_id=documento.doc_id, tipo_documento=documento.tipo_documento, emissor=documento.emissor,
        data_documento=documento.data_documento, claims=claims, tabela_numeros=documento.tabela_numeros,
    )


def anotacao(fonte: str) -> dict:
    return yaml.safe_load((REFERENCIA / f"{fonte}.anotacao.yaml").read_text(encoding="utf-8"))


def _falta_no_trecho(documento, factsheet: FactSheet, alternativa: dict) -> str | None:
    """None se algum claim cruza o trecho e os claims que cruzam trazem todos os números listados."""
    alvo = ancoragem.localizar(documento, alternativa["trecho"])
    cruzam = [
        c for c in factsheet.claims
        if c.ancora.pagina == alvo.pagina
        and c.ancora.offset_inicio < alvo.offset_fim
        and alvo.offset_inicio < c.ancora.offset_fim
    ]
    if not cruzam:
        return "nenhum claim"
    brutos = {"".join(n.bruto.split()) for c in cruzam for n in c.numeros}
    ausentes = [b for b in alternativa["numeros"] if "".join(b.split()) not in brutos]
    return f"sem os números {ausentes}" if ausentes else None


def fatos_sem_cobertura(documento, factsheet: FactSheet, fatos: list[dict]) -> dict[str, str]:
    """Fato coberto: alguma das alternativas de trecho está coberta. Devolve o motivo de cada fato faltante."""
    faltando = {}
    for fato in fatos:
        motivos = [_falta_no_trecho(documento, factsheet, alternativa) for alternativa in fato["trechos"]]
        if None not in motivos:
            faltando[fato["id"]] = f"{' | '.join(motivos)}: {fato['descricao']}"
    return faltando


# Anotação


@pytest.mark.parametrize("fonte", FONTES)
def test_anotacao_bate_com_o_documento(documentos, fonte):
    """Cada trecho anotado existe uma única vez numa página, com os números listados dentro dele."""
    documento = documentos[fonte]
    dados = anotacao(fonte)
    assert dados["doc_sha256"] == documento.doc_sha256
    ids = [f["id"] for f in dados["fatos"]]
    assert len(ids) == len(set(ids))
    for fato in dados["fatos"]:
        assert fato["trechos"], fato["id"]
        for alternativa in fato["trechos"]:
            alvo = ancoragem.localizar(documento, alternativa["trecho"])
            ancoragem.numeros_no_trecho(documento, alvo, alternativa["numeros"])


def test_cobertura_acusa_fato_e_numero_ausentes(documentos):
    documento = documentos["copom"]
    fatos = anotacao("copom")["fatos"]
    so_decisao = extractor.ancorar(documento, {"C01": proposto(
        "O Copom decidiu reduzir a taxa básica de juros para 13,75% a.a.", ["13,75%"])})[0]
    faltando = fatos_sem_cobertura(documento, factsheet_de(documento, so_decisao), fatos)
    assert "F01" not in faltando and "F09" in faltando
    sem_numero = extractor.ancorar(documento, {"C01": proposto(
        "O Copom decidiu reduzir a taxa básica de juros para 13,75% a.a.")})[0]
    assert "números" in fatos_sem_cobertura(documento, factsheet_de(documento, sem_numero), fatos)["F01"]


# Ancoragem (E2)


def test_literal_com_espaco_no_lugar_da_quebra_de_linha_resolve_no_texto_exato(documentos):
    documento = documentos["copom"]
    claim = ancoragem.resolver(documento, proposto(
        "O Copom decidiu reduzir a taxa básica de juros para 13,75% a.a., e entende que essa decisão "
        "é compatível", ["13,75%"]))
    pagina = documento.paginas[claim.ancora.pagina - 1].texto_limpo
    assert claim.literal_fonte == pagina[claim.ancora.offset_inicio : claim.ancora.offset_fim]
    assert "\n" in claim.literal_fonte  # o texto do documento, não o do modelo
    (numero,) = claim.numeros
    assert numero in documento.tabela_numeros and numero.valor == Decimal("13.75") and numero.unidade == "pct"


def test_aspas_e_travessoes_trocados_pelo_modelo_resolvem(documentos):
    documento = documentos["cvm"]
    claim = ancoragem.resolver(documento, proposto(
        'A Nova Transportadora do Sudeste S.A. - NTS, sociedade por ações', chunk_id="K01"))
    assert "–" in claim.literal_fonte


def test_parafrase_nao_resolve(documentos):
    with pytest.raises(ancoragem.NaoResolvido, match="não foi encontrado"):
        ancoragem.resolver(documentos["copom"], proposto("O Copom cortou a Selic para 13,75% ao ano."))


def test_literal_que_atravessa_pagina_e_recusado(documentos):
    documento = documentos["copom"]
    fim_p4 = documento.paginas[3].texto_limpo.split()[-3:]
    inicio_p5 = documento.paginas[4].texto_limpo.split()[:3]
    with pytest.raises(ancoragem.NaoResolvido, match="atravessa"):
        ancoragem.localizar(documento, " ".join(fim_p4 + inicio_p5))


def test_trecho_repetido_prefere_o_chunk_citado_e_fora_dele_e_ambiguo(documentos):
    documento = documentos["b3"]
    trecho = "queda de 8,3% em relação ao 2T25"
    no_chunk = ancoragem.localizar(documento, trecho, "K05")
    assert no_chunk.pagina == 3
    with pytest.raises(ancoragem.NaoResolvido, match="aparece 2 vezes"):
        ancoragem.localizar(documento, trecho, "K99")


def test_numero_fora_do_literal_e_recusado_com_os_numeros_do_trecho(documentos):
    with pytest.raises(ancoragem.NaoResolvido, match=r"'4,9%'.*13,75%"):
        ancoragem.resolver(documentos["copom"], proposto(
            "O Copom decidiu reduzir a taxa básica de juros para 13,75% a.a.", ["4,9%"]))


def test_numero_repetido_no_trecho_usa_cada_ocorrencia_uma_vez(documentos):
    documento = documentos["copom"]
    claim = ancoragem.resolver(documento, proposto(
        "são, respectivamente, 5,2%, 3,9%, e 3,2%", ["5,2%", "3,9%", "3,2%"], chunk_id="K13"))
    assert [n.bruto for n in claim.numeros] == ["5,2%", "3,9%", "3,2%"]
    with pytest.raises(ancoragem.NaoResolvido):
        ancoragem.resolver(documento, proposto("situando-se em 4,9% e 4,3%", ["4,9%", "4,9%"], chunk_id="K04"))


def test_ancorar_separa_pendencias(documentos):
    documento = documentos["copom"]
    claims, pendencias = extractor.ancorar(documento, {
        "C01": proposto("O Copom decidiu reduzir a taxa básica de juros para 13,75% a.a.", ["13,75%"]),
        "C02": proposto("texto inventado", claim_id="C02"),
    })
    assert [c.id for c in claims] == ["C01"]
    assert list(pendencias) == ["C02"]


# Validador (E3)


def claim_valido(documento) -> Claim:
    return ancoragem.resolver(documento, proposto(
        "O Copom decidiu reduzir a taxa básica de juros para 13,75% a.a.", ["13,75%"],
        texto="O Copom reduziu a Selic para 13,75% a.a. em setembro de 2026."))


def test_claim_resolvido_passa_na_validacao(documentos):
    documento = documentos["copom"]
    assert validador.validar(documento, factsheet_de(documento, [claim_valido(documento)])) == {}


def test_validador_recusa_ancora_deslocada(documentos):
    documento = documentos["copom"]
    claim = claim_valido(documento)
    a = claim.ancora
    deslocado = claim.model_copy(update={"ancora": Ancora(
        pagina=a.pagina, offset_inicio=a.offset_inicio + 1, offset_fim=a.offset_fim + 1)})
    assert "literal_fonte" in validador.validar(documento, factsheet_de(documento, [deslocado]))["C01"]
    outra_pagina = claim.model_copy(update={"ancora": Ancora(pagina=99, offset_inicio=0, offset_fim=1)})
    assert "não existe" in validador.validar(documento, factsheet_de(documento, [outra_pagina]))["C01"]


def test_validador_recusa_pp_no_lugar_de_pct(documentos):
    documento = documentos["copom"]
    claim = claim_valido(documento)
    (numero,) = claim.numeros
    trocado = claim.model_copy(update={"numeros": [numero.model_copy(update={"unidade": "pp"})]})
    motivo = validador.validar(documento, factsheet_de(documento, [trocado]))["C01"]
    assert "pp" in motivo and "pct" in motivo


def test_validador_recusa_numero_de_outro_trecho_e_id_repetido(documentos):
    documento = documentos["copom"]
    claim = claim_valido(documento)
    outro = next(n for n in documento.tabela_numeros if n.bruto == "4,9%")
    fora = claim.model_copy(update={"numeros": [outro]})
    assert "fora do literal_fonte" in validador.validar(documento, factsheet_de(documento, [fora]))["C01"]
    inventado = claim.model_copy(update={"numeros": [Numero(
        valor=Decimal("14"), unidade="pct", bruto="14%", ancora=claim.ancora)]})
    assert "tabela_numeros" in validador.validar(documento, factsheet_de(documento, [inventado]))["C01"]
    assert validador.validar(documento, factsheet_de(documento, [claim, claim])) == {"C01": "id repetido no FactSheet"}


def test_validador_exige_que_texto_e_numeros_digam_o_mesmo(documentos):
    documento = documentos["b3"]
    literal = "Receita de R$3,1 bilhões (+12% vs. 2T25), refletindo os crescimentos de 8% das receitas pró-cíclicas"

    def motivo(texto, brutos):
        claim = ancoragem.resolver(documento, proposto(literal, brutos, chunk_id="K02", texto=texto))
        return validador.validar(documento, factsheet_de(documento, [claim])).get("C01")

    assert motivo("A receita da B3 foi de R$ 3,1 bilhões no 2T26, alta de 12%.", ["R$3,1 bilhões", "12%"]) is None
    assert "não menciona: 8%" in motivo("A receita foi de R$ 3,1 bilhões, alta de 12%.", ["R$3,1 bilhões", "12%", "8%"])
    assert "não estão na lista: 12%" in motivo("A receita foi de R$ 3,1 bilhões, alta de 12%.", ["R$3,1 bilhões"])
    assert "não estão na lista: 15%" in motivo(
        "A receita foi de R$ 3,1 bilhões, alta de 15%.", ["R$3,1 bilhões"])  # número inventado no texto
    assert "formato não previsto" in motivo("A receita foi de 3.1 bi, alta de 12.5%.", [])


# Extractor (E2)


def test_prompt_traz_o_documento_por_pagina_sem_o_cabecalho_de_versao(documentos):
    prompt = extractor.montar_prompt(RAIZ, documentos["copom"])
    assert "versao" not in prompt.splitlines()[0]
    assert "### Bloco K19 | seção: D) Decisão de política monetária" in prompt
    assert "[página 5]" in prompt and "13,75% a.a." in prompt
    assert "<documento>" in prompt and "# Correção" not in prompt


def test_bloco_que_atravessa_pagina_e_separado_por_pagina(documentos):
    (bloco,) = [b for b in extractor.blocos(documentos["copom"]) if b["chunk_id"] == "K09"]
    assert [p["pagina"] for p in bloco["partes"]] == [3, 4]


def test_extrair_renumera_os_ids_na_ordem(documentos):
    resposta = json.dumps({"claims": [
        proposto("x", claim_id="C07").model_dump(), proposto("y", claim_id="C07").model_dump()]})
    propostas = extractor.extrair(
        cliente_falso([resposta]), RAIZ, documentos["copom"], run_id="r", registrar_span=lambda s: None)
    assert list(propostas) == ["C01", "C02"] and propostas["C02"].literal_fonte == "y"


def test_corrigir_troca_so_os_claims_pedidos(documentos):
    propostas = {"C01": proposto("a"), "C02": proposto("b", claim_id="C02")}
    resposta = json.dumps({"claims": [
        proposto("a corrigido").model_dump(), proposto("intruso", claim_id="C09").model_dump()]})
    llm_falso = cliente_falso([resposta])
    novas = extractor.corrigir(
        llm_falso, RAIZ, documentos["copom"], propostas, {"C01": "motivo X"},
        run_id="r", registrar_span=lambda s: None)
    assert novas == {"C01": proposto("a corrigido"), "C02": propostas["C02"]}
    prompt = llm_falso._provider.prompts[0]
    assert "## C01" in prompt and "motivo X" in prompt and "## C02" not in prompt
    assert llm_falso._provider.raciocinios == ["low"]


def blocos_do_prompt(prompt):
    return [linha.split()[2] for linha in prompt.splitlines() if linha.startswith("### Bloco ")]


def test_correcao_mostra_so_os_chunks_em_volta_do_claim_recusado(documentos):
    doc = documentos["copom"]
    decisao = "O Copom decidiu reduzir a taxa básica de juros para 13,75% a.a."
    pedido = [(proposto("trecho inventado", chunk_id="K19"), "motivo")]
    assert extractor.chunks_da_correcao(doc, pedido) == ["K18", "K19", "K20"]
    # Citou o chunk errado, mas o trecho existe uma vez no documento: entram os dois lugares.
    pedido = [(proposto(decisao, chunk_id="K03"), "motivo")]
    assert extractor.chunks_da_correcao(doc, pedido) == ["K02", "K03", "K04", "K18", "K19", "K20"]
    # Chunk que não existe: o documento inteiro.
    assert extractor.chunks_da_correcao(doc, [(proposto("x", chunk_id="K99"), "motivo")]) is None
    prompt = extractor.montar_prompt(RAIZ, doc, pedido, ["K18", "K19", "K20"])
    assert blocos_do_prompt(prompt) == ["K18", "K19", "K20"]
    assert len(blocos_do_prompt(extractor.montar_prompt(RAIZ, doc))) == len(doc.chunks)


def test_extracao_usa_o_raciocinio_padrao_do_modelo(documentos):
    llm_falso = cliente_falso([json.dumps({"claims": [proposto("x").model_dump()]})])
    extractor.extrair(llm_falso, RAIZ, documentos["copom"], run_id="r", registrar_span=lambda s: None)
    assert llm_falso._provider.raciocinios == [None]


# Cliente LLM


def novo_cliente(respostas, modelos=None):
    return ClienteLLM(
        ProviderFalso(respostas), modelos or {"extractor": "m"}, {"m": Preco(Decimal("0.75"), Decimal("3.75"))},
        Decimal("5.40"),
    )


def chamar(cliente, spans):
    return cliente.chamar("extractor", "prompt", RespostaMinima, run_id="r", registrar_span=spans.append)


class RespostaMinima(BaseModel):
    ok: bool


def test_custo_em_brl_pelo_preco_e_pela_cotacao():
    cliente = novo_cliente(['{"ok": true}'])
    spans = []
    assert chamar(cliente, spans).ok
    (span,) = spans
    # (1000 * 0,75 + 200 * 3,75) / 1e6 US$ * 5,40
    assert span.custo_brl == Decimal("0.008100")
    assert (span.tokens_entrada, span.tokens_saida, span.falha_classe) == (1000, 200, None)
    assert cliente.custo_total_tokens == 1200 and cliente.custo_total_brl == Decimal("0.008100")


def test_resposta_fora_do_schema_reinjeta_o_erro_e_conta_os_tokens():
    cliente = novo_cliente(['{"ok": "talvez"}', '{"ok": true}'])
    spans = []
    assert chamar(cliente, spans).ok
    assert [s.falha_classe for s in spans] == ["retriavel", None]
    assert "fora do schema" in spans[0].falha_mensagem
    segundo = cliente._provider.prompts[1]
    assert segundo.startswith("prompt") and "ok:" in segundo
    assert cliente.custo_total_tokens == 2400


def test_tres_respostas_fora_do_schema_viram_terminal():
    cliente = novo_cliente(["não é json"] * 3)
    spans = []
    with pytest.raises(FalhaTerminal, match="3 respostas fora do schema"):
        chamar(cliente, spans)
    assert len(spans) == 3


def test_falha_de_infra_repete_com_espera_sem_gastar_o_contador_de_parse(sem_espera):
    cliente = novo_cliente([FalhaRetriavel("HTTP 429"), FalhaRetriavel("HTTP 503"), "x", "x", '{"ok": true}'])
    spans = []
    assert chamar(cliente, spans).ok
    assert len(sem_espera) == 2 and sem_espera[1] > sem_espera[0]
    assert [s.tokens_entrada for s in spans[:2]] == [0, 0]
    assert [s.falha_classe for s in spans] == ["retriavel"] * 4 + [None]


def test_falha_de_infra_esgotada_vira_terminal():
    cliente = novo_cliente([FalhaRetriavel("HTTP 503")] * 4)
    spans = []
    with pytest.raises(FalhaTerminal, match="4 tentativas esgotadas"):
        chamar(cliente, spans)
    assert len(spans) == 4


def test_bloqueio_e_erro_4xx_sao_terminais_sem_repetir():
    spans = []
    with pytest.raises(FalhaTerminal, match="SAFETY"):
        chamar(novo_cliente([RespostaBruta("", 10, 0, "finish_reason SAFETY")]), spans)
    with pytest.raises(FalhaTerminal, match="HTTP 400"):
        chamar(novo_cliente([FalhaTerminal("HTTP 400: inválido"), '{"ok": true}']), spans)
    assert [s.falha_classe for s in spans] == ["terminal", "terminal"]
    assert spans[0].tokens_entrada == 10


def test_no_sem_modelo_ou_modelo_sem_preco_e_terminal():
    with pytest.raises(FalhaTerminal, match="nenhum modelo para o nó 'judge'"):
        novo_cliente([]).chamar("judge", "p", RespostaMinima, run_id="r", registrar_span=lambda s: None)
    with pytest.raises(FalhaTerminal, match="sem preço para outro"):
        novo_cliente([], {"extractor": "outro"})


def test_config_do_repositorio_tem_preco_para_cada_modelo():
    cliente = ClienteLLM.de_config(RAIZ, ProviderFalso([]))
    assert cliente.custo_brl("gemini-3.8-flash", 1_000_000, 0) == Decimal("4.050000")
    assert cliente._raciocinio == {"extractor_correcao": "low"}


def test_nivel_de_raciocinio_invalido_e_terminal():
    with pytest.raises(FalhaTerminal, match="nível de raciocínio inválido"):
        ClienteLLM(ProviderFalso([]), {"extractor": "m"}, {"m": Preco(Decimal(1), Decimal(1))}, Decimal(5),
                   raciocinio={"extractor_correcao": "baixo"})


def test_sem_chave_do_gemini_e_terminal():
    with pytest.raises(FalhaTerminal, match="GOOGLE_API_KEY"):
        llm.conferir_credenciais({})


# Persistência


def test_factsheet_nao_assinado_nao_e_gravado(documentos, repositorio, run_id, tmp_path):
    documento = documentos["copom"]
    with pytest.raises(FalhaTerminal, match="não está assinado"):
        persistencia.gravar_factsheet(repositorio, tmp_path, run_id, factsheet_de(documento, []))


def test_span_repetido_nao_soma_o_custo_de_novo(repositorio, run_id, tmp_path):
    spans = []
    chamar(novo_cliente(['{"ok": true}']), spans)
    (span,) = spans
    span = span.model_copy(update={"run_id": run_id})
    for _ in range(2):
        persistencia.gravar_span(repositorio, tmp_path, span)
    assert repositorio.runs[run_id]["custo_total_tokens"] == 1200
    assert (tmp_path / "runs" / run_id / "spans" / f"{span.span_id}.json").exists()


# Gemini de verdade


@pytest.fixture
def raiz_completa(tmp_path):
    shutil.copytree(RAIZ / "config", tmp_path / "config")
    shutil.copytree(RAIZ / "prompts", tmp_path / "prompts")
    pasta = tmp_path / "tests" / "fixtures" / "referencia"
    pasta.mkdir(parents=True)
    for fonte in FONTES:
        for extensao in ("pdf", "json"):
            shutil.copy(REFERENCIA / f"{fonte}.{extensao}", pasta / f"{fonte}.{extensao}")
    return tmp_path


@pytest.mark.gemini
@pytest.mark.parametrize("fonte", FONTES)
def test_gemini_cobre_os_fatos_anotados(documentos, raiz_completa, fonte):
    from dotenv import dotenv_values

    chave = dotenv_values(RAIZ / ".env").get("GOOGLE_API_KEY")
    if not chave:
        pytest.skip("GOOGLE_API_KEY ausente no .env")
    repo = RepositorioMemoria()
    cliente = llm.criar_cliente(raiz_completa, {"GOOGLE_API_KEY": chave})
    codigo = principal.executar(
        fonte, dev=True, repositorio=repo, checkpointer=InMemorySaver(), cliente_llm=cliente,
        raiz=raiz_completa, relogio=lambda: datetime.now(timezone.utc),
    )
    (run,) = repo.runs.values()
    assert codigo == 0, run.get("erro_mensagem")
    (factsheet,) = repo.factsheets.values()
    print(f"\n{fonte}: {len(factsheet.claims)} claims, {cliente.custo_total_tokens} tokens,"
          f" R$ {cliente.custo_total_brl:.4f}, rodadas {max(k[1] for k in repo.validacoes)}")
    for numero, span in enumerate(repo.spans.values(), start=1):  # uma chamada por rodada, mais as retentativas
        print(f"  chamada {numero}: entrada {span.tokens_entrada}, saída {span.tokens_saida},"
              f" R$ {span.custo_brl:.4f}, {span.duracao_ms / 1000:.0f} s{' (falhou)' if span.falha_classe else ''}")
    faltando = fatos_sem_cobertura(documentos[fonte], factsheet, anotacao(fonte)["fatos"])
    assert not faltando, json.dumps(faltando, ensure_ascii=False, indent=2)
