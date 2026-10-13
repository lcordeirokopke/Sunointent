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
from sunontent import numeros, persistencia
from sunontent.extracao import chunking, leitura, metadados, normalizacao, pdf, tabelas
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


PT = "A companhia informa que o prazo para exercício do direito de retirada termina em outubro. " * 3
EN = "The company informs the market that the period for the exercise of the right is open and will end in October. " * 3


def test_bloco_final_em_ingles_e_descartado_e_pagina_vazia_nao_interrompe():
    paginas = [Pagina(pagina=i, texto_limpo=t) for i, t in enumerate([PT, PT, EN, "", EN], start=1)]
    assert [p.pagina for p in normalizacao.sem_versao_em_ingles(paginas)] == [1, 2]


def test_ingles_no_meio_documento_todo_em_ingles_e_pouco_ingles_ficam():
    meio = [Pagina(pagina=i, texto_limpo=t) for i, t in enumerate([PT, EN, PT], start=1)]
    assert normalizacao.sem_versao_em_ingles(meio) == meio
    tudo = [Pagina(pagina=i, texto_limpo=EN) for i in (1, 2)]
    assert normalizacao.sem_versao_em_ingles(tudo) == tudo
    termo = [Pagina(pagina=1, texto_limpo=PT), Pagina(pagina=2, texto_limpo="Forward guidance of the Board.")]
    assert normalizacao.sem_versao_em_ingles(termo) == termo


def test_versao_em_ingles_dos_documentos_de_referencia(processados, capsys):
    assert [len(processados[f].paginas) for f in ("copom", "cvm", "b3")] == [6, 3, 12]
    leitura.processar(documento_fonte("cvm"), "cvm")
    assert capsys.readouterr().out == "E1: páginas 4 a 6 descartadas: versão em inglês\n"


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
    assert (cvm.tipo_documento, cvm.data_documento) == ("cvm_fato_relevante", date(2026, 10, 8))
    assert cvm.emissor == (
        "COMPANHIA DE SANEAMENTO BÁSICO DO ESTADO DE SÃO PAULO – SABESP; "
        "EMPRESA METROPOLITANA DE ÁGUAS E ENERGIA S.A. - EMAE"
    )
    assert (b3.tipo_documento, b3.data_documento) == ("b3_release", date(2026, 6, 30))
    assert (copom.doc_id, copom.doc_sha256) == (documento_fonte("copom").doc_id, documento_fonte("copom").doc_sha256)


@pytest.mark.parametrize(
    "tipo, texto",
    [("copom_ata", "sem data"), ("cvm_fato_relevante", "EMISSOR S.A.\nCNPJ nº 1\nFATO RELEVANTE\nsem assinatura"), ("b3_release", "sem data-base")],
)
def test_metadado_ausente_aborta(tipo, texto):
    with pytest.raises(FalhaTerminal, match="E1: não encontrei"):
        metadados.extrair(tipo, paginas_de(texto))


def test_emissor_cvm_de_uma_companhia_ignora_linha_sem_letra():
    texto = (
        ",\nNOVA TRANSPORTADORA DO SUDESTE S.A. - NTS\nCNPJ nº 04.992.714/0001-84\nNIRE 33.3.0026999-1\n"
        "FATO RELEVANTE\nA Nova Transportadora do Sudeste S.A.\nRio de Janeiro, 02 de outubro de 2026."
    )
    emissor, _ = metadados.extrair("cvm_fato_relevante", paginas_de(texto))
    assert emissor == "NOVA TRANSPORTADORA DO SUDESTE S.A. - NTS"


ASSINATURA = "\nSão Paulo, 08 de outubro de 2026."


def test_emissor_cvm_com_titulo_em_ingles():
    texto = "EMISSOR S.A.\nPublicly Held Company\nCNPJ/MF No. 1\nJOINT MATERIAL FACT\nThe company informs." + ASSINATURA
    emissor, _ = metadados.extrair("cvm_fato_relevante", paginas_de(texto))
    assert emissor == "EMISSOR S.A."


@pytest.mark.parametrize("texto", [
    ",\nEMISSOR S.A.\nFATO RELEVANTE\nA EMISSOR S.A. informa.",  # sem registro antes do título
    ",\nEMISSOR S.A.\nComunicado ao Mercado\nCompanhia Aberta e outras coisas do corpo\nCNPJ nº 1",  # sem título
])
def test_emissor_cvm_nao_identificado_usa_a_primeira_linha_com_aviso(texto, capsys):
    emissor, _ = metadados.extrair("cvm_fato_relevante", paginas_de(texto + ASSINATURA))
    assert emissor == "EMISSOR S.A."
    assert capsys.readouterr().out == (
        "E1: emissor não identificado no cabeçalho; usando a primeira linha: 'EMISSOR S.A.'\n"
    )


def test_emissor_cvm_pagina_sem_letra_aborta():
    with pytest.raises(FalhaTerminal, match="não tem texto"):
        metadados.extrair("cvm_fato_relevante", paginas_de(",\n123", "São Paulo, 08 de outubro de 2026."))


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
    assert brutos["1.938,6"] == (Decimal("1938.6"), "BRL_mi")  # célula de tabela "(Em R$ milhões)"
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
        # tabela "Variação do IPCA acumulada em quatro trimestres (%)"
        "5,2", "3,9", "3,2", "5,2", "4,1", "3,2", "5,3", "3,5", "3,2",
    ]
    assert ("13,75%", Decimal("13.75"), "pct") in numeros_de(doc, "pct")
    assert numeros_de(doc, "BRL") == [("R$5,15/US$", Decimal("5.15"), "BRL")]
    datas = [b for b, _, u in numeros_de(doc) if u == "data"]
    assert datas[:3] == ["15", "16", "2026"]
    assert ("280ª", Decimal("280"), "data") in numeros_de(doc)
    # a numeração dos parágrafos (1. a 20.) não é número do texto
    assert [b for b, _, u in numeros_de(doc) if u == "contagem"][:3] == ["281ª", "8º", "20º"]


def test_cvm_identificadores_fora(processados):
    doc = processados["cvm"]
    brutos = [b for b, _, _ in numeros_de(doc)]
    for identificador in ("43.776.517", "02.302.101", "0001", "80", "42"):
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


# Unidade das células de tabela


def unidades(texto):
    return [(a.bruto, a.valor, a.unidade) for a in tabelas.aplicar(texto, numeros.varrer(texto))]


def test_celula_recebe_a_unidade_do_cabecalho_e_texto_corrido_fica():
    texto = "(Em R$ milhões)\n2T26\n2T25\nReceita total\n3.081,4\n(8,6)\n12,2%\nA receita cresceu 12,2% e somou 3.081,4."
    assert unidades(texto) == [
        ("2", Decimal("2"), "data"), ("26", Decimal("26"), "data"),
        ("2", Decimal("2"), "data"), ("25", Decimal("25"), "data"),
        ("3.081,4", Decimal("3081.4"), "BRL_mi"), ("8,6", Decimal("8.6"), "BRL_mi"), ("12,2%", Decimal("12.2"), "pct"),
        ("12,2%", Decimal("12.2"), "pct"), ("3.081,4", Decimal("3081.4"), "contagem"),
    ]


def test_escala_do_cabecalho_e_unidade_do_rotulo_da_linha():
    texto = "(Em R$ milhares)\nReceita\n3.081.392\nRPC média (R$)\n0,786\nADV (milhares de contratos)\n5.909\nMargem (bps)\n3,074"
    assert [(b, v, u) for b, v, u in unidades(texto)] == [
        ("3.081.392", Decimal("3081.392"), "BRL_mi"), ("0,786", Decimal("0.786"), "BRL"),
        ("5.909", Decimal("5909000"), "contagem"), ("3,074", Decimal("0.03074"), "pp"),
    ]


def test_novo_cabecalho_de_colunas_encerra_a_tabela_anterior_e_parentese_que_nao_e_unidade():
    texto = "(Em R$ milhões)\n2T26\nSoluções (Trillia)\n315,2\n(+/-) Efeitos\n8,6\nOutra tabela\n2T26\nContratos\n61"
    assert [(b, u) for b, _, u in unidades(texto) if u != "data"] == [
        ("315,2", "BRL_mi"), ("8,6", "BRL_mi"), ("61", "contagem"),
    ]


def test_quantidade_e_lpa_nao_herdam_a_unidade():
    texto = (
        "(Em R$ milhões, exceto LPA)\nLucro líquido\n1.701,2\nLucro por ação básico\n0,34\n"
        "Número de pregões\n61\nNúmero de investidores (média em milhares)\n3.458"
    )
    assert [(b, v, u) for b, v, u in unidades(texto)] == [
        ("1.701,2", Decimal("1701.2"), "BRL_mi"), ("0,34", Decimal("0.34"), "contagem"),
        ("61", Decimal("61"), "contagem"), ("3.458", Decimal("3458000"), "contagem"),
    ]


def test_celulas_de_tabela_dos_documentos_de_referencia(processados):
    copom = {(n.ancora.pagina, n.bruto): (n.valor, n.unidade) for n in processados["copom"].tabela_numeros}
    assert copom[(6, "4,1")] == (Decimal("4.1"), "pct")  # IPCA livres 2027
    b3 = processados["b3"].tabela_numeros
    assert next(n for n in b3 if n.bruto == "3.081,4" and n.ancora.pagina == 4).unidade == "BRL_mi"
    assert next(n for n in b3 if n.bruto == "3.081.392").valor == Decimal("3081.392")
    assert next(n for n in b3 if n.bruto == "0,786").unidade == "BRL"  # RPC média (R$)


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
