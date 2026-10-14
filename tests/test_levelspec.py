"""LevelSpec da E4: leitura e as 4 regras de validação de config/levels/ (docs/fluxos/plano-editorial.md)."""

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


def test_le_os_valores_da_spec():
    specs = levelspec.carregar_todos(RAIZ)
    assert list(specs) == ["iniciante", "intermediario", "avancado"]
    assert specs["iniciante"].tags_descartaveis == ["metodologico"]
    assert specs["iniciante"].tags_priorizadas == ["impacto_pratico"]
    assert specs["intermediario"].tags_priorizadas == ["indicador_chave", "contexto_macro"]
    assert specs["avancado"].tags_priorizadas == ["indicador_chave", "metodologico", "governanca"]
    assert [s.max_claims for s in specs.values()] == [5, 7, 9]
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


@pytest.mark.parametrize("valor", [2, 0, "5", 5.0, True])
def test_max_claims_inteiro_maior_ou_igual_a_3(raiz, valor):
    reescrever(raiz, "avancado", max_claims=valor)
    with pytest.raises(FalhaTerminal, match="max_claims"):
        levelspec.carregar(raiz, "avancado")


def test_max_claims_3_e_aceito(raiz):
    reescrever(raiz, "avancado", max_claims=3)
    assert levelspec.carregar(raiz, "avancado").max_claims == 3


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
