"""Única porta de gravação: Supabase (tabelas e Storage) e cópia local em data/.

Funções da E0 e do run (docs/fluxos/ingestao.md, docs/arquiteturas.md):
- envio do PDF ao bucket `documentos`, sem upsert;
- transação de `documentos`, `coletas` e vínculo em `runs`, com releitura de `runs.coleta_id`;
- registro do documento de referência do modo --dev, sem coleta;
- criação e fechamento da linha de `runs`, e o manifest.json local;
- saída da E1: metadados (documentos_processados), páginas, chunks e números numa transação, e
  data/runs/<run_id>/documento.json;
- spans de LLM, com o custo somado em runs na mesma instrução, e data/runs/<run_id>/spans/;
- E3: resultado de cada rodada em validacoes_ancora e o FactSheet assinado (factsheets, claims e
  claim_numeros numa transação), e data/runs/<run_id>/validacoes/ e factsheet.json;
- cópia local em data/sources/<fonte>/ e data/runs/<run_id>/;
- conexão do checkpointer do LangGraph;
- leitura dos runs para o dashboard (app/), em conexão só de leitura (ConsultaSupabase).

As funções recebem o repositório (Supabase ou, nos testes, em memória) e a pasta raiz de dados
por parâmetro.
"""

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Protocol

from sunontent.retentativa import Falha, FalhaRetriavel, FalhaTerminal, com_retentativa
from sunontent.schemas import DocumentoFonte, DocumentoProcessado, FactSheet, RunManifest, Span

BUCKET_DOCUMENTOS = "documentos"


@dataclass(frozen=True)
class Coleta:
    """Linha de `coletas` vinculada ao run."""

    coleta_id: int
    doc_id: str
    doc_sha256: str
    url_origem: str
    coletado_em: datetime


class Repositorio(Protocol):
    """Operações da E0 no Supabase.

    Cada método traduz os erros do cliente para FalhaRetriavel ou FalhaTerminal.
    """

    def documento_existe(self, doc_sha256: str) -> bool: ...

    def enviar_pdf(self, caminho_storage: str, dados: bytes) -> None:
        """Envia sem upsert. Objeto já existente ("Asset Already Exists") não é falha."""
        ...

    def registrar_coleta(
        self,
        *,
        run_id: str,
        fonte: str,
        tipo_documento: str,
        doc_sha256: str,
        tamanho_bytes: int,
        doc_id: str,
        url_origem: str,
        coletado_em: datetime,
    ) -> Coleta:
        """Numa única transação: documentos (sem efeito se o hash existe), coletas e runs."""
        ...

    def coleta_do_run(self, run_id: str) -> Coleta | None:
        """Coleta já vinculada ao run, ou None se `runs.coleta_id` está vazio."""
        ...

    def registrar_documento_referencia(
        self,
        *,
        run_id: str,
        fonte: str,
        tipo_documento: str,
        doc_sha256: str,
        tamanho_bytes: int,
        doc_id: str,
        url_origem: str,
        coletado_em: datetime,
    ) -> None:
        """Modo --dev, numa transação: documentos (sem efeito se o hash existe) e o vínculo em runs, sem coleta."""
        ...

    def gravar_documento_processado(self, run_id: str, documento: DocumentoProcessado) -> None:
        """Numa transação: `documentos_processados`, `documento_paginas`, `chunks` e `numeros`.

        Linhas que já existem ficam como estão. O documento precisa ser o vinculado ao run (doc_id e doc_sha256).
        """
        ...

    def gravar_span(self, span: Span) -> None:
        """Insere em `llm_spans` e soma tokens e custo em `runs` na mesma instrução. Span repetido não soma de novo."""
        ...

    def gravar_validacao(self, run_id: str, rodada: int, resultados: list[tuple[str, str | None]]) -> None:
        """Linhas de `validacoes_ancora` da rodada: (claim_id, motivo), motivo None quando passou. Repetição não duplica."""
        ...

    def gravar_factsheet(self, run_id: str, factsheet: FactSheet) -> None:
        """Numa transação: `factsheets`, `claims` e `claim_numeros`, cada número ligado à sua linha em `numeros`."""
        ...

    def criar_run(self, manifest: RunManifest) -> None:
        """Insere a linha de `runs` com status em_andamento. Sem efeito se o run_id já existe."""
        ...

    def concluir_run(self, run_id: str) -> None:
        """status = concluido, só se o run está em_andamento."""
        ...

    def abortar_run(self, run_id: str, erro_classe: str, erro_mensagem: str) -> None:
        """status = abortado com a classe e a mensagem do erro, só se o run está em_andamento.

        Não toca nos campos de documento. Run já concluído ou abortado fica como está.
        """
        ...


def caminho_storage(fonte: str, doc_sha256: str) -> str:
    return f"{fonte}/{doc_sha256}.pdf"


def enviar_documento(repositorio: Repositorio, fonte: str, doc_sha256: str, dados: bytes) -> None:
    """Envia o PDF ao bucket se o hash ainda não está em `documentos`."""
    existe = com_retentativa(
        lambda: repositorio.documento_existe(doc_sha256),
        f"consulta de {doc_sha256} em documentos",
    )
    if existe:
        return
    destino = caminho_storage(fonte, doc_sha256)
    com_retentativa(
        lambda: repositorio.enviar_pdf(destino, dados),
        f"envio de {destino} ao bucket {BUCKET_DOCUMENTOS}",
    )


def registrar_coleta(
    repositorio: Repositorio,
    *,
    run_id: str,
    fonte: str,
    tipo_documento: str,
    doc_sha256: str,
    tamanho_bytes: int,
    doc_id: str,
    url_origem: str,
    coletado_em: datetime,
) -> Coleta:
    """Grava documentos, coletas e o vínculo em runs.

    Se a resposta da transação se perde, relê `runs.coleta_id` e só repete se estiver vazio.
    """

    def reler() -> Coleta | None:
        return com_retentativa(
            lambda: repositorio.coleta_do_run(run_id),
            f"releitura de runs.coleta_id do run {run_id}",
        )

    return com_retentativa(
        lambda: repositorio.registrar_coleta(
            run_id=run_id,
            fonte=fonte,
            tipo_documento=tipo_documento,
            doc_sha256=doc_sha256,
            tamanho_bytes=tamanho_bytes,
            doc_id=doc_id,
            url_origem=url_origem,
            coletado_em=coletado_em,
        ),
        f"transação da coleta do run {run_id}",
        antes_de_repetir=reler,
    )


def registrar_documento_referencia(
    repositorio: Repositorio,
    *,
    run_id: str,
    fonte: str,
    tipo_documento: str,
    doc_sha256: str,
    tamanho_bytes: int,
    doc_id: str,
    url_origem: str,
    coletado_em: datetime,
) -> None:
    """Registra o documento de referência em `documentos` e o vincula ao run, sem criar coleta."""
    com_retentativa(
        lambda: repositorio.registrar_documento_referencia(
            run_id=run_id,
            fonte=fonte,
            tipo_documento=tipo_documento,
            doc_sha256=doc_sha256,
            tamanho_bytes=tamanho_bytes,
            doc_id=doc_id,
            url_origem=url_origem,
            coletado_em=coletado_em,
        ),
        f"registro do documento de referência do run {run_id}",
    )


def gravar_documento_processado(
    repositorio: Repositorio, raiz_dados: Path, run_id: str, documento: DocumentoProcessado
) -> Path:
    """Grava a saída da E1 no Supabase e, depois, em data/runs/<run_id>/documento.json."""
    com_retentativa(
        lambda: repositorio.gravar_documento_processado(run_id, documento),
        f"gravação do documento processado do run {run_id}",
    )
    destino = Path(raiz_dados) / "runs" / run_id / "documento.json"
    texto = json.dumps(documento.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    _gravar_atomico(destino, texto.encode("utf-8"))
    return destino


def _gravar_json(destino: Path, conteudo) -> Path:
    texto = json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n"
    _gravar_atomico(destino, texto.encode("utf-8"))
    return destino


def gravar_span(repositorio: Repositorio, raiz_dados: Path, span: Span) -> Path:
    """Grava o span (e o custo do run) no Supabase e, depois, em data/runs/<run_id>/spans/<span_id>.json."""
    com_retentativa(lambda: repositorio.gravar_span(span), f"gravação do span {span.span_id} do run {span.run_id}")
    destino = Path(raiz_dados) / "runs" / span.run_id / "spans" / f"{span.span_id}.json"
    return _gravar_json(destino, span.model_dump(mode="json"))


def gravar_validacao(
    repositorio: Repositorio, raiz_dados: Path, run_id: str, rodada: int, resultados: list[tuple[str, str | None]]
) -> Path:
    """Grava a rodada da E3 no Supabase e, depois, em data/runs/<run_id>/validacoes/<rodada>.json."""
    com_retentativa(
        lambda: repositorio.gravar_validacao(run_id, rodada, resultados),
        f"gravação da validação {rodada} do run {run_id}",
    )
    destino = Path(raiz_dados) / "runs" / run_id / "validacoes" / f"{rodada}.json"
    conteudo = {
        "rodada": rodada,
        "claims": [
            {"claim_id": claim_id, "passou": motivo is None, "motivo": motivo} for claim_id, motivo in resultados
        ],
    }
    return _gravar_json(destino, conteudo)


def gravar_factsheet(repositorio: Repositorio, raiz_dados: Path, run_id: str, factsheet: FactSheet) -> Path:
    """Grava o FactSheet assinado no Supabase e, depois, em data/runs/<run_id>/factsheet.json, sem a
    tabela_numeros, que já está em documento.json."""
    if not factsheet.assinado:
        raise FalhaTerminal(f"FactSheet do run {run_id} não está assinado; só a E3 grava o FactSheet")
    com_retentativa(lambda: repositorio.gravar_factsheet(run_id, factsheet), f"gravação do FactSheet do run {run_id}")
    destino = Path(raiz_dados) / "runs" / run_id / "factsheet.json"
    return _gravar_json(destino, factsheet.model_dump(mode="json", exclude={"tabela_numeros"}))


def criar_run(repositorio: Repositorio, manifest: RunManifest) -> None:
    com_retentativa(lambda: repositorio.criar_run(manifest), f"criação do run {manifest.run_id}")


def concluir_run(repositorio: Repositorio, raiz_dados: Path, manifest: RunManifest) -> None:
    """Grava o run como concluído no Supabase e, depois, no manifest.json local."""
    com_retentativa(lambda: repositorio.concluir_run(manifest.run_id), f"conclusão do run {manifest.run_id}")
    gravar_manifest_local(raiz_dados, manifest, "concluido")


def abortar_run(
    repositorio: Repositorio, raiz_dados: Path, manifest: RunManifest, classe: str, mensagem: str
) -> None:
    """Registra o run como abortado no Supabase e no manifest.json local.

    Se o Supabase não aceitar o update (retentativas esgotadas), o erro fica só no manifest.json e
    a linha do run segue em_andamento (docs/fluxos/ingestao.md, Classificação de erros). Essa falha
    de gravação não é levantada, para não esconder o erro original.
    """
    mensagem = mensagem or "sem mensagem"
    status, texto = "abortado", mensagem
    try:
        com_retentativa(
            lambda: repositorio.abortar_run(manifest.run_id, classe, mensagem),
            f"registro do aborto do run {manifest.run_id}",
        )
    except Falha as falha:
        status = "em_andamento"
        texto = f"{mensagem} (status não gravado no Supabase: {falha.mensagem})"
    gravar_manifest_local(raiz_dados, manifest, status, classe, texto)


def gravar_manifest_local(
    raiz_dados: Path,
    manifest: RunManifest,
    status: str,
    erro_classe: str | None = None,
    erro_mensagem: str | None = None,
) -> Path:
    """Grava data/runs/<run_id>/manifest.json: o RunManifest, o status e o erro do run."""
    conteudo = manifest.model_dump(mode="json")
    conteudo.update(status=status, erro_classe=erro_classe, erro_mensagem=erro_mensagem)
    destino = Path(raiz_dados) / "runs" / manifest.run_id / "manifest.json"
    texto = json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n"
    _gravar_atomico(destino, texto.encode("utf-8"))
    return destino


def _pasta_fonte(raiz_dados: Path, fonte: str) -> Path:
    return Path(raiz_dados) / "sources" / fonte


def _gravar_atomico(destino: Path, dados: bytes) -> None:
    """Grava num temporário da mesma pasta e renomeia no fim."""
    temporario = destino.with_name(f".{destino.name}.{uuid.uuid4().hex}.tmp")
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        with open(temporario, "xb") as arquivo:
            arquivo.write(dados)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, destino)
    except PermissionError as erro:
        temporario.unlink(missing_ok=True)
        raise FalhaTerminal(
            f"não foi possível gravar {destino}: {erro}. Feche o arquivo {destino} "
            "se ele estiver aberto em outro programa."
        ) from erro
    except OSError as erro:
        temporario.unlink(missing_ok=True)
        raise FalhaTerminal(f"não foi possível gravar {destino}: {erro}") from erro


def gravar_pdf_local(raiz_dados: Path, fonte: str, doc_sha256: str, dados: bytes) -> Path:
    """Grava data/sources/<fonte>/<doc_sha256>.pdf só se ainda não existe."""
    destino = _pasta_fonte(raiz_dados, fonte) / f"{doc_sha256}.pdf"
    if not destino.exists():
        _gravar_atomico(destino, dados)
    return destino


def _utc_iso(momento: datetime) -> str:
    return momento.strftime("%Y-%m-%dT%H:%M:%SZ")


def gravar_metadados_coleta(raiz_dados: Path, fonte: str, documento: DocumentoFonte) -> Path:
    """Grava data/sources/<fonte>/<doc_sha256>.<AAAAMMDDTHHMMSSZ>.json a partir do DocumentoFonte."""
    carimbo = documento.coletado_em.strftime("%Y%m%dT%H%M%SZ")
    destino = _pasta_fonte(raiz_dados, fonte) / f"{documento.doc_sha256}.{carimbo}.json"
    conteudo = {
        "doc_id": documento.doc_id,
        "doc_sha256": documento.doc_sha256,
        "url_origem": documento.url_origem,
        "coletado_em": _utc_iso(documento.coletado_em),
    }
    texto = json.dumps(conteudo, ensure_ascii=False, indent=2) + "\n"
    _gravar_atomico(destino, texto.encode("utf-8"))
    return destino


class RepositorioSupabase:
    """Repositório real: Storage pela API do Supabase e tabelas pela conexão Postgres.

    Abre uma conexão nova por operação, para que uma conexão perdida não contamine a
    tentativa seguinte.
    """

    def __init__(self, cliente, db_url: str, connect_timeout_s: int = 10):
        self._cliente = cliente
        self._db_url = db_url
        self._connect_timeout_s = connect_timeout_s

    def _conectar(self):
        import psycopg

        try:
            return psycopg.connect(self._db_url, connect_timeout=self._connect_timeout_s)
        except psycopg.OperationalError as erro:
            raise FalhaRetriavel(f"conexão com o banco recusada: {erro}") from erro

    def _executar(self, operacao):
        import psycopg

        try:
            with self._conectar() as conexao:
                return operacao(conexao)
        except (FalhaRetriavel, FalhaTerminal):
            raise
        except (psycopg.OperationalError, psycopg.errors.QueryCanceled) as erro:
            raise FalhaRetriavel(f"banco: {erro}") from erro
        except psycopg.Error as erro:
            raise FalhaTerminal(f"banco recusou a gravação: {erro}") from erro

    def documento_existe(self, doc_sha256: str) -> bool:
        def consulta(conexao):
            linha = conexao.execute(
                "select 1 from documentos where doc_sha256 = %s", (doc_sha256,)
            ).fetchone()
            return linha is not None

        return self._executar(consulta)

    def enviar_pdf(self, caminho_storage: str, dados: bytes) -> None:
        import httpx

        try:
            # Sem a opção upsert: o Storage recusa caminho existente.
            self._cliente.storage.from_(BUCKET_DOCUMENTOS).upload(
                caminho_storage, dados, {"content-type": "application/pdf"}
            )
        except (httpx.TimeoutException, httpx.TransportError) as erro:
            raise FalhaRetriavel(f"bucket {BUCKET_DOCUMENTOS}: {erro}") from erro
        except Exception as erro:
            status, mensagem = _status_storage(erro)
            if _objeto_ja_existe(status, mensagem):
                return
            if status is not None and status >= 500:
                raise FalhaRetriavel(f"bucket {BUCKET_DOCUMENTOS}: HTTP {status} {mensagem}") from erro
            raise FalhaTerminal(
                f"bucket {BUCKET_DOCUMENTOS} recusou {caminho_storage}: HTTP {status} {mensagem}"
            ) from erro

    def registrar_coleta(
        self,
        *,
        run_id: str,
        fonte: str,
        tipo_documento: str,
        doc_sha256: str,
        tamanho_bytes: int,
        doc_id: str,
        url_origem: str,
        coletado_em: datetime,
    ) -> Coleta:
        def transacao(conexao):
            with conexao.transaction():
                conexao.execute(
                    "insert into documentos (doc_sha256, fonte, tipo_documento, storage_path, tamanho_bytes)"
                    " values (%s, %s, %s, %s, %s) on conflict (doc_sha256) do nothing",
                    (doc_sha256, fonte, tipo_documento, caminho_storage(fonte, doc_sha256), tamanho_bytes),
                )
                coleta_id, coletado_em_banco = conexao.execute(
                    "insert into coletas (doc_sha256, doc_id, url_origem, coletado_em)"
                    " values (%s, %s, %s, %s) returning id, coletado_em",
                    (doc_sha256, doc_id, url_origem, coletado_em),
                ).fetchone()
                vinculo = conexao.execute(
                    "update runs set doc_id = %s, doc_sha256 = %s, coleta_id = %s, url_origem = %s,"
                    " coletado_em = %s where run_id = %s and coleta_id is null",
                    (doc_id, doc_sha256, coleta_id, url_origem, coletado_em_banco, run_id),
                )
                if vinculo.rowcount != 1:
                    raise FalhaTerminal(
                        f"run {run_id} não existe em runs ou já tem coleta vinculada"
                    )
            return Coleta(coleta_id, doc_id, doc_sha256, url_origem, coletado_em_banco)

        return self._executar(transacao)

    def coleta_do_run(self, run_id: str) -> Coleta | None:
        def consulta(conexao):
            linha = conexao.execute(
                "select coleta_id, doc_id, doc_sha256, url_origem, coletado_em"
                " from runs where run_id = %s and coleta_id is not null",
                (run_id,),
            ).fetchone()
            return Coleta(*linha) if linha else None

        return self._executar(consulta)

    def registrar_documento_referencia(
        self,
        *,
        run_id: str,
        fonte: str,
        tipo_documento: str,
        doc_sha256: str,
        tamanho_bytes: int,
        doc_id: str,
        url_origem: str,
        coletado_em: datetime,
    ) -> None:
        def transacao(conexao):
            with conexao.transaction():
                conexao.execute(
                    "insert into documentos (doc_sha256, fonte, tipo_documento, storage_path, tamanho_bytes)"
                    " values (%s, %s, %s, %s, %s) on conflict (doc_sha256) do nothing",
                    (doc_sha256, fonte, tipo_documento, caminho_storage(fonte, doc_sha256), tamanho_bytes),
                )
                vinculo = conexao.execute(
                    "update runs set doc_id = %s, doc_sha256 = %s, url_origem = %s, coletado_em = %s"
                    " where run_id = %s and (doc_sha256 is null or (doc_sha256 = %s and doc_id = %s))",
                    (doc_id, doc_sha256, url_origem, coletado_em, run_id, doc_sha256, doc_id),
                )
                if vinculo.rowcount != 1:
                    raise FalhaTerminal(
                        f"run {run_id} não existe em runs ou já está vinculado a outro documento"
                    )

        self._executar(transacao)

    def gravar_documento_processado(self, run_id: str, documento: DocumentoProcessado) -> None:
        metadados = (
            run_id, documento.doc_id, documento.doc_sha256, documento.tipo_documento, documento.emissor,
            documento.data_documento,
        )
        paginas = [(run_id, p.pagina, p.texto_limpo, p.ocr) for p in documento.paginas]
        chunks = [
            (run_id, c.chunk_id, c.pagina_inicio, c.offset_inicio, c.pagina_fim, c.offset_fim, c.secao, c.texto)
            for c in documento.chunks
        ]
        numeros = [
            (run_id, n.bruto, n.valor, n.unidade, n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim)
            for n in documento.tabela_numeros
        ]

        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                cursor.execute(
                    "insert into documentos_processados (run_id, doc_id, doc_sha256, tipo_documento, emissor,"
                    " data_documento) values (%s, %s, %s, %s::tipo_documento, %s, %s) on conflict (run_id) do nothing",
                    metadados,
                )
                cursor.executemany(
                    "insert into documento_paginas (run_id, pagina, texto_limpo, ocr) values (%s, %s, %s, %s)"
                    " on conflict (run_id, pagina) do nothing",
                    paginas,
                )
                cursor.executemany(
                    "insert into chunks (run_id, chunk_id, pagina_inicio, offset_inicio, pagina_fim, offset_fim,"
                    " secao, texto) values (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " on conflict (run_id, chunk_id) do nothing",
                    chunks,
                )
                cursor.executemany(
                    "insert into numeros (run_id, bruto, valor, unidade, pagina, offset_inicio, offset_fim)"
                    " values (%s, %s, %s, %s::unidade_numero, %s, %s, %s)"
                    " on conflict (run_id, pagina, offset_inicio, offset_fim) do nothing",
                    numeros,
                )

        self._executar(transacao)

    def gravar_span(self, span: Span) -> None:
        def instrucao(conexao):
            conexao.execute(
                "with s as (insert into llm_spans (span_id, run_id, no, nivel, celula_id, tentativa, modelo,"
                " tokens_entrada, tokens_saida, custo_brl, duracao_ms, falha_classe, falha_mensagem)"
                " values (%s, %s, %s, %s::nivel, %s, %s, %s, %s, %s, %s, %s, %s::classe_falha, %s)"
                " on conflict (span_id) do nothing"
                " returning run_id, tokens_entrada + tokens_saida as tokens, custo_brl)"
                " update runs set custo_total_tokens = runs.custo_total_tokens + s.tokens,"
                " custo_total_brl = runs.custo_total_brl + s.custo_brl from s where runs.run_id = s.run_id",
                (
                    span.span_id,
                    span.run_id,
                    span.no,
                    span.nivel,
                    span.celula_id,
                    span.tentativa,
                    span.modelo,
                    span.tokens_entrada,
                    span.tokens_saida,
                    span.custo_brl,
                    span.duracao_ms,
                    span.falha_classe,
                    span.falha_mensagem,
                ),
            )

        self._executar(instrucao)

    def gravar_validacao(self, run_id: str, rodada: int, resultados: list[tuple[str, str | None]]) -> None:
        linhas = [(run_id, rodada, claim_id, motivo is None, motivo) for claim_id, motivo in resultados]

        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                cursor.executemany(
                    "insert into validacoes_ancora (run_id, rodada, claim_id, passou, motivo)"
                    " values (%s, %s, %s, %s, %s) on conflict (run_id, rodada, claim_id) do nothing",
                    linhas,
                )

        self._executar(transacao)

    def gravar_factsheet(self, run_id: str, factsheet: FactSheet) -> None:
        claims = [
            (run_id, c.id, c.texto, c.tipo, c.tags, c.literal_fonte, c.ancora.pagina, c.ancora.offset_inicio,
             c.ancora.offset_fim)
            for c in factsheet.claims
        ]
        numeros = [
            (run_id, c.id, ordem, n.bruto, n.valor, n.unidade, n.ancora.pagina, n.ancora.offset_inicio,
             n.ancora.offset_fim, run_id, n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim)
            for c in factsheet.claims
            for ordem, n in enumerate(c.numeros, start=1)
        ]

        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                cursor.execute(
                    "insert into factsheets (run_id, doc_id, tipo_documento, emissor, data_documento, assinado)"
                    " values (%s, %s, %s::tipo_documento, %s, %s, %s) on conflict (run_id) do nothing",
                    (run_id, factsheet.doc_id, factsheet.tipo_documento, factsheet.emissor,
                     factsheet.data_documento, factsheet.assinado),
                )
                cursor.executemany(
                    "insert into claims (run_id, claim_id, texto, tipo, tags, literal_fonte, pagina, offset_inicio,"
                    " offset_fim) values (%s, %s, %s, %s::tipo_claim, %s, %s, %s, %s, %s)"
                    " on conflict (run_id, claim_id) do nothing",
                    claims,
                )
                cursor.executemany(
                    "insert into claim_numeros (run_id, claim_id, ordem, bruto, valor, unidade, pagina,"
                    " offset_inicio, offset_fim, numero_id) values (%s, %s, %s, %s, %s, %s::unidade_numero, %s,"
                    " %s, %s, (select id from numeros where run_id = %s and pagina = %s and offset_inicio = %s"
                    " and offset_fim = %s)) on conflict (run_id, claim_id, ordem) do nothing",
                    numeros,
                )

        self._executar(transacao)

    def criar_run(self, manifest: RunManifest) -> None:
        from psycopg.types.json import Jsonb

        def insercao(conexao):
            conexao.execute(
                'insert into runs (run_id, "timestamp", modo, fonte, modelos, prompts, level_specs,'
                ' glossario_versao, arquivos_sha256, "K") values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'
                " on conflict (run_id) do nothing",
                (
                    manifest.run_id,
                    manifest.timestamp,
                    manifest.modo,
                    manifest.fonte,
                    Jsonb(manifest.modelos),
                    Jsonb(manifest.prompts),
                    Jsonb(manifest.level_specs),
                    manifest.glossario_versao,
                    Jsonb(manifest.arquivos_sha256),
                    manifest.K,
                ),
            )

        self._executar(insercao)

    def concluir_run(self, run_id: str) -> None:
        def atualizacao(conexao):
            alterado = conexao.execute(
                "update runs set status = 'concluido' where run_id = %s and status = 'em_andamento'",
                (run_id,),
            )
            if alterado.rowcount != 1:
                raise FalhaTerminal(f"run {run_id} não existe em runs ou não está em_andamento")

        self._executar(atualizacao)

    def abortar_run(self, run_id: str, erro_classe: str, erro_mensagem: str) -> None:
        def atualizacao(conexao):
            conexao.execute(
                "update runs set status = 'abortado', erro_classe = %s, erro_mensagem = %s"
                " where run_id = %s and status = 'em_andamento'",
                (erro_classe, erro_mensagem, run_id),
            )

        self._executar(atualizacao)

    def verificar_conexao(self) -> None:
        """Confere, sem retentativa, que o banco aceita a conexão."""
        self._executar(lambda conexao: conexao.execute("select 1"))


class ConsultaSupabase:
    """Leitura dos runs para o dashboard (app/). Não grava: toda transação abre com BEGIN READ ONLY.

    O read_only fica na conexão do psycopg, e não em `options` da URL, porque o pooler do Supabase
    descarta os parâmetros de inicialização da sessão.

    Uma conexão por consulta, como no RepositorioSupabase. Devolve dicionários com os tipos do psycopg
    (datetime, date, Decimal); a serialização para JSON fica com o servidor do dashboard.
    """

    def __init__(self, db_url: str, connect_timeout_s: int = 10):
        self._db_url = db_url
        self._connect_timeout_s = connect_timeout_s

    def _executar(self, consulta):
        import psycopg
        from psycopg.rows import dict_row

        try:
            with psycopg.connect(
                self._db_url, connect_timeout=self._connect_timeout_s, row_factory=dict_row
            ) as conexao:
                conexao.read_only = True
                return consulta(conexao)
        except psycopg.OperationalError as erro:
            raise FalhaRetriavel(f"banco: {erro}") from erro
        except psycopg.Error as erro:
            raise FalhaTerminal(f"banco recusou a consulta: {erro}") from erro

    def verificar_conexao(self) -> None:
        self._executar(lambda conexao: conexao.execute("select 1"))

    def listar_runs(self, limite: int = 200) -> list[dict]:
        """Resumo dos runs, do mais recente ao mais antigo. `claims` é nulo sem FactSheet assinado."""

        def consulta(conexao):
            return conexao.execute(
                'select r.run_id, r."timestamp", r.modo, r.fonte, r.status, r.doc_id, r.custo_total_tokens,'
                " r.custo_total_brl, r.erro_classe, r.erro_mensagem,"
                " nullif((select count(*) from claims c where c.run_id = r.run_id), 0) as claims"
                ' from runs r order by r."timestamp" desc, r.run_id limit %s',
                (limite,),
            ).fetchall()

        return self._executar(consulta)

    def detalhar_run(self, run_id: str) -> dict | None:
        """Run, documento processado (resumido), FactSheet, validações por rodada e spans. None se o run não existe."""

        def consulta(conexao):
            manifest = conexao.execute(
                'select run_id, "timestamp", modo, fonte, status, doc_id, doc_sha256, coleta_id, url_origem,'
                ' coletado_em, modelos, prompts, level_specs, glossario_versao, arquivos_sha256, "K",'
                " custo_total_tokens, custo_total_brl, erro_classe, erro_mensagem from runs where run_id = %s",
                (run_id,),
            ).fetchone()
            if manifest is None:
                return None
            return {
                "manifest": manifest,
                "documento": self._documento(conexao, run_id),
                "factsheet": self._factsheet(conexao, run_id),
                "validacoes": self._validacoes(conexao, run_id),
                "spans": conexao.execute(
                    "select span_id, no, nivel, celula_id, tentativa, modelo, tokens_entrada, tokens_saida,"
                    " custo_brl, duracao_ms, falha_classe, falha_mensagem, criado_em"
                    " from llm_spans where run_id = %s order by criado_em, span_id",
                    (run_id,),
                ).fetchall(),
            }

        return self._executar(consulta)

    @staticmethod
    def _documento(conexao, run_id: str) -> dict | None:
        """Metadados e contagens da saída da E1. None se a E1 não gravou o documento do run."""
        return conexao.execute(
            "select d.doc_id, d.doc_sha256, d.tipo_documento, d.emissor, d.data_documento,"
            " (select count(*) from documento_paginas p where p.run_id = d.run_id) as paginas,"
            " (select count(*) from documento_paginas p where p.run_id = d.run_id and p.ocr) as paginas_ocr,"
            " (select count(*) from chunks c where c.run_id = d.run_id) as chunks,"
            " (select count(*) from numeros n where n.run_id = d.run_id) as numeros"
            " from documentos_processados d where d.run_id = %s",
            (run_id,),
        ).fetchone()

    @staticmethod
    def _factsheet(conexao, run_id: str) -> dict | None:
        factsheet = conexao.execute(
            "select doc_id, tipo_documento, emissor, data_documento, assinado from factsheets where run_id = %s",
            (run_id,),
        ).fetchone()
        if factsheet is None:
            return None
        numeros: dict[str, list[dict]] = {}
        for n in conexao.execute(
            "select claim_id, bruto, valor, unidade, pagina, offset_inicio, offset_fim from claim_numeros"
            " where run_id = %s order by claim_id, ordem",
            (run_id,),
        ):
            numeros.setdefault(n["claim_id"], []).append(
                {"bruto": n["bruto"], "valor": n["valor"], "unidade": n["unidade"], "ancora": _ancora(n)}
            )
        claims = [
            {
                "id": c["claim_id"],
                "texto": c["texto"],
                "tipo": c["tipo"],
                "tags": c["tags"],
                "literal_fonte": c["literal_fonte"],
                "ancora": _ancora(c),
                "numeros": numeros.get(c["claim_id"], []),
            }
            for c in conexao.execute(
                "select claim_id, texto, tipo, tags, literal_fonte, pagina, offset_inicio, offset_fim"
                " from claims where run_id = %s order by claim_id",
                (run_id,),
            )
        ]
        return {**factsheet, "claims": claims}

    @staticmethod
    def _validacoes(conexao, run_id: str) -> list[dict]:
        rodadas: dict[int, list[dict]] = {}
        for v in conexao.execute(
            "select rodada, claim_id, passou, motivo from validacoes_ancora where run_id = %s"
            " order by rodada, claim_id",
            (run_id,),
        ):
            rodadas.setdefault(v["rodada"], []).append(
                {"claim_id": v["claim_id"], "passou": v["passou"], "motivo": v["motivo"]}
            )
        return [{"rodada": rodada, "claims": claims} for rodada, claims in rodadas.items()]


def _ancora(linha: dict) -> dict:
    return {"pagina": linha["pagina"], "offset_inicio": linha["offset_inicio"], "offset_fim": linha["offset_fim"]}


VARIAVEIS_SUPABASE = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_DB_URL")


def conferir_credenciais(ambiente: Mapping[str, str]) -> None:
    ausentes = [nome for nome in VARIAVEIS_SUPABASE if not ambiente.get(nome)]
    if ausentes:
        raise FalhaTerminal(
            f"credenciais do Supabase ausentes: {', '.join(ausentes)}. "
            "Preencha o .env a partir do .env.example."
        )


def criar_repositorio_supabase(ambiente: Mapping[str, str]) -> RepositorioSupabase:
    """Confere as credenciais, cria o cliente do Supabase e testa a conexão com o banco."""
    conferir_credenciais(ambiente)
    from supabase import create_client

    cliente = create_client(ambiente["SUPABASE_URL"], ambiente["SUPABASE_SERVICE_ROLE_KEY"])
    repositorio = RepositorioSupabase(cliente, ambiente["SUPABASE_DB_URL"])
    repositorio.verificar_conexao()
    return repositorio


def criar_consulta_supabase(ambiente: Mapping[str, str]) -> ConsultaSupabase:
    """Confere as credenciais e testa a conexão de leitura do dashboard."""
    conferir_credenciais(ambiente)
    consulta = ConsultaSupabase(ambiente["SUPABASE_DB_URL"])
    consulta.verificar_conexao()
    return consulta


@contextmanager
def checkpointer_postgres(db_url: str):
    """PostgresSaver no schema `langgraph`, em conexão própria (docs/arquiteturas.md, Granularidade do checkpoint)."""
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row

    try:
        with psycopg.connect(
            db_url,
            autocommit=True,
            row_factory=dict_row,
            options="-c search_path=langgraph",
            connect_timeout=10,
        ) as conexao:
            saver = PostgresSaver(conexao)
            saver.setup()
            yield saver
    except psycopg.Error as erro:
        raise FalhaTerminal(f"checkpointer: banco recusou a conexão ou o setup: {erro}") from erro


def _status_storage(erro: Exception) -> tuple[int | None, str]:
    """Extrai status HTTP e mensagem de uma exceção do cliente de Storage."""
    dados = erro.args[0] if erro.args and isinstance(erro.args[0], dict) else {}
    status = getattr(erro, "status", None) or dados.get("statusCode") or dados.get("status")
    mensagem = getattr(erro, "message", None) or dados.get("message") or dados.get("error") or str(erro)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    return status, str(mensagem)


def _objeto_ja_existe(status: int | None, mensagem: str) -> bool:
    texto = mensagem.lower()
    return status == 409 or "already exists" in texto or "duplicate" in texto
