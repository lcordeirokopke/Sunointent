"""Integração da E0 com a cópia local do Supabase CLI (supabase start).

Cobre o que o repositório em memória não reproduz: constraints, triggers de imutabilidade e o
bucket `documentos`. Pulado se SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY e SUPABASE_DB_URL não
estiverem no ambiente ou se o Supabase local não estiver no ar. Limpeza: supabase db reset.
"""

import os
import secrets
from datetime import datetime, timezone

import pytest

from sunontent import persistencia
from sunontent.ingestao import comum
from sunontent.retentativa import FalhaTerminal

pytestmark = pytest.mark.supabase


@pytest.fixture(scope="module")
def ambiente():
    faltando = [v for v in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_DB_URL") if not os.environ.get(v)]
    if faltando:
        pytest.skip(f"Supabase local não configurado: faltam {', '.join(faltando)}")
    psycopg = pytest.importorskip("psycopg")
    try:
        psycopg.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=3).close()
    except psycopg.OperationalError as erro:
        pytest.skip(f"Supabase local fora do ar: {erro}")
    return os.environ


@pytest.fixture
def repositorio(ambiente):
    from supabase import create_client

    cliente = create_client(ambiente["SUPABASE_URL"], ambiente["SUPABASE_SERVICE_ROLE_KEY"])
    return persistencia.RepositorioSupabase(cliente, ambiente["SUPABASE_DB_URL"])


def novo_run(db_url: str) -> str:
    import psycopg

    run_id = f"copom_20261004T142205Z_{secrets.token_hex(3)}"
    with psycopg.connect(db_url) as conexao:
        conexao.execute(
            "insert into runs (run_id, \"timestamp\", modo, fonte, modelos, prompts, level_specs,"
            " glossario_versao, arquivos_sha256, \"K\")"
            " values (%s, now(), 'normal', 'copom', '{}', '{}', '{}', 1, '{}', 2)",
            (run_id,),
        )
    return run_id


def run_com_referencia(repositorio, db_url: str):
    """Run --dev da CVM com o documento de referência vinculado, como a E1 o encontra, e o documento processado."""
    import json
    from pathlib import Path

    import psycopg

    from sunontent.extracao import leitura
    from sunontent.schemas import DocumentoFonte

    referencia = Path(__file__).parent / "fixtures" / "referencia"
    meta = json.loads((referencia / "cvm.json").read_text(encoding="utf-8"))
    run_id = f"cvm_20261004T142205Z_{secrets.token_hex(3)}"
    with psycopg.connect(db_url) as conexao:
        conexao.execute(
            "insert into runs (run_id, \"timestamp\", modo, fonte, modelos, prompts, level_specs,"
            " glossario_versao, arquivos_sha256, \"K\")"
            " values (%s, now(), 'dev', 'cvm', '{\"extractor\": \"m\"}', '{}', '{}', 1, '{}', 2)",
            (run_id,),
        )
    persistencia.registrar_documento_referencia(
        repositorio, run_id=run_id, fonte="cvm", tipo_documento="cvm_fato_relevante",
        doc_sha256=meta["doc_sha256"], tamanho_bytes=(referencia / "cvm.pdf").stat().st_size,
        doc_id=meta["doc_id"], url_origem=meta["url_origem"],
        coletado_em=datetime(2026, 10, 8, 22, 30, 23, tzinfo=timezone.utc),
    )
    return run_id, leitura.processar(DocumentoFonte(caminho=referencia / "cvm.pdf", **meta), "cvm")


def test_coleta_completa_e_reaproveitamento(repositorio, ambiente, tmp_path):
    dados = b"%PDF-1.7\n% integracao " + secrets.token_bytes(16) + b"\n%%EOF\n"
    sha = comum.sha256_hex(dados)
    coletado_em = datetime(2026, 10, 4, 14, 22, 5, tzinfo=timezone.utc)

    for numero in (1, 2):
        run_id = novo_run(ambiente["SUPABASE_DB_URL"])
        persistencia.enviar_documento(repositorio, "copom", sha, dados)
        coleta = persistencia.registrar_coleta(
            repositorio, run_id=run_id, fonte="copom", tipo_documento="copom_ata",
            doc_sha256=sha, tamanho_bytes=len(dados), doc_id="copom_ata_999",
            url_origem="https://exemplo/ata.pdf", coletado_em=coletado_em,
        )
        assert repositorio.coleta_do_run(run_id) == coleta
        assert repositorio.documento_existe(sha)

    # Objeto existente: o envio direto é aceito como reaproveitamento, sem sobrescrever.
    repositorio.enviar_pdf(persistencia.caminho_storage("copom", sha), dados)

    # Run já vinculado: a transação é recusada.
    with pytest.raises(FalhaTerminal):
        repositorio.registrar_coleta(
            run_id=run_id, fonte="copom", tipo_documento="copom_ata", doc_sha256=sha,
            tamanho_bytes=len(dados), doc_id="copom_ata_999",
            url_origem="https://exemplo/ata.pdf", coletado_em=coletado_em,
        )

    # documentos e coletas são imutáveis.
    import psycopg

    with psycopg.connect(ambiente["SUPABASE_DB_URL"]) as conexao:
        for sql in (
            "update documentos set tamanho_bytes = tamanho_bytes where doc_sha256 = %s",
            "delete from coletas where doc_sha256 = %s",
        ):
            with pytest.raises(psycopg.Error, match="imutável"):
                with conexao.transaction():
                    conexao.execute(sql, (sha,))


def test_documento_processado_grava_paginas_chunks_e_numeros(repositorio, ambiente, tmp_path):
    import psycopg

    run_id, documento = run_com_referencia(repositorio, ambiente["SUPABASE_DB_URL"])

    for _ in range(2):  # a repetição não duplica linhas
        persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, documento)

    with psycopg.connect(ambiente["SUPABASE_DB_URL"]) as conexao:
        contagem = {
            tabela: conexao.execute(f"select count(*) from {tabela} where run_id = %s", (run_id,)).fetchone()[0]
            for tabela in ("documentos_processados", "documento_paginas", "chunks", "numeros")
        }
        metadados = conexao.execute(
            "select emissor, data_documento from documentos_processados where run_id = %s", (run_id,)
        ).fetchone()
        valor = conexao.execute(
            "select valor, unidade from numeros where run_id = %s and bruto = 'R$ 390.000.000,00'", (run_id,)
        ).fetchone()
    assert contagem == {
        "documentos_processados": 1,
        "documento_paginas": len(documento.paginas),
        "chunks": len(documento.chunks),
        "numeros": len(documento.tabela_numeros),
    }
    assert (str(valor[0]), valor[1]) == ("390000000.00", "BRL")
    assert metadados == (documento.emissor, documento.data_documento)


def test_documento_que_nao_e_o_do_run_e_recusado(repositorio, ambiente, tmp_path):
    """A FK composta de documentos_processados recusa a saída da E1 de outro documento."""
    run_id, documento = run_com_referencia(repositorio, ambiente["SUPABASE_DB_URL"])
    outro = documento.model_copy(update={"doc_sha256": "0" * 64})
    with pytest.raises(FalhaTerminal, match="banco recusou"):
        persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, outro)


def test_span_validacao_e_factsheet(repositorio, ambiente, tmp_path):
    import uuid
    from decimal import Decimal

    import psycopg

    from sunontent.extracao import extractor
    from sunontent.schemas import ClaimProposto, FactSheet, Span

    run_id, documento = run_com_referencia(repositorio, ambiente["SUPABASE_DB_URL"])
    persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, documento)

    span = Span(span_id=str(uuid.uuid4()), run_id=run_id, no="extractor", modelo="m", tokens_entrada=100,
                tokens_saida=20, custo_brl=Decimal("0.5"), duracao_ms=10)
    for _ in range(2):  # o span repetido não soma de novo
        persistencia.gravar_span(repositorio, tmp_path, span)

    proposto = ClaimProposto(
        id="C01", chunk_id="K01", texto="A remuneração representou cerca de 11% da receita líquida.",
        literal_fonte="representou aproximadamente 11% (onze por cento) da receita", numeros=["11%"],
        tipo="resultado", tags=["indicador_chave"])
    claims, pendencias = extractor.ancorar(documento, {"C01": proposto})
    assert not pendencias
    factsheet = FactSheet(
        doc_id=documento.doc_id, tipo_documento=documento.tipo_documento, emissor=documento.emissor,
        data_documento=documento.data_documento, claims=claims, tabela_numeros=documento.tabela_numeros,
        assinado=True)
    for _ in range(2):  # a repetição não duplica linhas
        persistencia.gravar_validacao(repositorio, tmp_path, run_id, 1, [("C01", None)])
        persistencia.gravar_factsheet(repositorio, tmp_path, run_id, factsheet)

    with psycopg.connect(ambiente["SUPABASE_DB_URL"]) as conexao:
        custo = conexao.execute(
            "select custo_total_tokens, custo_total_brl from runs where run_id = %s", (run_id,)).fetchone()
        validacoes = conexao.execute(
            "select count(*) from validacoes_ancora where run_id = %s and passou", (run_id,)).fetchone()[0]
        vinculo = conexao.execute(
            "select n.bruto from claim_numeros cn join numeros n on n.id = cn.numero_id where cn.run_id = %s",
            (run_id,)).fetchall()
    assert (custo[0], custo[1]) == (120, Decimal("0.5"))
    assert validacoes == 1
    assert vinculo == [("11%",)]


# Leitura do dashboard (ConsultaSupabase)


def test_consulta_le_o_run_completo_do_dashboard(repositorio, ambiente, tmp_path):
    """Roda as consultas do dashboard contra o schema das migrations: uma coluna renomeada quebra aqui."""
    import uuid
    from decimal import Decimal

    from sunontent.extracao import extractor
    from sunontent.schemas import ClaimProposto, FactSheet, Span

    run_id, documento = run_com_referencia(repositorio, ambiente["SUPABASE_DB_URL"])
    persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, documento)
    for _ in range(2):
        span = Span(span_id=str(uuid.uuid4()), run_id=run_id, no="extractor", modelo="m", tokens_entrada=100,
                    tokens_saida=20, custo_brl=Decimal("0.5"), duracao_ms=10)
        persistencia.gravar_span(repositorio, tmp_path, span)
    proposto = ClaimProposto(
        id="C01", chunk_id="K01", texto="A remuneração representou cerca de 11% da receita líquida.",
        literal_fonte="representou aproximadamente 11% (onze por cento) da receita", numeros=["11%"],
        tipo="resultado", tags=["indicador_chave"])
    claims, _ = extractor.ancorar(documento, {"C01": proposto})
    persistencia.gravar_validacao(repositorio, tmp_path, run_id, 1, [("C01", "motivo de teste")])
    persistencia.gravar_validacao(repositorio, tmp_path, run_id, 2, [("C01", None)])
    persistencia.gravar_factsheet(repositorio, tmp_path, run_id, FactSheet(
        doc_id=documento.doc_id, tipo_documento=documento.tipo_documento, emissor=documento.emissor,
        data_documento=documento.data_documento, claims=claims, tabela_numeros=documento.tabela_numeros,
        assinado=True))
    repositorio.concluir_run(run_id)

    consulta = persistencia.ConsultaSupabase(ambiente["SUPABASE_DB_URL"])
    resumo = next(r for r in consulta.listar_runs() if r["run_id"] == run_id)
    assert (resumo["status"], resumo["claims"], resumo["custo_total_tokens"]) == ("concluido", 1, 240)

    detalhe = consulta.detalhar_run(run_id)
    assert detalhe["manifest"]["modelos"] == {"extractor": "m"} and detalhe["manifest"]["coleta_id"] is None
    assert detalhe["documento"] == {
        "doc_id": documento.doc_id, "doc_sha256": documento.doc_sha256,
        "tipo_documento": "cvm_fato_relevante", "emissor": documento.emissor,
        "data_documento": documento.data_documento, "paginas": len(documento.paginas), "paginas_ocr": 0,
        "chunks": len(documento.chunks), "numeros": len(documento.tabela_numeros),
    }
    (claim,) = detalhe["factsheet"]["claims"]
    assert claim["id"] == "C01" and claim["tags"] == ["indicador_chave"]
    assert [n["bruto"] for n in claim["numeros"]] == ["11%"] and claim["ancora"] == claims[0].ancora.model_dump()
    assert [(v["rodada"], v["claims"][0]["passou"]) for v in detalhe["validacoes"]] == [(1, False), (2, True)]
    assert len(detalhe["spans"]) == 2 and detalhe["spans"][0]["criado_em"] <= detalhe["spans"][1]["criado_em"]
    assert consulta.detalhar_run("cvm_20261004T142205Z_ffffff") is None


def test_consulta_nao_grava(ambiente):
    consulta = persistencia.ConsultaSupabase(ambiente["SUPABASE_DB_URL"])
    with pytest.raises(FalhaTerminal, match="read-only"):
        consulta._executar(lambda conexao: conexao.execute("delete from validacoes_ancora where false"))
