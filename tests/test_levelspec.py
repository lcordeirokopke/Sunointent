"""LevelSpec: leitura e validação de config/levels/, com o contrato de formato (docs/fluxos/plano-editorial.md)."""

import shutil
from pathlib import Path

import pytest
import yaml

from sunontent import levelspec
from sunontent.retentativa import FalhaTerminal

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def raiz(tmp_path):
    shutil.copytree(RAIZ / "config" / "levels", tmp_path / "config" / "levels")
    return tmp_path


def reescrever(raiz, arquivo, **campos):
    caminho = raiz / "config" / "levels" / f"{arquivo}.yaml"
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    dados.update(campos)
    caminho.write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")


def reescrever_formato(raiz, arquivo, formato, **campos):
    caminho = raiz / "config" / "levels" / f"{arquivo}.yaml"
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    dados["formatos"][formato].update(campos)
    caminho.write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")


def test_le_os_valores_da_spec():
    specs = levelspec.carregar_todos(RAIZ)
    assert list(specs) == ["iniciante", "intermediario", "avancado"]
    assert specs["iniciante"].tags_descartaveis == ["metodologico"]
    assert specs["iniciante"].tags_priorizadas == ["impacto_pratico"]
    assert specs["intermediario"].tags_priorizadas == ["indicador_chave", "contexto_macro"]
    assert specs["avancado"].tags_priorizadas == ["indicador_chave", "metodologico", "governanca"]
    assert [s.max_claims for s in specs.values()] == [5, 7, 9]  # o do artigo
    assert [(s.formatos.carrossel.max_claims, s.formatos.roteiro.max_claims) for s in specs.values()] == [
        (4, 3), (5, 5), (5, 5)]
    assert [(s.formatos.artigo.palavras.min, s.formatos.artigo.palavras.max) for s in specs.values()] == [
        (350, 550), (450, 750), (600, 900)]
    for s in specs.values():
        assert (s.formatos.carrossel.slides.min, s.formatos.carrossel.slides.max) == (5, 7)
        assert (s.formatos.roteiro.duracao_s, s.formatos.roteiro.palavras_por_segundo) == (60, 2.5)
        assert (s.peso_papel.principal, s.peso_papel.apoio, s.peso_papel.contexto) == (3, 2, 1)
        assert (s.formatos.roteiro.abertura_s, s.formatos.roteiro.fechamento_s) == (6, 6)
        caracteres = s.formatos.carrossel.caracteres
        assert (caracteres.gancho, caracteres.corpo, caracteres.conclusao) == (120, 220, 160)
    assert [s.versao for s in specs.values()] == [5, 4, 5]
    assert specs["intermediario"].foco == "o que o fato muda para quem já investe"
    assert specs["avancado"].foco == "análise técnica e institucional"


def test_nivel_diferente_do_nome_do_arquivo(raiz):
    reescrever(raiz, "avancado", nivel="iniciante")
    with pytest.raises(FalhaTerminal, match=r"config/levels/avancado.yaml: `nivel` é 'iniciante'"):
        levelspec.carregar_todos(raiz)


def test_tag_fora_do_vocabulario(raiz):
    reescrever(raiz, "intermediario", tags_priorizadas=["indicador_chave", "dividendos"])
    with pytest.raises(FalhaTerminal, match="intermediario.yaml: LevelSpec inválido: tags_priorizadas"):
        levelspec.carregar(raiz, "intermediario")


def test_tag_nas_duas_listas(raiz):
    reescrever(raiz, "iniciante", tags_priorizadas=["impacto_pratico", "metodologico"])
    with pytest.raises(FalhaTerminal, match="em tags_priorizadas e em tags_descartaveis: metodologico"):
        levelspec.carregar(raiz, "iniciante")


def test_tag_descartavel_so_metodologico(raiz):
    reescrever(raiz, "iniciante", tags_descartaveis=["metodologico", "contexto_macro"])
    with pytest.raises(FalhaTerminal, match="só aceita metodologico .encontrado: contexto_macro"):
        levelspec.carregar(raiz, "iniciante")


@pytest.mark.parametrize("formato", ["artigo", "carrossel", "roteiro"])
@pytest.mark.parametrize("valor", [2, 0, "5", 5.0, True])
def test_max_claims_inteiro_maior_ou_igual_a_3(raiz, formato, valor):
    reescrever_formato(raiz, "avancado", formato, max_claims=valor)
    with pytest.raises(FalhaTerminal, match=f"formatos.{formato}.max_claims"):
        levelspec.carregar(raiz, "avancado")


def test_campo_desconhecido_num_formato_e_recusado(raiz):
    reescrever_formato(raiz, "iniciante", "artigo", secoes=4)
    with pytest.raises(FalhaTerminal, match="formatos.artigo.secoes"):
        levelspec.carregar(raiz, "iniciante")


def test_max_claims_3_e_aceito(raiz):
    for formato in ("artigo", "carrossel", "roteiro"):
        reescrever_formato(raiz, "avancado", formato, max_claims=3)
    assert levelspec.carregar(raiz, "avancado").max_claims == 3


def test_max_claims_no_topo_e_recusado(raiz):
    reescrever(raiz, "avancado", max_claims=9)
    with pytest.raises(FalhaTerminal, match="max_claims: Extra inputs"):
        levelspec.carregar(raiz, "avancado")


def test_formato_menor_nao_usa_mais_claims_que_o_maior(raiz):
    reescrever_formato(raiz, "iniciante", "roteiro", max_claims=5)
    with pytest.raises(FalhaTerminal, match="artigo >= carrossel >= roteiro .encontrado: 5, 4 e 5"):
        levelspec.carregar(raiz, "iniciante")


@pytest.mark.parametrize("formato, campos, mensagem", [
    ("artigo", {"palavras": {"min": 600, "max": 500}}, "min .600. maior que max .500."),
    ("carrossel", {"slides": {"min": 2, "max": 7}}, "pelo menos 3 slides"),
    ("carrossel", {"caracteres": {"gancho": 120, "corpo": 220}}, "conclusao"),
    ("roteiro", {"abertura_s": 30, "fechamento_s": 30}, "menor que duracao_s"),
    ("roteiro", {"palavras_por_segundo": 0.0}, "palavras_por_segundo"),
])
def test_contrato_de_formato_invalido(raiz, formato, campos, mensagem):
    reescrever_formato(raiz, "intermediario", formato, **campos)
    with pytest.raises(FalhaTerminal, match=mensagem):
        levelspec.carregar(raiz, "intermediario")


def test_peso_do_papel_completo_e_positivo(raiz):
    reescrever(raiz, "avancado", peso_papel={"principal": 3, "apoio": 0, "contexto": 1})
    with pytest.raises(FalhaTerminal, match="peso_papel.apoio"):
        levelspec.carregar(raiz, "avancado")


def test_campo_desconhecido_e_recusado(raiz):
    reescrever(raiz, "iniciante", densidade={"min": 60})
    with pytest.raises(FalhaTerminal, match="densidade"):
        levelspec.carregar(raiz, "iniciante")


def test_campo_ausente_e_recusado(raiz):
    caminho = raiz / "config" / "levels" / "iniciante.yaml"
    dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    del dados["foco"]
    caminho.write_text(yaml.safe_dump(dados), encoding="utf-8")
    with pytest.raises(FalhaTerminal, match="foco"):
        levelspec.carregar(raiz, "iniciante")


def test_arquivo_ausente_ou_vazio(raiz):
    (raiz / "config" / "levels" / "iniciante.yaml").write_text("", encoding="utf-8")
    with pytest.raises(FalhaTerminal, match="iniciante.yaml: arquivo vazio"):
        levelspec.carregar(raiz, "iniciante")
    (raiz / "config" / "levels" / "avancado.yaml").unlink()
    with pytest.raises(FalhaTerminal, match="avancado.yaml: não foi possível ler"):
        levelspec.carregar(raiz, "avancado")


# Bloco `analogia`, avisos, ordem entre níveis e instrucoes_redator


def test_iniciante_le_os_marcadores_de_analogia():
    spec = levelspec.carregar(RAIZ, "iniciante")
    assert spec.analogia.marcadores == ["é como", "funciona como", "imagine que", "na prática"]
    assert levelspec.carregar(RAIZ, "intermediario").analogia is None
    assert levelspec.carregar(RAIZ, "avancado").analogia is None


def test_analogia_obrigatoria_no_iniciante(raiz):
    reescrever(raiz, "iniciante", analogia=None)
    with pytest.raises(FalhaTerminal, match="`analogia` é obrigatório no Iniciante"):
        levelspec.carregar(raiz, "iniciante")


@pytest.mark.parametrize("nivel", ["intermediario", "avancado"])
def test_analogia_proibida_fora_do_iniciante(raiz, nivel):
    reescrever(raiz, nivel, analogia={"marcadores": ["é como"]})
    with pytest.raises(FalhaTerminal, match="`analogia` só vale no Iniciante"):
        levelspec.carregar(raiz, nivel)


@pytest.mark.parametrize("marcadores, mensagem", [
    ([], "marcadores"),
    (["como"], "pelo menos duas palavras"),
    (["é como", "É  como"], "repetidos"),
    (["é como", " "], "pelo menos duas palavras"),
])
def test_marcadores_invalidos(raiz, marcadores, mensagem):
    reescrever(raiz, "iniciante", analogia={"marcadores": marcadores})
    with pytest.raises(FalhaTerminal, match=mensagem):
        levelspec.carregar(raiz, "iniciante")


def test_campo_desconhecido_no_bloco_analogia(raiz):
    reescrever(raiz, "iniciante", analogia={"marcadores": ["é como"], "janela": 40})
    with pytest.raises(FalhaTerminal, match="janela"):
        levelspec.carregar(raiz, "iniciante")


def test_aviso_quando_o_carrossel_tem_mais_claims_que_slides_de_corpo(raiz):
    reescrever_formato(raiz, "avancado", "carrossel", max_claims=6)
    with pytest.warns(UserWarning, match=r"carrossel.max_claims \(6\).*slides.max - 2 = 5"):
        levelspec.carregar(raiz, "avancado")  # 6 claims e no máximo 7 slides


def test_sem_aviso_quando_os_claims_cabem_nos_slides_de_corpo(raiz, recwarn):
    levelspec.carregar(raiz, "iniciante")  # 4 claims, 5 slides de corpo possíveis
    levelspec.carregar(raiz, "intermediario")  # 5 claims, exatamente 5
    levelspec.carregar(raiz, "avancado")  # 5 claims, exatamente 5
    assert not [w for w in recwarn if issubclass(w.category, UserWarning)]


def test_palavras_minimas_do_artigo_crescem_com_o_nivel(raiz):
    reescrever_formato(raiz, "intermediario", "artigo", palavras={"min": 340, "max": 750})
    with pytest.raises(FalhaTerminal, match=r"palavras.min precisa crescer.*intermediario 340"):
        levelspec.carregar_todos(raiz)


def test_palavras_minimas_iguais_sao_aceitas(raiz):
    reescrever_formato(raiz, "intermediario", "artigo", palavras={"min": 350, "max": 750})
    assert levelspec.carregar_todos(raiz)["intermediario"].formatos.artigo.palavras.min == 350


def test_instrucoes_do_artigo_no_iniciante():
    spec = levelspec.carregar(RAIZ, "iniciante")
    instrucoes = levelspec.instrucoes_redator(spec, "artigo")
    assert instrucoes.faixa_esperada == "de 350 a 550 palavras no texto das seções"
    assert "de 350 a 550 palavras" in instrucoes.orcamento and "usando até 5 claims" in instrucoes.orcamento
    assert '"é como", "funciona como", "imagine que", "na prática"' in instrucoes.marcadores
    assert "mesma seção, slide ou cena" in instrucoes.marcadores


def test_instrucoes_do_carrossel():
    spec = levelspec.carregar(RAIZ, "iniciante")
    instrucoes = levelspec.instrucoes_redator(spec, "carrossel")
    assert instrucoes.faixa_esperada == "até 120 caracteres no gancho, 220 em cada slide de corpo e 160 na conclusão"
    assert "de 5 a 7 slides, contando o gancho e a conclusão" in instrucoes.orcamento
    assert "usando até 4 claims" in instrucoes.orcamento


def test_instrucoes_do_roteiro_usam_virgula_decimal():
    spec = levelspec.carregar(RAIZ, "intermediario")
    instrucoes = levelspec.instrucoes_redator(spec, "roteiro")
    assert instrucoes.faixa_esperada == "até 2,5 palavras por segundo em cada cena e até 150 palavras em 60 segundos"
    assert "cerca de 6 s" in instrucoes.orcamento and "até 5 claims" in instrucoes.orcamento
    assert "Comece em 0 s" in instrucoes.orcamento


@pytest.mark.parametrize("nivel", ["intermediario", "avancado"])
def test_so_o_iniciante_tem_marcadores_nas_instrucoes(nivel):
    spec = levelspec.carregar(RAIZ, nivel)
    assert all(levelspec.instrucoes_redator(spec, f).marcadores == "" for f in ("artigo", "carrossel", "roteiro"))


# Bloco `legibilidade` (M3)


def test_so_o_iniciante_tem_piso_de_ifl():
    specs = levelspec.carregar_todos(RAIZ)
    piso = specs["iniciante"].legibilidade
    assert (piso.ifl_minimo.artigo, piso.ifl_minimo.carrossel, piso.ifl_minimo.roteiro) == (30, 45, 45)
    assert piso.frase_max_palavras == 18
    assert specs["intermediario"].legibilidade is None and specs["avancado"].legibilidade is None


def test_piso_de_ifl_pede_os_tres_formatos(raiz):
    reescrever(raiz, "iniciante", legibilidade={"ifl_minimo": {"artigo": 30, "carrossel": 45}, "frase_max_palavras": 18})
    with pytest.raises(FalhaTerminal, match="legibilidade.ifl_minimo.roteiro"):
        levelspec.carregar(raiz, "iniciante")


def test_meta_de_frase_precisa_de_pelo_menos_cinco_palavras(raiz):
    reescrever(raiz, "iniciante", legibilidade={"ifl_minimo": {"artigo": 30, "carrossel": 45, "roteiro": 45}, "frase_max_palavras": 3})
    with pytest.raises(FalhaTerminal, match="legibilidade.frase_max_palavras"):
        levelspec.carregar(raiz, "iniciante")


def test_nivel_sem_o_bloco_so_mede(raiz):
    reescrever(raiz, "avancado", legibilidade=None)
    assert levelspec.carregar(raiz, "avancado").legibilidade is None


def test_instrucoes_trazem_a_meta_de_frase_so_no_nivel_que_a_tem():
    iniciante = levelspec.instrucoes_redator(levelspec.carregar(RAIZ, "iniciante"), "artigo")
    assert iniciante.legibilidade.startswith("Escreva frases de até 18 palavras")
    for nivel in ("intermediario", "avancado"):
        assert levelspec.instrucoes_redator(levelspec.carregar(RAIZ, nivel), "artigo").legibilidade == ""


# Bloco `vocabulario` (prompt do redator)


def test_intermediario_explica_o_jargao_avancado_e_o_avancado_nao_explica():
    specs = levelspec.carregar_todos(RAIZ)
    assert specs["iniciante"].vocabulario is None
    assert specs["intermediario"].vocabulario.explicar_acima_de == "intermediario"
    assert specs["avancado"].vocabulario.explicar_acima_de == "avancado"


def test_vocabulario_proibido_no_iniciante(raiz):
    reescrever(raiz, "iniciante", vocabulario={"explicar_acima_de": "iniciante"})
    with pytest.raises(FalhaTerminal, match="`vocabulario` não vale no Iniciante"):
        levelspec.carregar(raiz, "iniciante")


def test_vocabulario_nao_explica_abaixo_do_proprio_nivel(raiz):
    reescrever(raiz, "avancado", vocabulario={"explicar_acima_de": "intermediario"})
    with pytest.raises(FalhaTerminal, match="explicar_acima_de"):
        levelspec.carregar(raiz, "avancado")


@pytest.mark.parametrize("bloco, mensagem", [
    ({"explicar_acima_de": "especialista"}, "vocabulario.explicar_acima_de"),
    ({"explicar_acima_de": "intermediario", "extra": 1}, "vocabulario.extra"),
])
def test_vocabulario_invalido(raiz, bloco, mensagem):
    reescrever(raiz, "intermediario", vocabulario=bloco)
    with pytest.raises(FalhaTerminal, match=mensagem):
        levelspec.carregar(raiz, "intermediario")


def test_nivel_sem_o_bloco_de_vocabulario_carrega(raiz):
    reescrever(raiz, "intermediario", vocabulario=None)
    spec = levelspec.carregar(raiz, "intermediario")
    assert spec.vocabulario is None
    assert levelspec.instrucoes_redator(spec, "artigo").vocabulario == ""


def test_instrucoes_de_vocabulario_por_nivel():
    def vocabulario(nivel):
        return levelspec.instrucoes_redator(levelspec.carregar(RAIZ, nivel), "carrossel").vocabulario

    assert vocabulario("iniciante") == ""
    assert vocabulario("intermediario").startswith("Use sem explicar o vocabulário padrão de quem investe")
    assert "explique-o em poucas palavras" in vocabulario("intermediario")
    assert vocabulario("avancado").startswith("Escreva para um leitor profissional: use o jargão técnico sem explicar")
