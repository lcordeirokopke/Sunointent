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
            "select valor, unidade from numeros where run_id = %s and bruto = '6.404'", (run_id,)
        ).fetchone()
    assert contagem == {
        "documentos_processados": 1,
        "documento_paginas": len(documento.paginas),
        "chunks": len(documento.chunks),
        "numeros": len(documento.tabela_numeros),
    }
    assert (str(valor[0]), valor[1]) == ("6404", "contagem")
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
        id="C01", chunk_id="K01", texto="O fato relevante atende à Resolução CVM nº 44.",
        literal_fonte="CVM nº 44, de 23 de agosto de 2021", numeros=["44"],
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
    assert vinculo == [("44",)]


def test_plano_editorial_rodadas_e_celulas(repositorio, ambiente, tmp_path):
    import psycopg

    from sunontent.extracao import extractor
    from sunontent.schemas import (
        FORMATOS, NIVEIS, Analogia, CelulaState, ClaimPlanejado, ClaimProposto, Descarte, FactSheet,
        PlanoEditorial, PlanoNivel, ProblemaPlano, celula_id,
    )

    run_id, documento = run_com_referencia(repositorio, ambiente["SUPABASE_DB_URL"])
    persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, documento)
    proposto = ClaimProposto(
        id="C01", chunk_id="K01", texto="O fato relevante atende à Resolução CVM nº 44.",
        literal_fonte="CVM nº 44, de 23 de agosto de 2021", numeros=["44"], tipo="resultado", tags=["governanca"])
    claims, _ = extractor.ancorar(documento, {"C01": proposto})
    factsheet = FactSheet(
        doc_id=documento.doc_id, tipo_documento=documento.tipo_documento, emissor=documento.emissor,
        data_documento=documento.data_documento, claims=claims, tabela_numeros=documento.tabela_numeros,
        assinado=True)

    plano = PlanoEditorial(nucleo=["C01"], rodadas=2, aviso=None, niveis=[
        PlanoNivel(nivel=n, descartes=[Descarte(claim_id="C02", motivo="secundario", origem="padrao")],
                   claims=[ClaimPlanejado(claim_id="C01", papel="principal", atribuir_fonte=False)],
                   tese="Tese.", tese_claim_ids=["C01"],
                   analogias=[Analogia(termo="CVM", explicacao="Como um árbitro.", origem="nova", revisada=False)]
                   if n == "iniciante" else [])
        for n in NIVEIS])
    celulas = [CelulaState(celula_id=celula_id(n, f), nivel=n, formato=f) for n in NIVEIS for f in FORMATOS]

    # O plano exige o FactSheet do run.
    with pytest.raises(FalhaTerminal):
        persistencia.gravar_plano(repositorio, tmp_path, run_id, plano, celulas)
    persistencia.gravar_factsheet(repositorio, tmp_path, run_id, factsheet)

    rodada_antiga = [ProblemaPlano(regra="contraditorio", nivel="avancado", detalhe="antiga")]
    persistencia.gravar_validacao_plano(repositorio, tmp_path, run_id, 1, rodada_antiga)
    persistencia.reiniciar_validacoes_plano(repositorio, tmp_path, run_id)
    problemas = [ProblemaPlano(regra="nucleo_ausente", nivel="avancado", detalhe="C01 fora do avançado"),
                 ProblemaPlano(regra="id_inexistente", detalhe="C99")]
    assert persistencia.carregar_plano(repositorio, tmp_path, run_id) is None
    for _ in range(2):  # a repetição não duplica linhas
        persistencia.gravar_validacao_plano(repositorio, tmp_path, run_id, 1, problemas)
        persistencia.gravar_validacao_plano(repositorio, tmp_path, run_id, 2, [])
        persistencia.gravar_plano(repositorio, tmp_path, run_id, plano, celulas)

    with psycopg.connect(ambiente["SUPABASE_DB_URL"]) as conexao:
        rodadas = conexao.execute(
            "select rodada, ordem, nivel::text, regra::text, detalhe from validacoes_plano where run_id = %s"
            " order by rodada, ordem", (run_id,)).fetchall()
        estados = conexao.execute(
            "select estado::text, tentativa, count(*) from celulas where run_id = %s group by 1, 2",
            (run_id,)).fetchall()
    assert rodadas == [(1, 1, "avancado", "nucleo_ausente", "C01 fora do avançado"),
                       (1, 2, None, "id_inexistente", "C99")]
    assert estados == [("PENDENTE", 0, 9)]

    lido, celulas_lidas = persistencia.carregar_plano(repositorio, tmp_path, run_id)
    assert lido == plano
    assert sorted(c.celula_id for c in celulas_lidas) == sorted(c.celula_id for c in celulas)


def test_span_do_adapter_aceita_nivel_nulo(repositorio, ambiente, tmp_path):
    """O planejador da E4 faz 1 chamada para os 3 níveis: o span do adapter não tem nível. O extractor nunca tem."""
    import uuid
    from decimal import Decimal

    from sunontent.schemas import Span

    run_id, _ = run_com_referencia(repositorio, ambiente["SUPABASE_DB_URL"])

    def span(no, nivel=None):
        return Span(span_id=str(uuid.uuid4()), run_id=run_id, no=no, nivel=nivel, modelo="m", tokens_entrada=1,
                    tokens_saida=1, custo_brl=Decimal("0"), duracao_ms=1)

    persistencia.gravar_span(repositorio, tmp_path, span("adapter"))
    persistencia.gravar_span(repositorio, tmp_path, span("adapter", "iniciante"))
    with pytest.raises(FalhaTerminal):
        persistencia.gravar_span(repositorio, tmp_path, span("extractor", "iniciante"))


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
        id="C01", chunk_id="K01", texto="O fato relevante atende à Resolução CVM nº 44.",
        literal_fonte="CVM nº 44, de 23 de agosto de 2021", numeros=["44"],
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
    assert [n["bruto"] for n in claim["numeros"]] == ["44"] and claim["ancora"] == claims[0].ancora.model_dump()
    assert [(v["rodada"], v["claims"][0]["passou"]) for v in detalhe["validacoes"]] == [(1, False), (2, True)]
    assert len(detalhe["spans"]) == 2 and detalhe["spans"][0]["criado_em"] <= detalhe["spans"][1]["criado_em"]
    assert consulta.detalhar_run("cvm_20261004T142205Z_ffffff") is None


def test_quarentena_e_revisao_humana(repositorio, ambiente, tmp_path):
    """Número que a E1 não leu: o claim vai para a quarentena e o humano o aceita com o número."""
    import psycopg

    from sunontent.extracao import extractor, quarentena
    from sunontent.schemas import ClaimProposto, ClaimQuarentena, FactSheet

    run_id, documento = run_com_referencia(repositorio, ambiente["SUPABASE_DB_URL"])
    persistencia.gravar_documento_processado(repositorio, tmp_path, run_id, documento)
    with psycopg.connect(ambiente["SUPABASE_DB_URL"]) as conexao:  # a E1 "não leu" o 44
        conexao.execute("delete from numeros where run_id = %s and bruto = '44'", (run_id,))

    informacoes = ClaimProposto(
        id="C01", chunk_id="K01", texto="Informações adicionais sobre a Incorporação de Ações serão divulgadas.",
        literal_fonte="Informações adicionais acerca da Incorporação de Ações serão tempestivamente", numeros=[], tipo="risco", tags=["governanca"])
    resolucao = ClaimProposto(
        id="C02", chunk_id="K01", texto="O fato relevante atende à Resolução CVM nº 44.",
        literal_fonte="CVM nº 44, de 23 de agosto de 2021", numeros=["44"],
        tipo="resultado", tags=["indicador_chave"])
    claims, _ = extractor.ancorar(documento, {"C01": informacoes})
    itens = [ClaimQuarentena(proposto=resolucao, motivo="o número '44' não está dentro do literal_fonte", rodada=3)]
    for _ in range(2):  # a repetição não duplica linhas
        persistencia.gravar_quarentena(repositorio, tmp_path, run_id, itens)
    persistencia.gravar_factsheet(repositorio, tmp_path, run_id, FactSheet(
        doc_id=documento.doc_id, tipo_documento=documento.tipo_documento, emissor=documento.emissor,
        data_documento=documento.data_documento, claims=claims, tabela_numeros=documento.tabela_numeros,
        assinado=True))

    (item,) = persistencia.carregar_quarentena(repositorio, run_id)
    assert item.claim == itens[0] and item.decisao is None
    (sugerido,) = quarentena.preparar_revisao(repositorio, run_id, "C02").numeros
    assert sugerido.novo and sugerido.numero.bruto == "44"
    claim = quarentena.aceitar_da_quarentena(repositorio, tmp_path, run_id, "C02", [sugerido.numero], autor="Ana")
    # Repetir a mesma decisão (resposta perdida e nova tentativa) não tem efeito; outra decisão é recusada.
    repositorio.gravar_decisao_quarentena(run_id, "C02", "aceitar", "Ana", claim, [sugerido.numero])
    with pytest.raises(FalhaTerminal, match="já foi revisado"):
        repositorio.gravar_decisao_quarentena(run_id, "C02", "descartar", "Ana", None, [])

    with psycopg.connect(ambiente["SUPABASE_DB_URL"]) as conexao:
        origens = conexao.execute(
            "select c.claim_id, c.origem::text, n.origem::text from claims c left join claim_numeros cn"
            " on cn.run_id = c.run_id and cn.claim_id = c.claim_id left join numeros n on n.id = cn.numero_id"
            " where c.run_id = %s order by c.claim_id", (run_id,)).fetchall()
        with pytest.raises(psycopg.Error, match="imutável"):
            conexao.execute("delete from decisoes_quarentena where run_id = %s", (run_id,))
    assert origens == [("C01", "pipeline", None), ("C02", "humano", "humano")]
    assert sugerido.numero in persistencia.carregar_documento_processado(repositorio, run_id).tabela_numeros

    detalhe = persistencia.ConsultaSupabase(ambiente["SUPABASE_DB_URL"]).detalhar_run(run_id)
    assert [(c["id"], c["origem"]) for c in detalhe["factsheet"]["claims"]] == [("C01", "pipeline"), ("C02", "humano")]
    (q,) = detalhe["quarentena"]
    assert (q["claim_id"], q["decisao"], q["autor"], q["proposto"]["id"]) == ("C02", "aceitar", "Ana", "C02")


def test_consulta_nao_grava(ambiente):
    consulta = persistencia.ConsultaSupabase(ambiente["SUPABASE_DB_URL"])
    with pytest.raises(FalhaTerminal, match="read-only"):
        consulta._executar(lambda conexao: conexao.execute("delete from validacoes_ancora where false"))
