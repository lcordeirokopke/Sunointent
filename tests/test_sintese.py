"""E5 de uma célula: pacote (E5.1), redator (E5.2) com o LLM simulado, montagem com o plan_contract (E5.3), gravação
da peça e o nó synth, com o LevelSpec e o glossário de config/."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import cliente_falso
from sunontent import glossario as modulo_glossario
from sunontent import levelspec, persistencia
from sunontent.metrics import numeric_fidelity
from sunontent.nodes.synth import synth
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import (
    FORMATOS,
    NIVEIS,
    Ancora,
    CelulaGrafoState,
    CelulaState,
    Claim,
    ClaimPlanejado,
    FactSheet,
    Numero,
    PlanoEditorial,
    PlanoNivel,
    RelatorioCorretivo,
    SaidaArtigo,
    SaidaCarrossel,
    SaidaRoteiro,
    Violacao,
    celula_id,
)
from sunontent.sintese import montagem, pacote, redator

RAIZ = Path(__file__).resolve().parent.parent
SPECS = levelspec.carregar_todos(RAIZ)
GLOSSARIO = modulo_glossario.carregar(RAIZ)
ANCORA = Ancora(pagina=1, offset_inicio=0, offset_fim=1)


def numero(bruto, valor, unidade):
    return Numero(valor=Decimal(valor), unidade=unidade, bruto=bruto, ancora=ANCORA)


def claim(id, texto, tipo="resultado", numeros=()):
    return Claim(id=id, texto=texto, numeros=list(numeros), tipo=tipo, tags=["indicador_chave"], ancora=ANCORA,
                 literal_fonte="x")


CLAIMS = [
    claim("C01", "O Copom reduziu a Selic para 13,75% em 20 de setembro de 2026.", "decisao",
          [numero("13,75%", "13.75", "pct")]),
    claim("C02", "O IPCA subiu 0,50 p.p. no trimestre.", numeros=[numero("0,50 p.p.", "0.50", "pp")]),
    claim("C03", "Segundo o Copom, a inflação deve ficar em 4,5% em 2027.", "projecao", [numero("4,5%", "4.5", "pct")]),
    claim("C04", "O lucro foi de R$ 1,2 bi.", numeros=[numero("R$ 1,2 bi", "1200", "BRL_mi")]),
    claim("C05", "A taxa ficou em 15% e a meta em 15%.", numeros=[numero("15%", "15", "pct"), numero("15%", "15", "pct")]),
    claim("C06", "O comitê manteve o tom das reuniões anteriores."),
]
FACTSHEET = FactSheet(doc_id="doc", tipo_documento="copom_ata", emissor="Banco Central do Brasil",
                      data_documento=date(2026, 9, 20), claims=CLAIMS, tabela_numeros=[], assinado=True)


def planejado(claim_id, papel, prioridade, atribuir_fonte=False):
    return ClaimPlanejado(claim_id=claim_id, papel=papel, prioridade=prioridade, atribuir_fonte=atribuir_fonte)


# Iniciante: C02 tem prioridade 4, por isso sai do roteiro (3 claims) e fica no carrossel (4 claims).
PLANO = PlanoEditorial(
    nucleo=["C01"],
    rodadas=1,
    aviso=None,
    niveis=[
        PlanoNivel(
            nivel="iniciante", descartes=[], tese="Os juros caíram.", tese_claim_ids=["C01"],
            claims=[planejado("C01", "principal", 1), planejado("C02", "apoio", 4),
                    planejado("C03", "apoio", 2, True), planejado("C04", "contexto", 3),
                    planejado("C06", "contexto", 5)],
            analogias=[GLOSSARIO.analogia("selic"), GLOSSARIO.analogia("ipca")],
        ),
        PlanoNivel(
            nivel="intermediario", descartes=[], tese="", tese_claim_ids=[], analogias=[],
            claims=[planejado(c, "principal" if c == "C01" else "apoio", i)
                    for i, c in enumerate(["C01", "C02", "C03", "C04", "C05"], start=1)],
        ),
        PlanoNivel(
            nivel="avancado", descartes=[], tese="Ciclo de cortes.", tese_claim_ids=["C01"], analogias=[],
            claims=[planejado(c.id, "apoio" if c.id != "C01" else "principal", i) for i, c in enumerate(CLAIMS, start=1)],
        ),
    ],
)


def pacote_de(nivel, formato, plano=PLANO):
    return pacote.montar(plano, nivel, formato, FACTSHEET, SPECS[nivel], GLOSSARIO)


def saida_artigo(**trocas):
    dados = {
        "titulo": "Selic cai para {{C01.1}}",
        "linha_fina": "O que muda no bolso",
        "secoes": [
            {"texto": "O Copom reduziu a Selic para {{C01.1}}. A Selic é como o preço do dinheiro.",
             "claim_ids": ["C01"]},
            {"texto": "O IPCA subiu {{C02.1}}, o lucro foi de {{C04.1}} e o Copom projeta {{C03.1}}. "
                      "Nada mudou no tom. {{C09.1}} {{C01.7}} {{C05.1}}",
             "claim_ids": ["C02", "C03", "C04", "C06"]},
        ],
    }
    return SaidaArtigo.model_validate({**dados, **trocas})


# E5.1 Pacote


def test_marcadores_trocam_os_numeros_e_deixam_as_datas():
    texto, marcadores = pacote.marcar(CLAIMS[0])
    assert texto == "O Copom reduziu a Selic para {{C01.1}} em 20 de setembro de 2026."
    assert [(m.marcador, m.claim_id, m.ordem, m.trecho) for m in marcadores] == [("{{C01.1}}", "C01", 1, "13,75%")]


def test_marcador_usa_o_trecho_do_texto_do_claim_e_a_primeira_entrada_livre():
    texto, marcadores = pacote.marcar(CLAIMS[4])
    assert texto == "A taxa ficou em {{C05.1}} e a meta em {{C05.2}}."
    assert [m.ordem for m in marcadores] == [1, 2]
    _, (m,) = pacote.marcar(CLAIMS[3])
    assert m.trecho == "R$ 1,2 bi"


@pytest.mark.parametrize("numeros, mensagem", [
    ([], "não está na lista"),
    ([numero("13,75%", "13.75", "pct"), numero("2%", "2", "pct")], "que o texto não menciona"),
    ([numero("13,75", "13.75", "contagem")], "não está na lista"),
])
def test_achado_sem_entrada_ou_entrada_sem_achado_e_erro_terminal(numeros, mensagem):
    with pytest.raises(FalhaTerminal, match=mensagem):
        pacote.marcar(claim("C01", "A Selic foi para 13,75%.", numeros=numeros))


def test_artigo_usa_todos_os_claims_do_plano_e_o_corte_mantem_o_nucleo_e_a_ordem():
    assert pacote_de("iniciante", "artigo").claim_ids == ["C01", "C02", "C03", "C04", "C06"]
    assert pacote_de("iniciante", "carrossel").claim_ids == ["C01", "C02", "C03", "C04"]
    assert pacote_de("iniciante", "roteiro").claim_ids == ["C01", "C03", "C04"]
    planejados = [planejado("C01", "contexto", 5), planejado("C02", "principal", 1), planejado("C03", "apoio", 2),
                  planejado("C04", "apoio", 3)]
    assert [c.claim_id for c in pacote.cortar(planejados, ["C01"], 3)] == ["C01", "C02", "C03"]


def test_marcadores_validos_sao_os_do_plano_do_nivel():
    p = pacote_de("iniciante", "roteiro")
    assert set(p.marcadores) == {"{{C01.1}}", "{{C02.1}}", "{{C03.1}}", "{{C04.1}}"}
    assert p.plano_claim_ids == ("C01", "C02", "C03", "C04", "C06")


def test_orcamento_do_artigo_divide_a_faixa_pelo_peso_do_papel_arredondando_para_baixo():
    # Iniciante: 350 a 550 palavras; pesos 3 (principal), 2 (apoio) e 1 (contexto), soma 9.
    p = pacote_de("iniciante", "artigo")
    assert [c.orcamento for c in p.claims] == [
        "de 116 a 183 palavras", "de 77 a 122 palavras", "de 77 a 122 palavras", "de 38 a 61 palavras",
        "de 38 a 61 palavras",
    ]


def test_orcamento_do_roteiro_divide_os_segundos_e_as_palavras_das_cenas_de_claim():
    # 60 s menos 6 de abertura e 6 de fechamento: 48 s pelos pesos 3, 2 e 1; 2,5 palavras por segundo.
    p = pacote_de("iniciante", "roteiro")
    assert [c.orcamento for c in p.claims] == ["24 s, até 60 palavras", "16 s, até 40 palavras", "8 s, até 20 palavras"]
    assert (p.orcamento_abertura, p.orcamento_fechamento) == ("6 s, até 15 palavras", "6 s, até 15 palavras")


def test_resto_dos_segundos_vai_para_a_ultima_cena_de_claim():
    plano = PLANO.model_copy(deep=True)
    plano.niveis[0].claims = [planejado("C01", "principal", 1), planejado("C03", "apoio", 2)]
    p = pacote_de("iniciante", "roteiro", plano)
    # 48 s pelos pesos 3 e 2: 28 e 19, e o segundo que sobra vai para a última.
    assert [c.orcamento for c in p.claims] == ["28 s, até 70 palavras", "20 s, até 50 palavras"]


def test_carrossel_nao_divide_por_claim():
    assert all(c.orcamento == "" for c in pacote_de("iniciante", "carrossel").claims)


def test_analogias_presas_aos_claims_do_formato():
    carrossel = pacote_de("iniciante", "carrossel")
    assert [(a.termo, a.claim_ids) for a in carrossel.analogias] == [("Selic", ("C01",)), ("IPCA", ("C02",))]
    roteiro = pacote_de("iniciante", "roteiro")  # C02 saiu pelo corte, e a analogia do IPCA sai com ele
    assert [a.termo for a in roteiro.analogias] == ["Selic"]


def test_pacote_traz_tese_atribuicao_nucleo_e_data_do_documento():
    p = pacote_de("iniciante", "artigo")
    assert p.tese == "Os juros caíram."
    assert [(c.id, c.nucleo, c.atribuir_fonte) for c in p.claims][:3] == [
        ("C01", True, False), ("C02", False, False), ("C03", False, True)]
    assert p.data_documento == date(2026, 9, 20)


def test_claim_do_plano_fora_do_factsheet_e_erro_terminal():
    plano = PLANO.model_copy(deep=True)
    plano.niveis[1].claims.append(planejado("C99", "apoio", 6))
    with pytest.raises(FalhaTerminal, match="C99"):
        pacote_de("intermediario", "artigo", plano)


# E5.2 Redator


def test_prompt_traz_marcadores_orcamento_tese_analogias_e_expressoes():
    p = pacote_de("iniciante", "artigo")
    prompt = redator.montar_prompt(RAIZ, p, SPECS["iniciante"])
    assert "C01 (principal, núcleo), de 116 a 183 palavras: O Copom reduziu a Selic para {{C01.1}} em 20 de" in prompt
    assert "Marcadores: {{C01.1}} = 13,75%" in prompt
    assert "C03 (apoio, atribuir à fonte)" in prompt
    assert "Os juros caíram." in prompt
    assert "Selic (nos claims C01)" in prompt
    assert '"é como"' in prompt
    assert "o ano do documento (2026)" in prompt
    assert "de 350 a 550 palavras no texto das seções" in prompt
    assert "Correção" not in prompt
    assert "{%" not in prompt and "{#" not in prompt


def test_prompt_fora_do_iniciante_nao_tem_analogia_e_roteiro_tem_abertura():
    prompt = redator.montar_prompt(RAIZ, pacote_de("avancado", "roteiro"), SPECS["avancado"])
    assert "analogias do plano" not in prompt and "é como" not in prompt
    assert "Abertura: 6 s" in prompt
    assert "Ciclo de cortes." in prompt


def test_prompt_do_retry_traz_a_peca_anterior_e_o_relatorio_agrupado():
    relatorio = RelatorioCorretivo(
        certos=["text_size: 0 unidades fora do limite"],
        violacoes=[
            Violacao(metrica="numeric_fidelity", trecho="4,75%", esperado="número de marcador", obtido="4,75%",
                     instrucao_corretiva="Use o marcador {{C01.1}}.", unidade_indice=1, campo="texto",
                     inicio=0, fim=5),
            Violacao(metrica="plan_contract", trecho="C04", esperado="o claim em alguma seção", obtido="ausente",
                     instrucao_corretiva="Escreva o fato do C04."),
        ],
        restantes={"numeric_fidelity": 2},
    )
    prompt = redator.montar_prompt(RAIZ, pacote_de("iniciante", "artigo"), SPECS["iniciante"], saida_artigo(), relatorio)
    assert "# Correção" in prompt
    assert '"titulo": "Selic cai para {{C01.1}}"' in prompt
    assert "- text_size: 0 unidades fora do limite" in prompt
    assert "## seção 1\n" in prompt and "## a peça inteira\n" in prompt
    assert '[numeric_fidelity] trecho: "4,75%"' in prompt
    assert "Mais 2 violações de numeric_fidelity" in prompt


# E5.3 Montagem


def test_montagem_troca_os_marcadores_e_registra_a_posicao_de_cada_numero():
    p = pacote_de("iniciante", "artigo")
    peca, contrato = montagem.montar(saida_artigo(), p, SPECS["iniciante"], tentativa=1)
    assert peca.payload.titulo == "Selic cai para 13,75%"
    assert peca.payload.secoes[0].texto == "O Copom reduziu a Selic para 13,75%. A Selic é como o preço do dinheiro."
    textos = {("titulo", None): peca.payload.titulo, **{("texto", i): s.texto for i, s in enumerate(peca.payload.secoes)}}
    trechos = [(n.campo, n.unidade_indice, n.claim_id, n.ordem, textos[(n.campo, n.unidade_indice)][n.inicio:n.fim])
               for n in peca.numeros_usados]
    assert trechos == [
        ("titulo", None, "C01", 1, "13,75%"), ("texto", 0, "C01", 1, "13,75%"), ("texto", 1, "C02", 1, "0,50 p.p."),
        ("texto", 1, "C04", 1, "R$ 1,2 bi"), ("texto", 1, "C03", 1, "4,5%"),
    ]
    assert contrato.nome == "plan_contract" and contrato.passou
    assert [c.nome for c in contrato.detalhes.checagens] == ["claim_ids_no_plano", "cobertura_do_formato"]


def test_marcador_invalido_fica_no_texto_e_a_m1_o_aponta():
    p = pacote_de("iniciante", "artigo")
    peca, _ = montagem.montar(saida_artigo(), p, SPECS["iniciante"], tentativa=1)
    # inexistente, ordem que o claim não tem e claim fora do plano do nível
    assert peca.payload.secoes[1].texto.endswith("{{C09.1}} {{C01.7}} {{C05.1}}")
    plano = [c for c in CLAIMS if c.id in p.plano_claim_ids]
    m1 = numeric_fidelity.avaliar(peca, plano, [], FACTSHEET.data_documento, GLOSSARIO)
    assert m1.detalhes.desfechos.marcador_invalido == 3


def test_restaurar_devolve_a_saida_do_redator():
    p = pacote_de("iniciante", "artigo")
    saida = saida_artigo()
    peca, _ = montagem.montar(saida, p, SPECS["iniciante"], tentativa=1)
    assert montagem.restaurar(peca) == saida


def carrossel(slides):
    return SaidaCarrossel.model_validate({"slides": slides})


def test_plan_contract_do_carrossel():
    p = pacote_de("iniciante", "carrossel")  # C01, C02, C03 e C04; de 5 a 7 slides
    slides = [{"papel": "gancho", "texto": "Juros caem", "claim_ids": []}]
    slides += [{"papel": "corpo", "texto": f"Slide {i}", "claim_ids": ids}
               for i, ids in enumerate([["C01"], ["C05"], ["C02"], ["C03"], ["C01"], ["C02"]], start=1)]
    slides += [{"papel": "conclusao", "texto": "Fim", "claim_ids": []}]
    peca, contrato = montagem.montar(carrossel(slides), p, SPECS["iniciante"], tentativa=1)
    assert contrato.valor == 3 and not contrato.passou
    por_nome = {c.nome: c for c in contrato.detalhes.checagens}
    assert (por_nome["quantidade_de_slides"].obtido, por_nome["quantidade_de_slides"].passou) == ("8 slides", False)
    v = {(x.trecho, x.unidade_indice): x for x in contrato.violacoes}
    assert "no máximo 7 slides" in v[("8 slides", None)].instrucao_corretiva
    assert "tire-o de claim_ids do slide 2" in v[("C05", 2)].instrucao_corretiva
    assert "não aparece em nenhum slide" in v[("C04", None)].instrucao_corretiva
    assert all(x.metrica == "plan_contract" for x in contrato.violacoes)


def test_plan_contract_do_carrossel_curto():
    p = pacote_de("iniciante", "carrossel")
    slides = [{"papel": "gancho", "texto": "Juros", "claim_ids": []},
              {"papel": "corpo", "texto": "Tudo", "claim_ids": ["C01", "C02", "C03", "C04"]},
              {"papel": "conclusao", "texto": "Fim", "claim_ids": []}]
    _, contrato = montagem.montar(carrossel(slides), p, SPECS["iniciante"], tentativa=1)
    assert [v.instrucao_corretiva.split(":")[0] for v in contrato.violacoes] == [
        "Aumente o carrossel para pelo menos 5 slides, contando o gancho e a conclusão"]


def test_plan_contract_do_roteiro_confere_o_fim_da_ultima_cena():
    p = pacote_de("iniciante", "roteiro")  # C01, C03 e C04; 60 s
    tempos = [(0, 6, []), (6, 30, ["C01"]), (30, 50, ["C03"]), (50, 65, ["C04"]), (65, 70, [])]
    saida = SaidaRoteiro.model_validate({"cenas": [
        {"inicio_s": a, "fim_s": b, "fala": "Fala {{C01.1}}", "gancho_visual": "tela", "claim_ids": ids}
        for a, b, ids in tempos]})
    peca, contrato = montagem.montar(saida, p, SPECS["iniciante"], tentativa=2)
    assert peca.tentativa == 2 and peca.payload.cenas[1].fala == "Fala 13,75%"
    assert [(v.trecho, v.unidade_indice) for v in contrato.violacoes] == [("70 s", 4)]
    assert [c.nome for c in contrato.detalhes.checagens] == ["soma_das_cenas", "claim_ids_no_plano",
                                                              "cobertura_do_formato"]


# Nó e gravação


@pytest.fixture
def com_plano(repositorio, run_id, tmp_path):
    repositorio.factsheets[run_id] = FACTSHEET.model_dump(mode="json", exclude={"tabela_numeros"})
    celulas = [CelulaState(celula_id=celula_id(n, f), nivel=n, formato=f) for n in NIVEIS for f in FORMATOS]
    persistencia.gravar_plano(repositorio, tmp_path, run_id, PLANO, celulas)
    return repositorio


def config(repositorio, tmp_path, llm):
    return {"configurable": {"repositorio": repositorio, "raiz_dados": tmp_path, "raiz": RAIZ, "llm": llm}}


def estado(run_id, celula=None, relatorio=None):
    celula = celula or CelulaState(celula_id="iniciante__artigo", nivel="iniciante", formato="artigo")
    return CelulaGrafoState(run_id=run_id, celula=celula, relatorio=relatorio)


def resposta(saida) -> str:
    return json.dumps(saida.model_dump(), ensure_ascii=False)


def test_no_grava_a_peca_e_deixa_a_celula_em_avaliando(com_plano, run_id, tmp_path):
    secoes = saida_artigo().model_dump()["secoes"]
    secoes[1]["claim_ids"].append("C99")  # id inventado: vai para o plan_contract, não para peca_unidade_claims
    llm = cliente_falso([resposta(saida_artigo(secoes=secoes))])
    celula = synth(estado(run_id), config(com_plano, tmp_path, llm))["celula"]

    assert (celula.estado, celula.tentativa, celula.peca.tentativa) == ("AVALIANDO", 1, 1)
    assert [v.trecho for v in celula.plan_contract.violacoes] == ["C99"]
    assert com_plano.chamadas["atualizar_celula"] == 1  # GERANDO; a tentativa e AVALIANDO vão com a peça
    gravada = com_plano.celulas[(run_id, "iniciante__artigo")]
    assert (gravada.estado, gravada.tentativa) == ("AVALIANDO", 1)
    assert "C99" in com_plano.pecas[(run_id, "iniciante__artigo", 1)]["claim_ids"]
    unidades = {(k[3], k[4]) for k in com_plano.peca_unidade_claims}
    assert (1, "C99") not in unidades and (1, "C06") in unidades
    assert ("titulo", None, 15) in {k[3:] for k in com_plano.peca_unidade_numeros}
    pasta = tmp_path / "runs" / run_id / "celulas" / "iniciante__artigo"
    assert json.loads((pasta / "estado.json").read_text(encoding="utf-8"))["estado"] == "AVALIANDO"
    assert json.loads((pasta / "peca_1.json").read_text(encoding="utf-8"))["tentativa"] == 1
    (span,) = com_plano.spans.values()
    assert (span.no, span.nivel, span.celula_id, span.tentativa) == ("synthesizer", "iniciante", "iniciante__artigo", 1)


def test_retry_manda_a_peca_anterior_e_o_relatorio_com_raciocinio_baixo(com_plano, run_id, tmp_path):
    llm = cliente_falso([resposta(saida_artigo()), resposta(saida_artigo(linha_fina="Outra linha"))])
    primeira = synth(estado(run_id), config(com_plano, tmp_path, llm))["celula"]
    relatorio = RelatorioCorretivo(certos=["text_size: dentro da faixa"], violacoes=[])
    segunda = synth(estado(run_id, primeira, relatorio), config(com_plano, tmp_path, llm))["celula"]

    provider = llm._provider
    assert provider.raciocinios == [None, "low"]
    assert "# Correção" in provider.prompts[1] and '"titulo": "Selic cai para {{C01.1}}"' in provider.prompts[1]
    assert "{{C09.1}}" in provider.prompts[1]  # o marcador inválido volta como estava
    assert (segunda.tentativa, segunda.peca.payload.linha_fina) == (2, "Outra linha")
    assert {k[2] for k in com_plano.pecas} == {1, 2}


def test_resposta_fora_do_schema_volta_ao_modelo_sem_consumir_tentativa(com_plano, run_id, tmp_path):
    sem_conclusao = json.dumps({"slides": [{"papel": "gancho", "texto": "a", "claim_ids": []},
                                           {"papel": "corpo", "texto": "b", "claim_ids": ["C01"]},
                                           {"papel": "corpo", "texto": "c", "claim_ids": ["C02"]}]})
    slides = [{"papel": "gancho", "texto": "Juros", "claim_ids": []}]
    slides += [{"papel": "corpo", "texto": f"Fato {{{{{c}.1}}}}", "claim_ids": [c]} for c in ("C01", "C02", "C03", "C04")]
    slides += [{"papel": "conclusao", "texto": "Fim", "claim_ids": []}]
    llm = cliente_falso([sem_conclusao, json.dumps({"slides": slides})])
    celula = CelulaState(celula_id="iniciante__carrossel", nivel="iniciante", formato="carrossel")
    celula = synth(estado(run_id, celula), config(com_plano, tmp_path, llm))["celula"]
    assert len(llm._provider.prompts) == 2
    assert celula.tentativa == 1 and celula.plan_contract.passou
    assert celula.peca.payload.slides[1].texto == "Fato 13,75%"


def test_no_sem_plano_devolve_a_falha_no_estado(repositorio, run_id, tmp_path):
    saida = synth(estado(run_id), config(repositorio, tmp_path, cliente_falso([])))
    assert saida["falha"].classe == "terminal" and "plano editorial" in saida["falha"].mensagem
    assert "celula" not in saida


def test_gravar_a_mesma_peca_de_novo_nao_grava_nada_e_outra_e_erro(com_plano, run_id, tmp_path):
    p = pacote_de("iniciante", "artigo")
    peca, _ = montagem.montar(saida_artigo(), p, SPECS["iniciante"], tentativa=1)
    celula = CelulaState(celula_id="iniciante__artigo", nivel="iniciante", formato="artigo", estado="AVALIANDO",
                         tentativa=1)
    persistencia.gravar_peca(com_plano, tmp_path, run_id, peca, celula)
    persistencia.gravar_peca(com_plano, tmp_path, run_id, peca, celula)
    assert len(com_plano.pecas) == 1
    outra, _ = montagem.montar(saida_artigo(linha_fina="Outra"), p, SPECS["iniciante"], tentativa=1)
    with pytest.raises(FalhaTerminal, match="outra peça"):
        persistencia.gravar_peca(com_plano, tmp_path, run_id, outra, celula)
