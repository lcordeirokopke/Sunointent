"""E4: pré-filtro, complemento e gate do plano (docs/arquiteturas.md, E4.1 e E4.3), como funções puras, e o
planejador (E4.2) com o LLM simulado."""

import json
from datetime import date
from pathlib import Path

import pytest

from conftest import RepositorioMemoria, cliente_falso
from sunontent import glossario as modulo_glossario
from sunontent import persistencia
from sunontent.adaptacao import complemento, gate, planejador, prefiltro
from sunontent.levelspec import LevelSpec
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import (
    FORMATOS,
    NIVEIS,
    Ancora,
    CelulaState,
    Claim,
    FactSheet,
    ProblemaPlano,
    SaidaPlanejador,
    celula_id,
)
from test_glossario import analogia, termo

SPECS = {
    "iniciante": LevelSpec(nivel="iniciante", versao=1, foco="bolso", tags_priorizadas=["impacto_pratico"],
                           tags_descartaveis=["metodologico"], max_claims=3),
    "intermediario": LevelSpec(nivel="intermediario", versao=1, foco="alocação", tags_priorizadas=[],
                               tags_descartaveis=[], max_claims=4),
    "avancado": LevelSpec(nivel="avancado", versao=1, foco="análise", tags_priorizadas=[],
                          tags_descartaveis=[], max_claims=5),
}

GLOSSARIO = modulo_glossario.validar({"versao": 1, "termos": [
    termo("juros", "juros", nivel="iniciante"),
    termo("selic", "Selic", ["taxa Selic"], analogias=[analogia("A Selic é o preço do dinheiro.")]),
    termo("ipca", "IPCA", []),  # sem analogia: vai para a lista do planejador
    termo("hiato", "hiato do produto", [], nivel="avancado"),
]})


def claim(id, texto, tipo="resultado", tags=("indicador_chave",)):
    return Claim(id=id, texto=texto, numeros=[], tipo=tipo, tags=list(tags),
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


def nivel(nome, claims, descartes=(), analogias=(), tese_ids=None):
    return {
        "nivel": nome,
        "descartes": [dict(zip(("claim_id", "motivo", "relacionado"), d)) for d in descartes],
        "claims": [{"claim_id": c, "papel": "principal" if i == 0 else "apoio"} for i, c in enumerate(claims)],
        "tese": f"Tese do {nome}.",
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


def problemas(p):
    return [(x.regra, x.nivel) for x in gate.conferir(p, CLAIMS, PREFILTRO, SPECS, GLOSSARIO)]


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


def test_tese_fica_so_com_os_claims_usados():
    s = saida(inter=nivel("intermediario", ["C01", "C02"], [("C03", "fora_do_foco", None)],
                          tese_ids=["C01", "C03", "C05", "C99"]))
    assert plano(s).nivel("intermediario").tese_claim_ids == ["C01"]


def test_lista_de_termos_sem_analogia_para_o_planejador():
    assert complemento.termos_sem_analogia(GLOSSARIO, CLAIMS, PREFILTRO.candidatos["iniciante"]) == ["ipca", "hiato"]


def test_retirar_ids_inexistentes():
    s = saida(nucleo=("C01", "C99"), ini=nivel("iniciante", ["C01", "C98", "C02"],
                                                [("C97", "fora_do_foco", None), ("C03", "redundante", "C96")],
                                                analogias=[("IPCA", "Termômetro.")], tese_ids=["C01", "C95"]))
    p = complemento.retirar_ids_inexistentes(plano(s), CLAIMS)
    ini = p.nivel("iniciante")
    assert p.nucleo == ["C01"]
    assert [c.claim_id for c in ini.claims] == ["C01", "C02"]
    assert [(d.claim_id, d.relacionado) for d in ini.descartes if d.origem == "planejador"] == [("C03", None)]
    assert ini.tese_claim_ids == ["C01"]


def test_retirar_pode_esvaziar_o_nucleo():
    p = complemento.retirar_ids_inexistentes(plano(saida(nucleo=("C99",))), CLAIMS)
    assert p.nucleo == []


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
    assert "só com as tags metodologico já saíram deste nível" in prompt
    assert "C01 | decisao | indicador_chave, impacto_pratico | candidato nos 3 níveis | O Copom reduziu a Selic." in prompt
    assert "C04 | condicionante | metodologico | candidato só em intermediario, avancado |" in prompt
    assert "C06 | risco | impacto_pratico | candidato nos 3 níveis |" in prompt
    assert "- IPCA: Definição simples." in prompt and "- hiato do produto: Definição simples." in prompt
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
    assert [(s.no, s.nivel, s.celula_id) for s in spans] == [("adapter", None, None)]


def test_correcao_traz_so_os_problemas_e_o_plano_enviado():
    llm = cliente_falso([saida().model_dump_json()])
    anterior = saida(ini=nivel("iniciante", ["C01", "C02", "C03", "C06"]))
    problema = "[acima_do_limite] iniciante: 4 claims usados, acima do máximo de 3"
    chamar(llm, anterior=anterior, problemas=[problema])
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
    repo.factsheets[RUN] = FACTSHEET
    return repo


def test_rodadas_do_gate_no_banco_e_na_copia_local(repo_com_factsheet, tmp_path):
    problemas = [ProblemaPlano(regra="acima_do_limite", nivel="iniciante", detalhe="4 claims"),
                 ProblemaPlano(regra="id_inexistente", detalhe="C99")]
    persistencia.gravar_validacao_plano(repo_com_factsheet, tmp_path, RUN, 1, problemas)
    persistencia.gravar_validacao_plano(repo_com_factsheet, tmp_path, RUN, 1, problemas)  # repetir não duplica
    persistencia.gravar_validacao_plano(repo_com_factsheet, tmp_path, RUN, 2, [])
    assert sorted(repo_com_factsheet.validacoes_plano) == [(RUN, 1, 1), (RUN, 1, 2)]
    local = json.loads((tmp_path / "runs" / RUN / "validacoes_plano" / "1.json").read_text(encoding="utf-8"))
    assert local == {"rodada": 1, "problemas": [
        {"regra": "acima_do_limite", "nivel": "iniciante", "detalhe": "4 claims"},
        {"regra": "id_inexistente", "nivel": None, "detalhe": "C99"},
    ]}
    passou = json.loads((tmp_path / "runs" / RUN / "validacoes_plano" / "2.json").read_text(encoding="utf-8"))
    assert passou == {"rodada": 2, "problemas": []}


def test_reiniciar_apaga_as_rodadas_do_run(repo_com_factsheet, tmp_path):
    problema = [ProblemaPlano(regra="id_inexistente", detalhe="C99")]
    persistencia.gravar_validacao_plano(repo_com_factsheet, tmp_path, RUN, 1, problema)
    repo_com_factsheet.validacoes_plano[("outro_run", 1, 1)] = problema[0]
    persistencia.reiniciar_validacoes_plano(repo_com_factsheet, tmp_path, RUN)
    assert list(repo_com_factsheet.validacoes_plano) == [("outro_run", 1, 1)]
    assert list((tmp_path / "runs" / RUN / "validacoes_plano").glob("*.json")) == []
    persistencia.reiniciar_validacoes_plano(repo_com_factsheet, tmp_path / "sem_pasta", RUN)  # sem cópia local


def test_plano_e_9_celulas_pendentes(repo_com_factsheet, tmp_path):
    p = plano()
    persistencia.gravar_plano(repo_com_factsheet, tmp_path, RUN, p, celulas())
    assert repo_com_factsheet.planos[RUN] == p
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


def test_celula_state_confere_o_id():
    with pytest.raises(ValueError, match="não confere"):
        CelulaState(celula_id="iniciante__artigo", nivel="avancado", formato="artigo")
