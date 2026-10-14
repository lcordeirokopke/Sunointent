"""Glossário: leitura, validações, regra de casamento e analogia do glossário (docs/fluxos/plano-editorial.md,
Glossário, e docs/glossario.md, Casamento no texto)."""

import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from sunontent import glossario
from sunontent.retentativa import FalhaTerminal

RAIZ = Path(__file__).resolve().parent.parent
REFERENCIA = RAIZ / "tests" / "fixtures" / "referencia"


def termo(id, termo, variantes=(), nivel="intermediario", ambiguo=False, analogias=None, **extra):
    base = {
        "id": id, "termo": termo, "variantes": list(variantes), "categoria": "juros", "nivel_minimo": nivel,
        "definicao_simples": "Definição simples.", "definicao_tecnica": "Definição técnica.",
        "analogias": analogias if analogias is not None else [], "ambiguo": ambiguo,
        "fonte": {"nome": "Banco Central do Brasil", "url": "https://www.bcb.gov.br"},
    }
    base.update(extra)
    return base


def analogia(texto="Como um termostato.", status="rascunho", limite="Não vale para tudo."):
    return {"texto": texto, "limite": limite, "status": status}


TERMOS = [
    termo("juros", "juros", ["juro", "taxa de juros"], nivel="iniciante"),
    termo("selic", "Selic", ["taxa Selic", "taxa básica de juros"], analogias=[analogia()]),
    termo("cdi", "CDI", ["Certificado de Depósito Interbancário"]),
    termo("ipca", "IPCA", []),
    termo("ipca_15", "IPCA-15", []),
    termo("embi", "EMBI+", []),
    termo("acao", "ação", ["ações", "ações ordinárias", "mercado de ações"], ambiguo=True, nivel="iniciante"),
    termo("hiato", "hiato do produto", [], nivel="avancado",
          analogias=[analogia("Primeira."), analogia("Segunda.", "aprovada"), analogia("Terceira.", "aprovada")]),
    termo("politica_monetaria", "política monetária", []),
]


def montar(termos=None, versao=1):
    return glossario.validar({"versao": versao, "termos": copy.deepcopy(termos if termos is not None else TERMOS)})


def casados(texto, g=None):
    g = g or montar()
    return [(o.id, texto[o.inicio:o.fim]) for o in g.casar(texto)]


# Leitura e validação


def test_glossario_do_repositorio_e_valido():
    g = glossario.carregar(RAIZ)
    assert g.versao == yaml.safe_load((RAIZ / "config" / "glossario.yaml").read_text(encoding="utf-8"))["versao"]
    assert len(g.termos) > 200


@pytest.mark.parametrize(
    "mudanca, mensagem",
    [
        ({"categoria": "cripto"}, r"selic.categoria"),
        ({"nivel_minimo": "basico"}, r"selic.nivel_minimo"),
        ({"analogias": [analogia(status="revisada")]}, r"selic.analogias.0.status"),
        ({"analogias": [{"texto": "Sem limite.", "status": "rascunho"}]}, r"selic.analogias.0.limite"),
        ({"analogias": [analogia(limite="")]}, r"selic.analogias.0.limite"),
        ({"ambiguo": "sim"}, r"selic.ambiguo"),
        ({"fonte": {"nome": "BCB"}}, r"selic.fonte.url"),
        ({"extra": 1}, r"selic.extra"),
        ({"variantes": [True]}, r"selic.variantes.0"),  # ON sem aspas vira booleano no YAML
    ],
)
def test_campo_invalido_e_terminal(mudanca, mensagem):
    termos = copy.deepcopy(TERMOS)
    termos[1].update(mudanca)
    with pytest.raises(FalhaTerminal, match=f"config/glossario.yaml: glossário inválido: .*{mensagem}"):
        montar(termos)


def test_campo_ausente_e_terminal():
    termos = copy.deepcopy(TERMOS)
    del termos[1]["analogias"]
    with pytest.raises(FalhaTerminal, match="selic.analogias: Field required"):
        montar(termos)


def test_id_repetido():
    with pytest.raises(FalhaTerminal, match="id repetido: selic"):
        montar(TERMOS + [termo("selic", "Selic over")])


def test_forma_em_duas_entradas_sem_caixa_e_sem_acento():
    with pytest.raises(FalhaTerminal, match=r"forma 'Politica Monetaria' em politica_monetaria e em pm"):
        montar(TERMOS + [termo("pm", "Politica Monetaria")])


def test_forma_repetida_na_mesma_entrada():
    with pytest.raises(FalhaTerminal, match=r"forma 'SELIC' repetida em selic"):
        montar([termo("selic", "Selic", ["SELIC"])])


@pytest.mark.parametrize(
    "mudanca, campo",
    [
        ({"definicao_simples": "Taxa de 2 em 2 anos."}, "definicao_simples"),
        ({"definicao_tecnica": "Definida pela Lei 9.069."}, "definicao_tecnica"),
        ({"analogias": [analogia(texto="Como 3 bancos.")]}, "analogias#1.texto"),
        ({"analogias": [analogia(limite="Vale até 10 anos.")]}, "analogias#1.limite"),
    ],
)
def test_digito_em_definicao_ou_analogia(mudanca, campo):
    termos = copy.deepcopy(TERMOS)
    termos[1].update(mudanca)
    with pytest.raises(FalhaTerminal, match=f"selic.{re.escape(campo)} tem dígito"):
        montar(termos)


def test_arquivo_vazio_e_versao_invalida(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "glossario.yaml").write_text("", encoding="utf-8")
    with pytest.raises(FalhaTerminal, match="arquivo vazio"):
        glossario.carregar(tmp_path)
    with pytest.raises(FalhaTerminal, match="versao"):
        glossario.validar({"versao": 0, "termos": []})
    with pytest.raises(FalhaTerminal, match="não foi possível ler"):
        glossario.carregar(tmp_path / "nao_existe")


# Casamento


def test_ignora_maiusculas_e_acentos():
    assert casados("A POLITICA MONETARIA e a política Monetária.") == [
        ("politica_monetaria", "POLITICA MONETARIA"), ("politica_monetaria", "política Monetária"),
    ]


def test_posicoes_no_texto_original_com_acentos():
    texto = "Ação: a política monetária do Copom."
    (ocorrencia,) = montar().casar(texto)
    assert (ocorrencia.inicio, ocorrencia.fim) == (8, 26)
    assert texto[ocorrencia.inicio:ocorrencia.fim] == "política monetária"
    assert ocorrencia.forma == "política monetária"


def test_fronteira_de_palavra():
    assert casados("jurosos, ajuros, CDIs, IPCA15 e Selicão") == []
    assert casados("(juros), CDI. IPCA") == [("juros", "juros"), ("cdi", "CDI"), ("ipca", "IPCA")]


def test_sigla_so_com_a_caixa_exata():
    assert casados("o cdi, o Cdi e o CDI") == [("cdi", "CDI")]
    assert casados("ipca-15 e IPCA-15") == [("ipca_15", "IPCA-15")]
    assert glossario.e_sigla("NTN-B") and glossario.e_sigla("EMBI+") and glossario.e_sigla("IPCA-15")
    assert not glossario.e_sigla("Selic") and not glossario.e_sigla("15")


def test_sigla_terminada_em_sinal():
    assert casados("O EMBI+ subiu; EMBI+X não.") == [("embi", "EMBI+")]


def test_flexao_fora_de_variantes_nao_casa():
    assert casados("hiatos do produto") == []
    assert casados("os juro e os juros") == [("juros", "juro"), ("juros", "juros")]


def test_ambiguo_so_pelas_formas_de_mais_de_uma_palavra():
    assert casados("A ação subiu, as ações também.") == []
    assert casados("As ações ordinárias e o mercado de ações.") == [
        ("acao", "ações ordinárias"), ("acao", "mercado de ações"),
    ]


def test_a_forma_mais_longa_vence():
    assert casados("A taxa Selic e a taxa básica de juros.") == [
        ("selic", "taxa Selic"), ("selic", "taxa básica de juros"),
    ]
    assert casados("a taxa de juros") == [("juros", "taxa de juros")]


def test_quebra_de_linha_entre_palavras_da_forma():
    assert casados("a taxa\nbásica  de juros") == [("selic", "taxa\nbásica  de juros")]


def test_marcador_de_numero_fica_fora():
    g = montar(TERMOS + [termo("c01", "C01")])
    assert casados("A Selic foi a {{C01.1}} e C01.", g) == [("selic", "Selic"), ("c01", "C01")]


def test_termos_em_e_filtro_por_nivel():
    g = montar()
    ids = g.termos_em("Os juros, a Selic, o hiato do produto e a Selic de novo.")
    assert ids == ["juros", "selic", "hiato"]
    assert g.acima_de(ids, "iniciante") == ["selic", "hiato"]
    assert g.acima_de(ids, "intermediario") == ["hiato"]
    assert g.acima_de(ids, "avancado") == []


def test_termo_da_forma():
    g = montar()
    assert g.termo_da_forma("taxa basica de juros") == "selic"
    assert g.termo_da_forma(" SELIC ") == "selic"
    assert g.termo_da_forma("dólar") is None


# Analogia do glossário


def test_analogia_primeira_aprovada():
    a = montar().analogia("hiato")
    assert (a.glossario_ref, a.explicacao, a.revisada, a.origem, a.termo) == (
        "hiato#2", "Segunda.", True, "glossario", "hiato do produto",
    )


def test_analogia_sem_aprovada_usa_o_primeiro_rascunho():
    termos = copy.deepcopy(TERMOS)
    termos[1]["analogias"] = [analogia("Um."), analogia("Dois.")]
    a = montar(termos).analogia("selic")
    assert (a.glossario_ref, a.explicacao, a.revisada) == ("selic#1", "Um.", False)


def test_termo_sem_analogia():
    assert montar().analogia("cdi") is None


# Flexões nos claims das referências


def _flexoes(g: glossario.Glossario, texto: str) -> list[tuple[str, str]]:
    """Trechos que parecem uma forma do termo com outra terminação em alguma palavra (plural, gênero) e
    que o casador não reconhece. Heurística só para revisão humana do glossário."""
    dobrado = glossario.dobrar(texto)
    reconhecidos = g.casar(texto)
    achados = []
    for termo_id, padrao in _padroes_de_flexao(g):
        for m in padrao.finditer(dobrado):
            achado = " ".join(texto[m.start():m.end()].split())
            if g.termo_da_forma(achado) is not None:
                continue
            if any(o.inicio < m.end() and m.start() < o.fim for o in reconhecidos):
                continue
            achados.append((termo_id, achado))
    return achados


_PADROES_DE_FLEXAO: dict[int, list] = {}


def _padroes_de_flexao(g: glossario.Glossario) -> list[tuple[str, re.Pattern]]:
    """Cada forma com as 2 últimas letras de cada palavra livres (até 4 letras no lugar)."""
    if id(g) not in _PADROES_DE_FLEXAO:
        padroes = []
        for t in g.termos.values():
            for forma in t.formas:
                palavras = glossario.dobrar(forma).split()
                if glossario.e_sigla(forma) or (t.ambiguo and len(palavras) < 2):
                    continue
                radicais = [p[: max(3, len(p) - 2)] if len(p) >= 4 else p for p in palavras]
                padroes.append((t.id, re.compile(
                    r"(?<!\w)" + r"\s+".join(re.escape(r) + r"\w{0,4}" for r in radicais) + r"(?!\w)"
                )))
        _PADROES_DE_FLEXAO[id(g)] = padroes
    return _PADROES_DE_FLEXAO[id(g)]


def test_flexoes_fora_de_variantes_nos_claims_das_referencias(capsys):
    """Lista os termos escritos nos claims com flexão que não está em `variantes`, para completar o
    glossário. Não falha: a lista é para revisão humana (rode com -s para ver)."""
    g = glossario.carregar(RAIZ)
    por_termo: dict[str, set[str]] = {}
    for fonte in ("copom", "cvm", "b3"):
        dados = json.loads((REFERENCIA / f"{fonte}.claims.json").read_text(encoding="utf-8"))
        for claim in dados["claims"]:
            for termo_id, achado in _flexoes(g, claim["texto"]):
                por_termo.setdefault(termo_id, set()).add(achado)
    with capsys.disabled():
        print(f"\nflexões fora de variantes: {len(por_termo)} termos")
        for termo_id in sorted(por_termo):
            print(f"  {termo_id}: {', '.join(sorted(por_termo[termo_id]))}")
    assert all(isinstance(v, set) for v in por_termo.values())


def test_heuristica_de_flexao_acha_plural_fora_de_variantes():
    assert _flexoes(montar(), "Os hiatos do produto fecharam.") == [("hiato", "hiatos do produto")]
    assert _flexoes(montar(), "A taxa Selic e os juros.") == []
