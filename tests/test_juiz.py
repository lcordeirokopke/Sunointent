"""Juiz (M6): o ProviderOpenRouter com o HTTP simulado, o provider por nó do ClienteLLM, a recuperação de contexto
(BM25, chunk da âncora e vizinhos), o prompt, a completude da resposta, as violações, a matriz de confusão de
níveis e as peças de calibração.

A calibração com o OpenRouter de verdade tem o marker `judge` e só roda com `pytest -m judge`; ela lê só a
API_KEY_ROUTER do .env, julga as peças de tests/fixtures/juiz/calibracao.yaml e exige acerto de pelo menos 80% em
cada grupo (peças corretas aprovadas e peças com erro plantado reprovadas).
"""

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml

from conftest import ProviderFalso
from sunontent import glossario as modulo_glossario
from sunontent import llm
from sunontent.llm import ClienteLLM, Preco, RespostaBruta
from sunontent.metrics import contexto, llm_judge, matriz_niveis
from sunontent.retentativa import Falha, FalhaRetriavel, FalhaTerminal
from sunontent.schemas import (
    Analogia,
    Ancora,
    Chunk,
    ClaimPlanejado,
    FactSheet,
    Pagina,
    Peca,
    PlanoNivel,
    SaidaJuiz,
    celula_id,
    trecho_das_paginas,
)

RAIZ = Path(__file__).resolve().parent.parent
REFERENCIA = RAIZ / "tests" / "fixtures" / "referencia"
CALIBRACAO = RAIZ / "tests" / "fixtures" / "juiz" / "calibracao.yaml"
ACERTO_MINIMO = 0.8
CALIBRACAO_EM_PARALELO = 2
GLOSSARIO = modulo_glossario.carregar(RAIZ)


def referencia(fonte: str) -> tuple[FactSheet, list[Chunk]]:
    """O FactSheet de <fonte>.claims.json e os chunks de <fonte>.documento.json, com o texto tirado das páginas."""
    claims = json.loads((REFERENCIA / f"{fonte}.claims.json").read_text(encoding="utf-8"))
    documento = json.loads((REFERENCIA / f"{fonte}.documento.json").read_text(encoding="utf-8"))
    factsheet = FactSheet(
        doc_id=claims["doc_id"], tipo_documento=claims["tipo_documento"], emissor=claims["emissor"],
        data_documento=claims["data_documento"], claims=claims["claims"], tabela_numeros=[], assinado=True,
    )
    paginas = [Pagina(**p) for p in documento["paginas"]]
    chunks = [
        Chunk(chunk_id=c, secao=s, pagina_inicio=pi, offset_inicio=oi, pagina_fim=pf, offset_fim=of,
              texto=trecho_das_paginas(paginas, pi, oi, pf, of))
        for c, s, pi, oi, pf, of in documento["chunks"]
    ]
    return factsheet, chunks


def peca_de(nivel, formato, payload, tentativa=1) -> Peca:
    peca = {"celula_id": celula_id(nivel, formato), "nivel": nivel, "formato": formato, "tentativa": tentativa,
            "claim_ids": [], "numeros_usados": [], "payload": payload}
    ids = [c for u in (payload.get("secoes") or payload.get("slides") or payload.get("cenas")) for c in u["claim_ids"]]
    peca["claim_ids"] = list(dict.fromkeys(ids))
    return Peca.model_validate(peca)


def plano_de(peca: Peca, analogias=()) -> PlanoNivel:
    return PlanoNivel(
        nivel=peca.nivel, descartes=[], tese="", tese_claim_ids=[],
        claims=[ClaimPlanejado(claim_id=c, papel="principal", prioridade=i + 1, atribuir_fonte=False)
                for i, c in enumerate(peca.claim_ids)],
        analogias=[Analogia(termo=a["termo"], explicacao=a["explicacao"], origem="nova", revisada=False)
                   for a in analogias],
    )


def cliente_juiz(respostas, por_no=True) -> ClienteLLM:
    juiz = ProviderFalso(respostas)
    return ClienteLLM(
        ProviderFalso([]) if por_no else juiz,
        {"judge": "modelo-juiz", "synthesizer": "modelo-falso"},
        {"modelo-juiz": Preco(Decimal(1), Decimal(1)), "modelo-falso": Preco(Decimal(1), Decimal(1))},
        Decimal(5),
        raciocinio={"judge": "low"},
        por_no={"judge": [("API_KEY_ROUTER", juiz)]} if por_no else None,
    )


def resposta(unidades, analogias=(), nivel="intermediario") -> str:
    return json.dumps({
        "analogias": [{"termo": t, "justificativa": "j", "correta": ok} for t, ok in analogias],
        "unidades": [{"unidade_indice": i, "justificativa": f"justificativa {i}", "implicada": ok,
                      "trecho_problematico": trecho} for i, ok, trecho in unidades],
        "nivel_percebido": nivel,
    }, ensure_ascii=False)


COPOM, CHUNKS_COPOM = referencia("copom")
CARROSSEL = peca_de("iniciante", "carrossel", {"slides": [
    {"papel": "gancho", "texto": "Os juros caíram. E agora?", "claim_ids": []},
    {"papel": "corpo", "texto": "O Copom reduziu a Selic para 13,75% ao ano porque o dólar caiu. A Selic é como o "
                                "aluguel do dinheiro.", "claim_ids": ["C27"]},
    {"papel": "corpo", "texto": "O Copom projeta inflação de 5,2% em 2026.", "claim_ids": ["C18"]},
    {"papel": "conclusao", "texto": "Fique de olho.", "claim_ids": []},
]})
ANALOGIA_SELIC = {"termo": "Selic", "explicacao": "É como o aluguel do dinheiro."}


# Cliente LLM: provider por nó e OpenRouter


def test_juiz_usa_a_cadeia_do_no_e_os_outros_nos_a_padrao():
    juiz = ProviderFalso([resposta([(0, True, None)])])
    padrao = ProviderFalso([resposta([(0, True, None)])])
    cliente = ClienteLLM(padrao, {"judge": "m", "synthesizer": "m"}, {"m": Preco(Decimal(1), Decimal(1))}, Decimal(5),
                         por_no={"judge": [("API_KEY_ROUTER", juiz)]})
    cliente.chamar("judge", "juiz", SaidaJuiz, run_id="r", registrar_span=lambda s: None)
    cliente.chamar("synthesizer", "redator", SaidaJuiz, run_id="r", registrar_span=lambda s: None)
    assert juiz.prompts == ["juiz"] and padrao.prompts == ["redator"]


def test_chave_do_openrouter_recusada_e_terminal_sem_reserva():
    cliente = cliente_juiz([llm.FalhaChave("HTTP 401: chave inválida")])
    with pytest.raises(FalhaTerminal, match="chave recusada"):
        cliente.chamar("judge", "p", SaidaJuiz, run_id="r", registrar_span=lambda s: None)


def test_schema_estrito_exige_todas_as_propriedades_e_fecha_os_objetos():
    schema = llm.schema_estrito(SaidaJuiz.model_json_schema())
    unidade = schema["$defs"]["VeredictoUnidade"]
    assert unidade["required"] == ["unidade_indice", "justificativa", "implicada", "trecho_problematico"]
    assert unidade["additionalProperties"] is False and schema["additionalProperties"] is False
    assert "default" not in json.dumps(schema)


def openrouter(resposta_http, pedidos=None) -> llm.ProviderOpenRouter:
    def tratar(pedido):
        if pedidos is not None:
            pedidos.append(pedido)
        return resposta_http(pedido) if callable(resposta_http) else resposta_http

    return llm.ProviderOpenRouter("chave", cliente_http=httpx.Client(transport=httpx.MockTransport(tratar)))


def corpo_ok(conteudo='{"x": 1}', motivo="stop") -> dict:
    return {"choices": [{"finish_reason": motivo, "message": {"content": conteudo}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40,
                      "completion_tokens_details": {"reasoning_tokens": 30}}}


def test_openrouter_pede_schema_estrito_roteamento_e_raciocinio():
    pedidos = []
    bruta = openrouter(httpx.Response(200, json=corpo_ok()), pedidos).gerar(
        "nvidia/nemotron-3-ultra-550b-a55b", "prompt", SaidaJuiz.model_json_schema(), "low")
    (pedido,) = pedidos
    corpo = json.loads(pedido.content)
    assert str(pedido.url) == llm.URL_OPENROUTER and pedido.headers["Authorization"] == "Bearer chave"
    assert corpo["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert corpo["messages"] == [{"role": "user", "content": "prompt"}]
    assert corpo["response_format"]["type"] == "json_schema"
    assert corpo["response_format"]["json_schema"]["strict"] is True
    assert corpo["provider"] == {"require_parameters": True}
    assert corpo["reasoning"] == {"effort": "low", "exclude": True}
    assert corpo["max_tokens"] == llm.MAX_TOKENS_SAIDA_ROUTER
    # Os tokens de raciocínio já estão em completion_tokens: contam como saída, sem somar de novo.
    assert bruta == RespostaBruta('{"x": 1}', 100, 40, None)


def test_openrouter_sem_raciocinio_nao_manda_o_parametro():
    pedidos = []
    openrouter(httpx.Response(200, json=corpo_ok()), pedidos).gerar("m", "p", {})
    assert "reasoning" not in json.loads(pedidos[0].content)


@pytest.mark.parametrize("codigo, classe", [
    (408, FalhaRetriavel), (429, FalhaRetriavel), (500, FalhaRetriavel), (502, FalhaRetriavel),
    (503, FalhaRetriavel), (401, llm.FalhaChave), (402, FalhaTerminal), (400, FalhaTerminal),
    (403, FalhaTerminal),  # 403 sem motivo de moderação, como o teto de gasto da chave
])
def test_openrouter_classifica_o_erro_http(codigo, classe):
    resposta_http = httpx.Response(codigo, json={"error": {"code": codigo, "message": "erro"}})
    with pytest.raises(classe, match=f"HTTP {codigo}"):
        openrouter(resposta_http).gerar("m", "p", {})


def test_openrouter_moderacao_e_content_filter_sao_bloqueio():
    moderado = httpx.Response(403, json={"error": {"code": 403, "message": "flagged",
                                                   "metadata": {"reasons": ["violencia"]}}})
    assert "violencia" in openrouter(moderado).gerar("m", "p", {}).bloqueio
    filtrado = openrouter(httpx.Response(200, json=corpo_ok("", "content_filter"))).gerar("m", "p", {})
    assert filtrado.bloqueio == "finish_reason content_filter"


def test_openrouter_erro_com_http_200_e_retriavel():
    with pytest.raises(FalhaRetriavel, match="HTTP 502"):
        openrouter(httpx.Response(200, json={"error": {"code": 502, "message": "provedor caiu"}})).gerar("m", "p", {})
    no_meio = {"choices": [{"finish_reason": "error", "error": {"message": "cortou"}, "message": {"content": ""}}]}
    with pytest.raises(FalhaRetriavel, match="cortou"):
        openrouter(httpx.Response(200, json=no_meio)).gerar("m", "p", {})


def test_openrouter_conexao_e_timeout_sao_retriaveis():
    def cair(pedido):
        raise httpx.ReadTimeout("demorou", request=pedido)

    with pytest.raises(FalhaRetriavel, match="ReadTimeout"):
        openrouter(cair).gerar("m", "p", {})


def test_saida_esgotada_pelo_raciocinio_volta_como_fora_do_schema():
    """`finish_reason` length com o conteúdo vazio: o ClienteLLM repete com o erro de validação."""
    vazia = RespostaBruta("", 100, 16384)
    cliente = cliente_juiz([vazia, resposta([(0, True, None)])])
    spans = []
    saida = cliente.chamar("judge", "p", SaidaJuiz, run_id="r", registrar_span=spans.append)
    assert saida.unidades[0].implicada and spans[0].falha_mensagem.startswith("resposta fora do schema")
    assert spans[0].tokens_saida == 16384  # os tokens da resposta vazia são cobrados


# Recuperação de contexto


def test_tokens_sem_acento_sem_maiuscula_e_sem_palavras_vazias():
    assert contexto.tokens("A inflação de preços e a Selic") == ["inflacao", "precos", "selic"]


def test_bm25_ordena_pela_sobreposicao_lexical():
    bm25 = contexto.BM25(["juros e Selic em queda", "receita da companhia", "Selic Selic Selic juros"])
    notas = bm25.pontuar("a Selic e os juros")
    assert notas[1] == 0 and notas[2] > notas[0] > 0


def test_chunk_da_ancora_e_o_que_contem_o_inicio():
    c27 = next(c for c in COPOM.claims if c.id == "C27")
    posicao = contexto.chunk_da_ancora(CHUNKS_COPOM, c27.ancora)
    assert c27.literal_fonte.split()[0] in CHUNKS_COPOM[posicao].texto
    assert contexto.chunk_da_ancora(CHUNKS_COPOM, Ancora(pagina=99, offset_inicio=0, offset_fim=1)) is None


def test_contexto_traz_ancora_vizinhos_e_tres_lexicos_por_unidade():
    ctx = contexto.montar(CARROSSEL, {c.id: c for c in COPOM.claims}, CHUNKS_COPOM)
    ids = [c.chunk_id for c in CHUNKS_COPOM]
    posicao = contexto.chunk_da_ancora(CHUNKS_COPOM, next(c for c in COPOM.claims if c.id == "C27").ancora)
    corpo = ctx.unidades[1]
    assert corpo.claim_ids == ["C27"] and corpo.cita_claims
    assert corpo.chunks_ancora == [ids[p] for p in (posicao - 1, posicao, posicao + 1) if 0 <= p < len(ids)]
    assert len(corpo.chunks_lexicos) == contexto.QUANTIDADE_LEXICA
    assert not set(corpo.chunks_lexicos) & set(corpo.chunks_ancora)
    # O gancho não cita claim: recebe todos os da peça.
    assert ctx.unidades[0].claim_ids == ["C27", "C18"] and not ctx.unidades[0].cita_claims
    # Cada chunk vai uma vez, na ordem do documento.
    enviados = [c.chunk_id for c in ctx.chunks]
    assert enviados == sorted(set(enviados), key=ids.index)


def test_documento_de_um_chunk_so_nao_tem_vizinho_nem_lexico():
    cvm, chunks = referencia("cvm")
    peca = peca_de("intermediario", "carrossel", {"slides": [
        {"papel": "gancho", "texto": "Sabesp e EMAE", "claim_ids": []},
        {"papel": "corpo", "texto": "A Sabesp incorporará as ações da EMAE.", "claim_ids": ["C01"]},
        {"papel": "conclusao", "texto": "Fim.", "claim_ids": []},
    ]})
    ctx = contexto.montar(peca, {c.id: c for c in cvm.claims}, chunks)
    assert [len(chunks)] == [1] and ctx.unidades[1].chunks_ancora == [chunks[0].chunk_id]
    assert ctx.unidades[1].chunks_lexicos == []


# Juiz: analogias, prompt, completude e violações


def test_so_as_analogias_do_plano_usadas_na_peca_vao_ao_juiz():
    plano = plano_de(CARROSSEL, [ANALOGIA_SELIC, {"termo": "IPCA", "explicacao": "É como a cesta do mercado."}])
    (selic,) = llm_judge.analogias_usadas(CARROSSEL, plano, GLOSSARIO)
    assert (selic.termo, selic.unidade_indice, selic.trecho) == ("Selic", 1, "Selic")
    assert selic.definicao_tecnica == GLOSSARIO.termos["selic"].definicao_tecnica


def julgar(cliente, peca=CARROSSEL, analogias=(ANALOGIA_SELIC,), fonte=(COPOM, CHUNKS_COPOM)):
    spans = []
    factsheet, chunks = fonte
    m6 = llm_judge.julgar(cliente, RAIZ, peca, plano_de(peca, analogias), factsheet, chunks, GLOSSARIO,
                          run_id="r", registrar_span=spans.append)
    return m6, spans


def test_prompt_traz_claims_trechos_unidades_e_definicao_sem_o_nivel_pedido():
    cliente = cliente_juiz([resposta([(i, True, None) for i in range(4)], [("Selic", True)])])
    julgar(cliente)
    prompt = cliente._por_no["judge"][0][1].prompts[0]
    c27 = next(c for c in COPOM.claims if c.id == "C27")
    assert f"- C27 ({c27.tipo}): {c27.texto}" in prompt and " ".join(c27.literal_fonte.split()) in prompt
    assert "## Unidade 1 (corpo)" in prompt and "porque o dólar caiu" in prompt
    assert GLOSSARIO.termos["selic"].definicao_tecnica in prompt and "É como o aluguel do dinheiro." in prompt
    assert "de 0 a 3" in prompt and "pedido" not in prompt.split("# Pergunta")[0]
    ctx = contexto.montar(CARROSSEL, {c.id: c for c in COPOM.claims}, CHUNKS_COPOM)
    assert all(f"[{c.chunk_id}]" in prompt for c in ctx.chunks)
    assert cliente._por_no["judge"][0][1].raciocinios == ["low"]


def test_juiz_aprova_quando_tudo_e_implicado_e_registra_o_nivel_percebido():
    cliente = cliente_juiz([resposta([(i, True, None) for i in range(4)], [("Selic", True)], "iniciante")])
    m6, spans = julgar(cliente)
    assert m6.nome == "llm_judge" and m6.passou and m6.valor is True and m6.faixa_esperada == "true"
    assert m6.detalhes.nivel_percebido == "iniciante" and len(m6.detalhes.unidades) == 4
    (span,) = spans
    assert (span.no, span.celula_id, span.tentativa, span.nivel) == ("judge", "iniciante__carrossel", 1, "iniciante")


def test_cada_false_vira_uma_violacao_com_a_posicao_do_trecho():
    unidades = [(0, True, None), (1, False, "porque o dólar caiu"), (2, True, None), (3, False, "trecho que não existe")]
    m6, _ = julgar(cliente_juiz([resposta(unidades, [("selic", False)])]))
    assert not m6.passou and m6.valor is False
    analogia, causa, sem_posicao = m6.violacoes
    texto = CARROSSEL.payload.slides[1].texto
    assert (analogia.unidade_indice, analogia.trecho, analogia.campo) == (1, "Selic", "texto")
    assert texto[analogia.inicio:analogia.fim] == "Selic"
    assert GLOSSARIO.termos["selic"].definicao_tecnica in analogia.instrucao_corretiva
    assert texto[causa.inicio:causa.fim] == "porque o dólar caiu" and causa.unidade_indice == 1
    assert "C27" in causa.esperado and "justificativa 1" in causa.instrucao_corretiva
    # Trecho que não está na unidade: a violação fica na unidade, sem intervalo.
    assert (sem_posicao.unidade_indice, sem_posicao.inicio, sem_posicao.campo) == (3, None, "texto")


def test_trecho_no_gancho_visual_do_roteiro():
    peca = peca_de("avancado", "roteiro", {"cenas": [
        {"inicio_s": 0, "fim_s": 5, "fala": "Abertura", "gancho_visual": "Logo", "claim_ids": []},
        {"inicio_s": 5, "fim_s": 20, "fala": "O Copom reduziu a Selic para 13,75% a.a.",
         "gancho_visual": "Selic sobe", "claim_ids": ["C27"]},
        {"inicio_s": 20, "fim_s": 25, "fala": "Fim", "gancho_visual": "Logo", "claim_ids": []},
    ]})
    m6, _ = julgar(cliente_juiz([resposta([(0, True, None), (1, False, "Selic sobe"), (2, True, None)])]),
                   peca=peca, analogias=())
    (violacao,) = m6.violacoes
    assert (violacao.campo, violacao.inicio, violacao.fim) == ("gancho_visual", 0, 10)


@pytest.mark.parametrize("unidades, analogias, mensagem", [
    ([(0, True, None), (1, True, None), (2, True, None)], [("Selic", True)], "unidades sem resposta: 3"),
    ([(i, True, None) for i in range(4)] + [(1, True, None)], [("Selic", True)], "unidades repetidas"),
    ([(i, True, None) for i in range(5)], [("Selic", True)], "unidades repetidas ou inexistentes: 4"),
    ([(i, True, None) for i in range(4)], [], "analogias sem resposta: Selic"),
    ([(i, True, None) for i in range(4)], [("Selic", True), ("IPCA", True)], "não pedidas: IPCA"),
])
def test_resposta_incompleta_e_falha_terminal(unidades, analogias, mensagem):
    with pytest.raises(FalhaTerminal, match=f"resposta incompleta.*{mensagem}"):
        julgar(cliente_juiz([resposta(unidades, analogias)]))


def test_criar_juiz_tem_a_assinatura_que_a_e6_chama():
    cliente = cliente_juiz([resposta([(i, True, None) for i in range(4)], [("Selic", True)])])
    juiz = llm_judge.criar_juiz(cliente, RAIZ)
    m6 = juiz(CARROSSEL, plano_de(CARROSSEL, [ANALOGIA_SELIC]), COPOM, CHUNKS_COPOM, GLOSSARIO,
              run_id="r", registrar_span=lambda s: None)
    assert m6.passou


# Matriz de confusão de níveis


def test_matriz_cruza_nivel_pedido_e_percebido_por_formato():
    matriz = matriz_niveis.montar([
        ("artigo", "iniciante", "iniciante"), ("artigo", "avancado", "intermediario"),
        ("roteiro", "avancado", "avancado"),
    ])
    assert matriz["artigo"]["avancado"]["intermediario"] == 1 and matriz["carrossel"]["iniciante"]["iniciante"] == 0
    assert matriz_niveis.acerto(matriz) == (2, 3) and matriz_niveis.acerto(matriz, "artigo") == (1, 2)
    texto = matriz_niveis.formatar(matriz)
    assert "artigo: 1 de 2 no nível pedido" in texto and "total: 2 de 3 no nível pedido" in texto


def test_comando_da_matriz_le_a_consulta(monkeypatch, capsys):
    class Consulta:
        def niveis_percebidos(self, run_ids):
            assert run_ids == ["r1"]
            return [{"formato": "carrossel", "nivel": "iniciante", "nivel_percebido": "intermediario"}]

    monkeypatch.setattr("sunontent.persistencia.criar_consulta_supabase", lambda ambiente: Consulta())
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    assert matriz_niveis.main(["r1"]) == 0
    assert "carrossel: 0 de 1 no nível pedido" in capsys.readouterr().out


# Calibração


def pecas_de_calibracao() -> list[dict]:
    return yaml.safe_load(CALIBRACAO.read_text(encoding="utf-8"))["pecas"]


def test_pecas_de_calibracao_sao_validas_e_equilibradas():
    pecas = pecas_de_calibracao()
    referencias = {f: referencia(f)[0] for f in ("copom", "cvm", "b3")}
    for p in pecas:
        peca = peca_de(p["nivel"], p["formato"], p["payload"])
        ids = {c.id for c in referencias[p["fonte"]].claims}
        assert set(peca.claim_ids) <= ids, p["id"]
        if p["erro"]:
            assert 0 <= p["unidade_erro"] < len(peca.payload.textos()), p["id"]
        # Toda analogia dada é de um termo do glossário usado na peça.
        assert len(llm_judge.analogias_usadas(peca, plano_de(peca, p["analogias"]), GLOSSARIO)) == len(p["analogias"])
    assert len({p["id"] for p in pecas}) == len(pecas) >= 18
    com_erro = [p for p in pecas if p["erro"]]
    assert len(com_erro) * 2 == len(pecas)
    assert {p["erro"] for p in com_erro} == {"analogia_errada", "causa_inventada", "projecao_como_fato",
                                             "numero_por_extenso"}
    assert {(p["nivel"], p["formato"]) for p in pecas} == {(n, f) for n in ("iniciante", "intermediario", "avancado")
                                                          for f in ("artigo", "carrossel", "roteiro")}
    assert {p["fonte"] for p in pecas} == {"copom", "cvm", "b3"}


@pytest.mark.judge
def test_calibracao_do_juiz():
    from dotenv import dotenv_values

    chave = dotenv_values(RAIZ / ".env").get(llm.CHAVE_ROUTER)
    if not chave:
        pytest.skip(f"{llm.CHAVE_ROUTER} ausente no .env")
    cliente = ClienteLLM.de_config(RAIZ, ProviderFalso([]), por_no={
        "judge": [(llm.CHAVE_ROUTER, llm.ProviderOpenRouter(chave))]})
    fontes = {f: referencia(f) for f in ("copom", "cvm", "b3")}

    def julgar_peca(p):
        """O MetricaResultado ou, se o juiz falhou (ERRO_INFRA no pipeline), a Falha, que conta como erro."""
        peca = peca_de(p["nivel"], p["formato"], p["payload"])
        factsheet, chunks = fontes[p["fonte"]]
        try:
            return llm_judge.julgar(cliente, RAIZ, peca, plano_de(peca, p["analogias"]), factsheet, chunks,
                                    GLOSSARIO, run_id="calibracao", registrar_span=lambda s: None)
        except Falha as falha:
            return falha

    pecas = pecas_de_calibracao()
    # O OpenRouter reserva o custo do max_tokens de cada chamada em andamento: poucas em paralelo.
    with ThreadPoolExecutor(max_workers=CALIBRACAO_EM_PARALELO) as executor:
        resultados = list(executor.map(julgar_peca, pecas))

    linhas = []
    for p, m6 in zip(pecas, resultados):
        if isinstance(m6, Falha):
            linhas.append(f"ERRO {p['id']:<42} falha do juiz: {m6.mensagem[:200]}")
            continue
        acertou = m6.passou == (p["erro"] is None)
        apontadas = sorted({v.unidade_indice for v in m6.violacoes})
        linhas.append(f"{'ok  ' if acertou else 'ERRO'} {p['id']:<42} {'reprovou' if not m6.passou else 'aprovou':<8}"
                      f" unidades {apontadas} (plantado: {p.get('unidade_erro')}) percebido {m6.detalhes.nivel_percebido}")
        for v in m6.violacoes:
            linhas.append(f"       unidade {v.unidade_indice}: {v.obtido[:180]}")
    julgadas = [(p, m6) for p, m6 in zip(pecas, resultados) if not isinstance(m6, Falha)]
    corretas = [p["id"] in {q["id"] for q, m6 in julgadas if m6.passou} for p in pecas if p["erro"] is None]
    com_erro = [p["id"] in {q["id"] for q, m6 in julgadas if not m6.passou} for p in pecas if p["erro"]]
    matriz = matriz_niveis.montar((p["formato"], p["nivel"], m6.detalhes.nivel_percebido) for p, m6 in julgadas)
    print("\n" + "\n".join(linhas))
    print(f"\ncorretas aprovadas: {sum(corretas)} de {len(corretas)}; com erro reprovadas: {sum(com_erro)} de "
          f"{len(com_erro)}; {cliente.custo_total_tokens} tokens, R$ {cliente.custo_total_brl:.4f}\n")
    print(matriz_niveis.formatar(matriz))
    assert sum(corretas) / len(corretas) >= ACERTO_MINIMO
    assert sum(com_erro) / len(com_erro) >= ACERTO_MINIMO
