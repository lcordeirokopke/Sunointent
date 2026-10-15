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
        (4, 3), (5, 5), (6, 5)]
    assert [(s.formatos.artigo.palavras.min, s.formatos.artigo.palavras.max) for s in specs.values()] == [
        (350, 550), (450, 750), (600, 900)]
    for s in specs.values():
        assert (s.formatos.carrossel.slides.min, s.formatos.carrossel.slides.max) == (5, 7)
        assert (s.formatos.roteiro.duracao_s, s.formatos.roteiro.palavras_por_segundo) == (60, 2.5)
        assert (s.peso_papel.principal, s.peso_papel.apoio, s.peso_papel.contexto) == (3, 2, 1)
        assert (s.formatos.roteiro.abertura_s, s.formatos.roteiro.fechamento_s) == (6, 6)
        caracteres = s.formatos.carrossel.caracteres
        assert (caracteres.gancho, caracteres.corpo, caracteres.conclusao) == (120, 220, 160)
        assert s.versao == 3
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
    reescrever(raiz, "iniciante", legibilidade={"min": 60})
    with pytest.raises(FalhaTerminal, match="legibilidade"):
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
