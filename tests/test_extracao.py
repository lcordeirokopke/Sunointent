"""E1: leitura, normalização, chunking, metadados e tabela de números.

Sem LLM e sem rede. Os documentos de tests/fixtures/referencia/ são lidos de verdade; o snapshot
`<fonte>.documento.json` fixa a saída exata (igualdade exata contra fixture). Para regerar depois
de uma mudança intencional: REGERAR_DOCUMENTO=1 pytest tests/test_extracao.py
"""

import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal

import pymupdf
import pytest

from conftest import FIXTURES
from sunontent import persistencia
from sunontent.extracao import chunking, leitura, metadados, normalizacao, pdf
from sunontent.extracao.pdf import Linha, PaginaLida
from sunontent.retentativa import FalhaRetriavel, FalhaTerminal
from sunontent.schemas import Chunk, DocumentoFonte, DocumentoProcessado, Pagina

REFERENCIA = FIXTURES / "referencia"
FONTES = ["copom", "cvm", "b3"]


def documento_fonte(fonte: str) -> DocumentoFonte:
    meta = json.loads((REFERENCIA / f"{fonte}.json").read_text(encoding="utf-8"))
    return DocumentoFonte(caminho=REFERENCIA / f"{fonte}.pdf", **meta)


@pytest.fixture(scope="module")
def processados() -> dict[str, DocumentoProcessado]:
    return {fonte: leitura.processar(documento_fonte(fonte), fonte) for fonte in FONTES}


def linha(texto, x0=0, x1=50, baseline=100.0, tamanho=10.0, y0=90.0, y1=102.0, baseline_fim=None):
    return Linha(texto, x0, x1, y0, y1, baseline, baseline if baseline_fim is None else baseline_fim, tamanho)


# Leitura do PDF


def test_hash_diferente_aborta_com_arquivo_e_hashes(tmp_path):
    copia = tmp_path / "copom.pdf"
    copia.write_bytes((REFERENCIA / "copom.pdf").read_bytes() + b"\n")
    fonte = documento_fonte("copom").model_copy(update={"caminho": copia})
    with pytest.raises(FalhaTerminal) as erro:
        pdf.ler_documento(fonte)
    mensagem = erro.value.mensagem
    assert str(copia) in mensagem and fonte.doc_sha256 in mensagem
    assert hashlib.sha256(copia.read_bytes()).hexdigest() in mensagem
    assert "Apague" in mensagem


def test_arquivo_ausente_aborta_pedindo_nova_coleta(tmp_path):
    fonte = documento_fonte("copom").model_copy(update={"caminho": tmp_path / "nao_existe.pdf"})
    with pytest.raises(FalhaTerminal, match="nao_existe.pdf"):
        pdf.ler_documento(fonte)


def test_pdf_sem_camada_de_texto_aborta(tmp_path):
    arquivo = tmp_path / "escaneado.pdf"
    documento = pymupdf.open()
    documento.new_page()
    documento.save(arquivo)
    dados = arquivo.read_bytes()
    fonte = DocumentoFonte(
        caminho=arquivo, doc_id="x", doc_sha256=hashlib.sha256(dados).hexdigest(), url_origem="u",
        coletado_em=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(FalhaTerminal, match=r"sem camada de texto: \[1\]"):
        pdf.ler_documento(fonte)


def test_arquivo_que_nao_e_pdf_aborta(tmp_path):
    arquivo = tmp_path / "lixo.pdf"
    arquivo.write_bytes(b"isto nao e um pdf")
    fonte = DocumentoFonte(
        caminho=arquivo, doc_id="x", doc_sha256=hashlib.sha256(b"isto nao e um pdf").hexdigest(),
        url_origem="u", coletado_em=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(FalhaTerminal, match="não abre como PDF"):
        pdf.ler_documento(fonte)


# União de fragmentos


def unir(*partes):
    return [l.texto for l in pdf._unir_fragmentos([(1, p) for p in partes])]


def test_fragmentos_de_um_numero_na_mesma_linha_base_se_unem():
    assert unir(linha("recebeu R$31", 0, 50), linha("5,2 milhões", 70, 130)) == ["recebeu R$315,2 milhões"]
    assert unir(linha("alta de 22,", 0, 50), linha("0%", 60, 70)) == ["alta de 22,0%"]
    assert unir(linha("(+2", 0, 30), linha("2%)", 40, 55)) == ["(+22%)"]


def test_rotulo_de_tabela_nao_se_une_ao_valor():
    assert unir(linha("Receita total", 0, 60), linha("3.081,4", 200, 240)) == ["Receita total", "3.081,4"]
    assert unir(linha("(Em R$ milhões, exceto LPA)", 0, 60), linha("2T26", 100, 120)) == [
        "(Em R$ milhões, exceto LPA)", "2T26",
    ]
    assert unir(linha("Fundos Listados", 0, 60), linha("ADTV (R$ milhões)", 70, 130)) == [
        "Fundos Listados", "ADTV (R$ milhões)",
    ]


def test_espaco_real_na_fronteira_impede_a_uniao():
    assert unir(linha("1.93 ", 0, 30), linha("8,6", 40, 50)) == ["1.93 ", "8,6"]
    assert unir(linha("1.93", 0, 30), linha(" 8,6", 40, 50)) == ["1.93", " 8,6"]


def test_palavra_partida_em_minuscula_e_pontuacao_se_unem():
    assert unir(linha("pel", 0, 20), linha("o menor volume", 40, 100)) == ["pelo menor volume"]
    assert unir(linha("R$3 bilhões", 0, 60), linha(", alta", 70, 100)) == ["R$3 bilhões, alta"]


def test_linhas_base_diferentes_nao_se_unem_na_mesma_linha():
    assert unir(linha("abc", 0, 30, baseline=100), linha("def", 40, 70, baseline=120)) == ["abc", "def"]


def test_numero_quebrado_na_quebra_de_linha_se_une():
    assert unir(linha("totalizou R$1", 0, 90, baseline=100), linha(".302,9 milhões", 0, 70, baseline=112)) == [
        "totalizou R$1.302,9 milhões"
    ]
    assert unir(linha("IPCA + 1,", 0, 60, baseline=100), linha("5% ao ano", 0, 50, baseline=112)) == [
        "IPCA + 1,5% ao ano"
    ]


def test_linha_que_termina_em_ano_nao_engole_a_proxima_que_comeca_com_digito():
    assert unir(linha("em 2025", 0, 60, baseline=100), linha("2026 foi", 0, 50, baseline=112)) == [
        "em 2025", "2026 foi",
    ]
    assert unir(linha("em 2025.", 0, 60, baseline=100), linha("2. Item", 0, 50, baseline=112)) == [
        "em 2025.", "2. Item",
    ]


def test_blocos_diferentes_nao_se_unem():
    juntas = pdf._unir_fragmentos([(1, linha("R$31", 0, 50)), (2, linha("5,2", 60, 80))])
    assert [l.texto for l in juntas] == ["R$31", "5,2"]


# Normalização


def pagina_lida(numero, textos, altura=800.0):
    linhas = []
    for texto, y in textos:
        linhas.append(linha(texto, y0=y, y1=y + 10, baseline=y + 8))
    return PaginaLida(numero, altura, linhas)


def test_cabecalho_e_rodape_repetidos_e_numero_de_pagina_saem():
    paginas = [
        pagina_lida(n, [("Relatório Anual 2026", 20), (f"Corpo da página {n}", 300), (f"{n}", 780), ("ri.exemplo.com.br", 790)])
        for n in (1, 2, 3)
    ]
    texto = [p.texto_limpo for p in normalizacao.normalizar(paginas)]
    assert texto == ["Corpo da página 1", "Corpo da página 2", "Corpo da página 3"]


def test_numero_de_pagina_so_sai_na_margem():
    paginas = [pagina_lida(1, [("1", 400), ("Página 1 de 9", 780)])]
    assert normalizacao.normalizar(paginas)[0].texto_limpo == "1"


def test_numero_na_margem_que_nao_e_o_da_pagina_e_dado_da_tabela():
    paginas = [pagina_lida(n, [("corpo", 300), (f"{n}", 780)]) for n in (1, 2, 3)]
    paginas[1].linhas.append(linha("236", y0=750, y1=760))
    assert [p.texto_limpo for p in normalizacao.normalizar(paginas)] == ["corpo", "corpo\n236", "corpo"]


def test_numeracao_que_nao_comeca_na_capa_e_reconhecida():
    paginas = [pagina_lida(n, [("corpo", 300), (f"{n + 1}", 780)]) for n in (1, 2, 3)]
    assert [p.texto_limpo for p in normalizacao.normalizar(paginas)] == ["corpo"] * 3


def test_linha_repetida_no_corpo_nao_e_removida():
    paginas = [pagina_lida(n, [("Aviso legal", 400), (f"texto {n}", 420)]) for n in (1, 2, 3)]
    assert [p.texto_limpo for p in normalizacao.normalizar(paginas)][0] == "Aviso legal\ntexto 1"


def test_palavra_hifenizada_na_quebra_de_linha_e_juntada():
    paginas = [pagina_lida(1, [("a conver-", 300), ("gência da inflação", 312), ("Itaú-", 324), ("Unibanco", 336)])]
    assert normalizacao.normalizar(paginas)[0].texto_limpo == "a convergência da inflação\nItaú-\nUnibanco"


def test_espaco_ligadura_e_caractere_invisivel():
    assert normalizacao.limpar_linha("  e" + chr(0xFB01) + "ciente  aqui" + chr(0xAD) + chr(0x200B) + "  ") == "eficiente aqui"


# Chunking


def paginas_de(*textos):
    return [Pagina(pagina=i, texto_limpo=t) for i, t in enumerate(textos, start=1)]


def test_copom_chunk_por_paragrafo_numerado_e_secao():
    paginas = paginas_de(
        "Ata\nDados da reunião\nA) Conjuntura\n1. Primeiro parágrafo\ncontinua aqui\n2. Segundo",
        "terceira linha do segundo\nB) Riscos\n3. Terceiro\n2. Falso positivo fora de sequência",
    )
    chunks = chunking.fatiar(paginas, "copom_ata")
    assert [(c.secao, c.texto.split("\n")[0]) for c in chunks] == [
        (None, "Ata"),
        ("A) Conjuntura", "A) Conjuntura"),
        ("A) Conjuntura", "2. Segundo"),
        ("B) Riscos", "B) Riscos"),
    ]
    assert chunks[2].texto == "2. Segundo\nterceira linha do segundo"
    assert (chunks[2].pagina_inicio, chunks[2].pagina_fim) == (1, 2)
    assert "2. Falso positivo fora de sequência" in chunks[3].texto


def test_b3_chunk_por_titulo_em_maiusculas():
    paginas = paginas_de(
        "RESULTADOS 2T26\nDESTAQUES DO TRIMESTRE\n▪ Receita\n2T26/2T25\nMENSAGEM DA ADMINISTRAÇÃO\nTexto\nDESPESAS\nOUTRO TÍTULO\nCorpo",
    )
    chunks = chunking.fatiar(paginas, "b3_release")
    assert [c.secao for c in chunks] == [None, "DESTAQUES DO TRIMESTRE", "MENSAGEM DA ADMINISTRAÇÃO", "DESPESAS"]
    assert chunks[-1].texto == "DESPESAS\nOUTRO TÍTULO\nCorpo"


def test_cvm_sem_secoes_e_um_chunk():
    chunks = chunking.fatiar(paginas_de("FATO RELEVANTE\ntexto"), "cvm_fato_relevante")
    assert len(chunks) == 1 and chunks[0].secao is None and chunks[0].texto == "FATO RELEVANTE\ntexto"


@pytest.mark.parametrize("fonte", FONTES)
def test_chunks_cobrem_o_texto_inteiro_sem_sobreposicao(processados, fonte):
    doc = processados[fonte]
    linhas_dos_chunks = [l for c in doc.chunks for l in c.texto.split("\n")]
    linhas_do_texto = [l for p in doc.paginas for l in p.texto_limpo.split("\n") if l]
    assert linhas_dos_chunks == linhas_do_texto
    posicoes = [(c.pagina_inicio, c.offset_inicio) for c in doc.chunks]
    assert posicoes == sorted(posicoes) and len(set(posicoes)) == len(posicoes)
    assert [c.chunk_id for c in doc.chunks] == [f"K{n:02d}" for n in range(1, len(doc.chunks) + 1)]


def test_chunk_inconsistente_com_o_texto_e_recusado():
    with pytest.raises(ValueError, match="não confere"):
        DocumentoProcessado(
            doc_id="d", doc_sha256="0" * 64, tipo_documento="cvm_fato_relevante", emissor="e",
            data_documento=date(2026, 1, 1), paginas=paginas_de("abc def"),
            chunks=[Chunk(chunk_id="K01", pagina_inicio=1, offset_inicio=0, pagina_fim=1, offset_fim=3, texto="xyz")],
            tabela_numeros=[],
        )


# Metadados


def test_metadados_dos_documentos_de_referencia(processados):
    copom, cvm, b3 = processados["copom"], processados["cvm"], processados["b3"]
    assert (copom.tipo_documento, copom.data_documento) == ("copom_ata", date(2026, 9, 16))
    assert copom.emissor.startswith("Comitê de Política Monetária")
    assert (cvm.tipo_documento, cvm.data_documento) == ("cvm_fato_relevante", date(2026, 10, 2))
    assert cvm.emissor == "NOVA TRANSPORTADORA DO SUDESTE S.A. - NTS"
    assert (b3.tipo_documento, b3.data_documento) == ("b3_release", date(2026, 6, 30))
    assert (copom.doc_id, copom.doc_sha256) == (documento_fonte("copom").doc_id, documento_fonte("copom").doc_sha256)


@pytest.mark.parametrize(
    "tipo, texto",
    [("copom_ata", "sem data"), ("cvm_fato_relevante", "NTS\nsem assinatura"), ("b3_release", "sem data-base")],
)
def test_metadado_ausente_aborta(tipo, texto):
    with pytest.raises(FalhaTerminal, match="E1: não encontrei"):
        metadados.extrair(tipo, paginas_de(texto))


# Números dos documentos reais, conferidos à mão no PDF


def numeros_de(doc, unidade=None):
    return [(n.bruto, n.valor, n.unidade) for n in doc.tabela_numeros if unidade in (None, n.unidade)]


def test_b3_valores_que_o_pdf_parte_em_fragmentos(processados):
    brutos = {b: (v, u) for b, v, u in numeros_de(processados["b3"])}
    assert brutos["R$315,2 milhões"] == (Decimal("315.2"), "BRL_mi")
    assert brutos["R$526,8 milhões"] == (Decimal("526.8"), "BRL_mi")
    assert brutos["R$1.302,9 milhões"] == (Decimal("1302.9"), "BRL_mi")
    assert brutos["R$1.938,6 milhões"] == (Decimal("1938.6"), "BRL_mi")
    assert brutos["22,0%"] == (Decimal("22.0"), "pct")
    assert brutos["1.938,6"] == (Decimal("1938.6"), "contagem")
    assert ("2026", Decimal("2026"), "data") in numeros_de(processados["b3"])
    # nenhum fragmento solto do que o PDF partiu: "202 6", "R $1,3", "R$3, 0"
    assert not [b for b in brutos if "202 6" in b or b.startswith("R $") or ", " in b]


def test_b3_destaques(processados):
    brutos = {b: (v, u) for b, v, u in numeros_de(processados["b3"])}
    assert brutos["R$3,1 bilhões"] == (Decimal("3100"), "BRL_mi")
    assert brutos["R$31,3 bilhões"] == (Decimal("31300"), "BRL_mi")
    assert brutos["12,2%"] == (Decimal("12.2"), "pct")
    assert brutos["-3,8%"] == (Decimal("-3.8"), "pct")
    assert brutos["R$0,28"] == (Decimal("0.28"), "BRL")
    assert brutos["-0,7 p.p."] == (Decimal("-0.7"), "pp")
    assert brutos["47 bps"] == (Decimal("0.47"), "pp")


def test_copom_decisao_e_datas(processados):
    doc = processados["copom"]
    assert [b for b, _, _ in numeros_de(doc, "pct")] == [
        "4,9%", "4,3%", "3,2%", "2%", "5,2%", "3,9%", "3,2%", "13,75%",
    ]
    assert ("13,75%", Decimal("13.75"), "pct") in numeros_de(doc, "pct")
    assert numeros_de(doc, "BRL") == [("R$5,15/US$", Decimal("5.15"), "BRL")]
    datas = [b for b, _, u in numeros_de(doc) if u == "data"]
    assert datas[:3] == ["15", "16", "2026"]
    assert ("280ª", Decimal("280"), "data") in numeros_de(doc)
    # a numeração dos parágrafos (1. a 20.) não é número do texto
    assert [b for b, _, u in numeros_de(doc) if u == "contagem"][:3] == ["281ª", "8º", "20º"]


def test_cvm_valor_em_reais_e_identificadores_fora(processados):
    doc = processados["cvm"]
    assert ("R$ 390.000.000,00", Decimal("390000000.00"), "BRL") in numeros_de(doc)
    assert ("11%", Decimal("11"), "pct") in numeros_de(doc)
    brutos = [b for b, _, _ in numeros_de(doc)]
    for identificador in ("04.992.714", "0001", "84", "22.210", "901", "33.3", "0026999"):
        assert identificador not in brutos
    assert ("6.404", Decimal("6404"), "contagem") in numeros_de(doc)


@pytest.mark.parametrize("fonte", FONTES)
def test_ancoras_unicas_e_batem_com_o_texto(processados, fonte):
    doc = processados[fonte]
    texto = {p.pagina: p.texto_limpo for p in doc.paginas}
    posicoes = [(n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim) for n in doc.tabela_numeros]
    assert len(posicoes) == len(set(posicoes))
    for n in doc.tabela_numeros:
        a = n.ancora
        assert texto[a.pagina][a.offset_inicio : a.offset_fim] == n.bruto
        assert isinstance(n.valor, Decimal)
    assert posicoes == sorted(posicoes)


def test_numero_em_formato_nao_previsto_aborta_com_a_pagina():
    with pytest.raises(FalhaTerminal, match=r"página 2.*1,234\.56"):
        leitura.tabela_de_numeros(paginas_de("tudo certo 12,5%", "a taxa foi 1,234.56 ontem"))


# Snapshot: igualdade exata contra fixture


def compactar(doc: DocumentoProcessado) -> dict:
    return {
        "doc_id": doc.doc_id,
        "doc_sha256": doc.doc_sha256,
        "tipo_documento": doc.tipo_documento,
        "emissor": doc.emissor,
        "data_documento": doc.data_documento.isoformat(),
        "paginas": [{"pagina": p.pagina, "ocr": p.ocr, "texto_limpo": p.texto_limpo} for p in doc.paginas],
        "chunks": [
            [c.chunk_id, c.secao, c.pagina_inicio, c.offset_inicio, c.pagina_fim, c.offset_fim]
            for c in doc.chunks
        ],
        "numeros": [
            [n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim, n.bruto, n.unidade, str(n.valor)]
            for n in doc.tabela_numeros
        ],
    }


@pytest.mark.parametrize("fonte", FONTES)
def test_saida_igual_ao_snapshot(processados, fonte):
    arquivo = REFERENCIA / f"{fonte}.documento.json"
    atual = compactar(processados[fonte])
    if os.environ.get("REGERAR_DOCUMENTO"):
        arquivo.write_text(json.dumps(atual, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    esperado = json.loads(arquivo.read_text(encoding="utf-8"))
    assert atual == esperado


# Gravação


def vincular(repositorio, run_id, doc):
    """O que a E0 ou a referência fazem antes da E1: vincular o documento ao run."""
    repositorio.runs[run_id].update(doc_id=doc.doc_id, doc_sha256=doc.doc_sha256)
    return doc


def test_gravacao_no_repositorio_e_na_copia_local(processados, repositorio, run_id, tmp_path):
    doc = vincular(repositorio, run_id, processados["copom"])
    caminho = persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, doc)
    assert caminho == tmp_path / "runs" / run_id / "documento.json"
    assert repositorio.documentos_processados[run_id] == {
        "doc_id": doc.doc_id, "doc_sha256": doc.doc_sha256, "tipo_documento": doc.tipo_documento,
        "emissor": doc.emissor, "data_documento": doc.data_documento,
    }
    assert len(repositorio.paginas) == len(doc.paginas)
    assert len(repositorio.chunks) == len(doc.chunks)
    assert len(repositorio.numeros) == len(doc.tabela_numeros)
    local = DocumentoProcessado.model_validate_json(caminho.read_text(encoding="utf-8"))
    assert local == doc


def test_gravacao_repetida_nao_duplica(processados, repositorio, run_id, tmp_path):
    doc = vincular(repositorio, run_id, processados["cvm"])
    for _ in range(2):
        persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, doc)
    assert len(repositorio.paginas) == len(doc.paginas) and len(repositorio.numeros) == len(doc.tabela_numeros)


def test_documento_que_nao_e_o_do_run_e_recusado(processados, repositorio, run_id, tmp_path):
    vincular(repositorio, run_id, processados["copom"])
    with pytest.raises(FalhaTerminal, match="não é o vinculado"):
        persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, processados["cvm"])
    assert not repositorio.documentos_processados and not (tmp_path / "runs").exists()


def test_supabase_fora_antes_da_copia_local_nao_grava_localmente(processados, repositorio, run_id, tmp_path):
    repositorio.falhas["gravar_documento_processado"] = [FalhaRetriavel("fora")] * 10
    with pytest.raises(FalhaTerminal, match="tentativas esgotadas"):
        persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, processados["cvm"])
    assert not (tmp_path / "runs").exists() and not repositorio.paginas


def test_falha_passageira_do_supabase_e_repetida(processados, repositorio, run_id, tmp_path):
    repositorio.falhas["gravar_documento_processado"] = [FalhaRetriavel("oscilou")]
    persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, vincular(repositorio, run_id, processados["cvm"]))
    assert repositorio.chamadas["gravar_documento_processado"] == 2 and repositorio.paginas
