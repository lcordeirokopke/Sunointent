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
    import json
    from pathlib import Path

    import psycopg

    from sunontent.extracao import leitura
    from sunontent.schemas import DocumentoFonte

    referencia = Path(__file__).parent / "fixtures" / "referencia"
    meta = json.loads((referencia / "cvm.json").read_text(encoding="utf-8"))
    documento = leitura.processar(DocumentoFonte(caminho=referencia / "cvm.pdf", **meta), "cvm")
    run_id = novo_run(ambiente["SUPABASE_DB_URL"])

    for _ in range(2):  # a repetição não duplica linhas
        persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, documento)

    with psycopg.connect(ambiente["SUPABASE_DB_URL"]) as conexao:
        contagem = {
            tabela: conexao.execute(f"select count(*) from {tabela} where run_id = %s", (run_id,)).fetchone()[0]
            for tabela in ("documento_paginas", "chunks", "numeros")
        }
        valor = conexao.execute(
            "select valor, unidade from numeros where run_id = %s and bruto = 'R$ 390.000.000,00'", (run_id,)
        ).fetchone()
    assert contagem == {
        "documento_paginas": len(documento.paginas),
        "chunks": len(documento.chunks),
        "numeros": len(documento.tabela_numeros),
    }
    assert (str(valor[0]), valor[1]) == ("390000000.00", "BRL")


def test_span_validacao_e_factsheet(repositorio, ambiente, tmp_path):
    import json
    import uuid
    from decimal import Decimal
    from pathlib import Path

    import psycopg

    from sunontent.extracao import extractor, leitura
    from sunontent.schemas import ClaimProposto, DocumentoFonte, FactSheet, Span

    referencia = Path(__file__).parent / "fixtures" / "referencia"
    meta = json.loads((referencia / "cvm.json").read_text(encoding="utf-8"))
    documento = leitura.processar(DocumentoFonte(caminho=referencia / "cvm.pdf", **meta), "cvm")
    run_id = novo_run(ambiente["SUPABASE_DB_URL"])
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
