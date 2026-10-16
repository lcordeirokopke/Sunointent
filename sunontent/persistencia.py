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
- quarentena do loop E2/E3 (claims_quarentena e data/runs/<run_id>/quarentena.json), a leitura do
  documento e da quarentena para a revisão, e a decisão humana (decisoes_quarentena e, ao aceitar, o
  claim e os números novos com origem humano, numa transação; data/runs/<run_id>/revisoes/);
- E4: problemas e saída do planejador de cada rodada do gate (validacoes_plano e rodadas_plano), e o plano
  (planos_editoriais e planos_nivel) com as 9 células em PENDENTE numa transação; data/runs/<run_id>/
  validacoes_plano/, plano.json e celulas/<celula_id>/estado.json;
- leitura do FactSheet assinado do run, com os números de cada claim na ordem de claim_numeros;
- atualização do estado, da tentativa, do motivo de reprovação e do erro de uma célula, com atualizado_em;
- E5: cada tentativa da peça (pecas, peca_unidade_claims e peca_unidade_numeros) com a atualização da célula
  numa transação; data/runs/<run_id>/celulas/<celula_id>/peca_<tentativa>.json e estado.json;
- E6: o scorecard de cada tentativa (scorecards, metricas e violacoes) numa transação, e
  data/runs/<run_id>/celulas/<celula_id>/scorecard_<tentativa>.json; a leitura do scorecard de uma tentativa (E7);
- cópia local em data/sources/<fonte>/ e data/runs/<run_id>/;
- leitura dos runs para o dashboard (app/), em conexão só de leitura (ConsultaSupabase).

As funções recebem o repositório (Supabase ou, nos testes, em memória) e a pasta raiz de dados
por parâmetro.
"""

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from sunontent.retentativa import Falha, FalhaRetriavel, FalhaTerminal, com_retentativa
from sunontent.schemas import (
    Ancora,
    CelulaState,
    Chunk,
    Claim,
    ClaimQuarentena,
    DecisaoQuarentena,
    DocumentoFonte,
    DocumentoProcessado,
    FactSheet,
    Numero,
    Pagina,
    Peca,
    PlanoEditorial,
    ProblemaPlano,
    RunManifest,
    SaidaPlanejador,
    Scorecard,
    Span,
)

BUCKET_DOCUMENTOS = "documentos"


@dataclass(frozen=True)
class Coleta:
    """Linha de `coletas` vinculada ao run."""

    coleta_id: int
    doc_id: str
    doc_sha256: str
    url_origem: str
    coletado_em: datetime


@dataclass(frozen=True)
class ItemQuarentena:
    """Linha de `claims_quarentena` e a decisão humana, None enquanto não revisado."""

    claim: ClaimQuarentena
    decisao: DecisaoQuarentena | None


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

    def gravar_quarentena(self, run_id: str, itens: list[ClaimQuarentena]) -> None:
        """Linhas de `claims_quarentena`, numa transação. Repetição não duplica."""
        ...

    def carregar_documento_processado(self, run_id: str) -> DocumentoProcessado:
        """O DocumentoProcessado gravado pela E1, com os números de origem humano. FalhaTerminal se não existe."""
        ...

    def carregar_quarentena(self, run_id: str) -> list[ItemQuarentena]:
        """Claims em quarentena do run, em ordem de id, cada um com a sua decisão."""
        ...

    def gravar_decisao_quarentena(
        self,
        run_id: str,
        claim_id: str,
        decisao: DecisaoQuarentena,
        autor: str,
        claim: Claim | None,
        numeros_novos: list[Numero],
    ) -> None:
        """Numa transação: a linha de `decisoes_quarentena` e, ao aceitar, os números novos em `numeros` e o
        claim em `claims` e `claim_numeros`, com origem humano. Repetir a mesma decisão não tem efeito;
        decisão diferente da gravada é FalhaTerminal. Aceitar exige o FactSheet assinado do run."""
        ...

    def carregar_factsheet(self, run_id: str) -> FactSheet:
        """O FactSheet assinado do run: claims em ordem de id (com os de origem humano), os números de cada claim
        na ordem de `claim_numeros` e a tabela de números do documento. FalhaTerminal se não existe."""
        ...

    def gravar_validacao_plano(
        self, run_id: str, rodada: int, problemas: list[ProblemaPlano], saida: SaidaPlanejador
    ) -> None:
        """Numa transação: a linha de `rodadas_plano` com a saída do planejador e as linhas de `validacoes_plano`
        da rodada, uma por problema, com `ordem` a partir de 1. Repetição não duplica."""
        ...

    def apagar_validacoes_plano(self, run_id: str) -> None:
        """Apaga as linhas de `validacoes_plano` e `rodadas_plano` do run: a E4 recomeça sem plano gravado."""
        ...

    def gravar_plano(self, run_id: str, plano: PlanoEditorial, celulas: list[CelulaState]) -> None:
        """Numa transação: `planos_editoriais`, `planos_nivel` e as linhas de `celulas`. Exige o FactSheet
        assinado do run. Se o run já tem plano, não grava nada: o mesmo plano não é erro (repetição depois de
        resposta perdida), outro plano é FalhaTerminal."""
        ...

    def carregar_plano(self, run_id: str) -> tuple[PlanoEditorial, list[CelulaState]] | None:
        """O plano gravado do run e as suas células, ou None se `planos_editoriais` não tem o run."""
        ...

    def gravar_peca(self, run_id: str, peca: Peca, celula: CelulaState) -> None:
        """Grava a peça de uma tentativa (pecas, as unidades com os claims que existem no run e os números usados)
        e atualiza a célula, numa transação. A mesma peça já gravada não grava nada; outra peça na mesma
        tentativa é FalhaTerminal."""
        ...

    def atualizar_celula(self, run_id: str, celula: CelulaState) -> None:
        """Grava o estado, a tentativa, o motivo de reprovação e o erro da célula em `celulas`, com
        `atualizado_em = now()`. FalhaTerminal se a célula não existe."""
        ...

    def gravar_scorecard(self, run_id: str, scorecard: Scorecard) -> None:
        """Numa transação: `scorecards`, `metricas` e `violacoes` da tentativa. Exige a peça da tentativa. O mesmo
        scorecard já gravado não grava nada; outro na mesma tentativa é FalhaTerminal."""
        ...

    def carregar_scorecard(self, run_id: str, celula_id: str, tentativa: int) -> Scorecard | None:
        """O scorecard gravado da tentativa, com as métricas e as violações na ordem gravada; None se não existe."""
        ...

    def criar_run(self, manifest: RunManifest) -> None:
        """Insere a linha de `runs` com status em_andamento. Sem efeito se o run_id já existe."""
        ...

    def concluir_run(self, run_id: str) -> None:
        """status = concluido, só se o run está em_andamento."""
        ...

    def abortar_run(self, run_id: str, erro_classe: str, erro_mensagem: str, etapa_erro: str | None) -> None:
        """status = abortado com a classe, a mensagem e a etapa do erro, só se o run está em_andamento.

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


def gravar_quarentena(repositorio: Repositorio, raiz_dados: Path, run_id: str, itens: list[ClaimQuarentena]) -> Path:
    """Grava a quarentena no Supabase e, depois, em data/runs/<run_id>/quarentena.json."""
    com_retentativa(lambda: repositorio.gravar_quarentena(run_id, itens), f"gravação da quarentena do run {run_id}")
    destino = Path(raiz_dados) / "runs" / run_id / "quarentena.json"
    return _gravar_json(destino, [item.model_dump(mode="json") for item in itens])


def carregar_documento_processado(repositorio: Repositorio, run_id: str) -> DocumentoProcessado:
    return com_retentativa(
        lambda: repositorio.carregar_documento_processado(run_id),
        f"leitura do documento processado do run {run_id}",
    )


def carregar_factsheet(repositorio: Repositorio, run_id: str) -> FactSheet:
    return com_retentativa(lambda: repositorio.carregar_factsheet(run_id), f"leitura do FactSheet do run {run_id}")


def carregar_quarentena(repositorio: Repositorio, run_id: str) -> list[ItemQuarentena]:
    return com_retentativa(lambda: repositorio.carregar_quarentena(run_id), f"leitura da quarentena do run {run_id}")


def gravar_decisao_quarentena(
    repositorio: Repositorio,
    raiz_dados: Path,
    run_id: str,
    claim_id: str,
    decisao: DecisaoQuarentena,
    autor: str,
    *,
    claim: Claim | None = None,
    numeros_novos: list[Numero] = (),
) -> Path:
    """Grava a decisão humana sobre um claim em quarentena no Supabase e, depois, em
    data/runs/<run_id>/revisoes/<claim_id>.json. Ao aceitar, o claim entra no FactSheet do run."""
    autor = autor.strip()
    if not autor:
        raise FalhaTerminal("a decisão sobre a quarentena precisa do nome de quem revisou")
    if (decisao == "aceitar") != (claim is not None) or (claim is not None and claim.id != claim_id):
        raise FalhaTerminal(f"decisão {decisao} do claim {claim_id} sem o claim correspondente")
    numeros_novos = list(numeros_novos)
    com_retentativa(
        lambda: repositorio.gravar_decisao_quarentena(run_id, claim_id, decisao, autor, claim, numeros_novos),
        f"gravação da decisão sobre o claim {claim_id} do run {run_id}",
    )
    destino = Path(raiz_dados) / "runs" / run_id / "revisoes" / f"{claim_id}.json"
    conteudo = {
        "claim_id": claim_id,
        "decisao": decisao,
        "autor": autor,
        "decidido_em": _utc_iso(datetime.now(timezone.utc)),
        "claim": claim.model_dump(mode="json") if claim else None,
        "numeros_novos": [n.model_dump(mode="json") for n in numeros_novos],
    }
    return _gravar_json(destino, conteudo)


def gravar_validacao_plano(
    repositorio: Repositorio,
    raiz_dados: Path,
    run_id: str,
    rodada: int,
    problemas: list[ProblemaPlano],
    saida: SaidaPlanejador,
) -> Path:
    """Grava a saída do planejador e os problemas da rodada do gate no Supabase (a rodada sem problema não tem
    linha em validacoes_plano) e, depois, em data/runs/<run_id>/validacoes_plano/<rodada>.json, com a lista
    vazia na rodada que passou."""
    com_retentativa(
        lambda: repositorio.gravar_validacao_plano(run_id, rodada, problemas, saida),
        f"gravação da rodada {rodada} do gate do plano do run {run_id}",
    )
    destino = Path(raiz_dados) / "runs" / run_id / "validacoes_plano" / f"{rodada}.json"
    conteudo = {
        "rodada": rodada,
        "problemas": [p.model_dump(mode="json") for p in problemas],
        "saida": saida.model_dump(mode="json"),
    }
    return _gravar_json(destino, conteudo)


def reiniciar_validacoes_plano(repositorio: Repositorio, raiz_dados: Path, run_id: str) -> None:
    """Apaga as rodadas do gate (problemas e saída do planejador) de uma execução da E4 que não chegou a gravar
    o plano, no Supabase e na cópia local, para que a nova execução não misture as suas linhas com as antigas."""
    com_retentativa(
        lambda: repositorio.apagar_validacoes_plano(run_id), f"limpeza das rodadas do gate do plano do run {run_id}"
    )
    pasta = Path(raiz_dados) / "runs" / run_id / "validacoes_plano"
    if pasta.is_dir():
        for arquivo in pasta.glob("*.json"):
            arquivo.unlink()


def _gravar_celula_local(raiz_dados: Path, run_id: str, celula: CelulaState) -> Path:
    destino = Path(raiz_dados) / "runs" / run_id / "celulas" / celula.celula_id / "estado.json"
    return _gravar_json(
        destino, celula.model_dump(mode="json", exclude={"peca", "scorecard", "plan_contract"}, exclude_none=True)
    )


def _gravar_plano_local(raiz_dados: Path, run_id: str, plano: PlanoEditorial, celulas: list[CelulaState]) -> Path:
    for celula in celulas:
        _gravar_celula_local(raiz_dados, run_id, celula)
    return _gravar_json(Path(raiz_dados) / "runs" / run_id / "plano.json", plano.model_dump(mode="json"))


def gravar_plano(
    repositorio: Repositorio, raiz_dados: Path, run_id: str, plano: PlanoEditorial, celulas: list[CelulaState]
) -> Path:
    """Grava o plano e as 9 células no Supabase e, depois, em data/runs/<run_id>/plano.json e
    celulas/<celula_id>/estado.json."""
    com_retentativa(
        lambda: repositorio.gravar_plano(run_id, plano, celulas), f"gravação do plano editorial do run {run_id}"
    )
    return _gravar_plano_local(raiz_dados, run_id, plano, celulas)


def carregar_plano(
    repositorio: Repositorio, raiz_dados: Path, run_id: str
) -> tuple[PlanoEditorial, list[CelulaState]] | None:
    """O plano já gravado do run, com a cópia local que faltar gravada; None se não existe."""
    gravado = com_retentativa(lambda: repositorio.carregar_plano(run_id), f"leitura do plano editorial do run {run_id}")
    if gravado is not None:
        plano, celulas = gravado
        pasta = Path(raiz_dados) / "runs" / run_id
        faltando = not (pasta / "plano.json").exists() or any(
            not (pasta / "celulas" / c.celula_id / "estado.json").exists() for c in celulas
        )
        if faltando:
            _gravar_plano_local(raiz_dados, run_id, plano, celulas)
    return gravado


def atualizar_celula(repositorio: Repositorio, raiz_dados: Path, run_id: str, celula: CelulaState) -> Path:
    """Grava o estado e a tentativa da célula no Supabase e, depois, em
    data/runs/<run_id>/celulas/<celula_id>/estado.json."""
    com_retentativa(
        lambda: repositorio.atualizar_celula(run_id, celula),
        f"atualização da célula {celula.celula_id} do run {run_id}",
    )
    return _gravar_celula_local(raiz_dados, run_id, celula)


def gravar_scorecard(repositorio: Repositorio, raiz_dados: Path, run_id: str, scorecard: Scorecard) -> Path:
    """Grava o scorecard da tentativa no Supabase e, depois, em data/runs/<run_id>/celulas/<celula_id>/
    scorecard_<tentativa>.json."""
    com_retentativa(
        lambda: repositorio.gravar_scorecard(run_id, scorecard),
        f"gravação do scorecard {scorecard.celula_id} tentativa {scorecard.tentativa} do run {run_id}",
    )
    destino = (
        Path(raiz_dados) / "runs" / run_id / "celulas" / scorecard.celula_id / f"scorecard_{scorecard.tentativa}.json"
    )
    return _gravar_json(destino, scorecard.model_dump(mode="json"))


def carregar_scorecard(repositorio: Repositorio, run_id: str, celula_id: str, tentativa: int) -> Scorecard | None:
    return com_retentativa(
        lambda: repositorio.carregar_scorecard(run_id, celula_id, tentativa),
        f"leitura do scorecard {celula_id} tentativa {tentativa} do run {run_id}",
    )


def gravar_peca(repositorio: Repositorio, raiz_dados: Path, run_id: str, peca: Peca, celula: CelulaState) -> Path:
    """Grava a peça e a célula no Supabase e, depois, em data/runs/<run_id>/celulas/<celula_id>/
    peca_<tentativa>.json e estado.json."""
    com_retentativa(
        lambda: repositorio.gravar_peca(run_id, peca, celula),
        f"gravação da peça {peca.celula_id} tentativa {peca.tentativa} do run {run_id}",
    )
    _gravar_celula_local(raiz_dados, run_id, celula)
    destino = Path(raiz_dados) / "runs" / run_id / "celulas" / peca.celula_id / f"peca_{peca.tentativa}.json"
    return _gravar_json(destino, peca.model_dump(mode="json"))


def criar_run(repositorio: Repositorio, manifest: RunManifest) -> None:
    com_retentativa(lambda: repositorio.criar_run(manifest), f"criação do run {manifest.run_id}")


def concluir_run(repositorio: Repositorio, raiz_dados: Path, manifest: RunManifest) -> None:
    """Grava o run como concluído no Supabase e, depois, no manifest.json local."""
    com_retentativa(lambda: repositorio.concluir_run(manifest.run_id), f"conclusão do run {manifest.run_id}")
    gravar_manifest_local(raiz_dados, manifest, "concluido")


def abortar_run(
    repositorio: Repositorio,
    raiz_dados: Path,
    manifest: RunManifest,
    classe: str,
    mensagem: str,
    etapa: str | None = None,
) -> None:
    """Registra o run como abortado no Supabase e no manifest.json local, com a etapa do erro (o nó do grafo
    em execução ou, se a falha veio depois do fim do nó, o último concluído; None fora do grafo).

    Se o Supabase não aceitar o update (retentativas esgotadas), o erro fica só no manifest.json e
    a linha do run segue em_andamento (docs/fluxos/ingestao.md, Classificação de erros). Essa falha
    de gravação não é levantada, para não esconder o erro original.
    """
    mensagem = mensagem or "sem mensagem"
    status, texto = "abortado", mensagem
    try:
        com_retentativa(
            lambda: repositorio.abortar_run(manifest.run_id, classe, mensagem, etapa),
            f"registro do aborto do run {manifest.run_id}",
        )
    except Falha as falha:
        status = "em_andamento"
        texto = f"{mensagem} (status não gravado no Supabase: {falha.mensagem})"
    gravar_manifest_local(raiz_dados, manifest, status, classe, texto, etapa)


def gravar_manifest_local(
    raiz_dados: Path,
    manifest: RunManifest,
    status: str,
    erro_classe: str | None = None,
    erro_mensagem: str | None = None,
    etapa_erro: str | None = None,
) -> Path:
    """Grava data/runs/<run_id>/manifest.json: o RunManifest, o status e o erro do run, com a etapa do erro."""
    conteudo = manifest.model_dump(mode="json")
    conteudo.update(status=status, erro_classe=erro_classe, erro_mensagem=erro_mensagem, etapa_erro=etapa_erro)
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
                "with s as (insert into llm_spans (span_id, run_id, no, nivel, celula_id, tentativa, rodada, modelo,"
                " tokens_entrada, tokens_saida, custo_brl, duracao_ms, falha_classe, falha_mensagem)"
                " values (%s, %s, %s, %s::nivel, %s, %s, %s, %s, %s, %s, %s, %s, %s::classe_falha, %s)"
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
                    span.rodada,
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

    @staticmethod
    def _inserir_claims(cursor, run_id: str, claims: list[Claim], origem: str) -> None:
        """Linhas de `claims` e `claim_numeros`, cada número ligado à sua linha em `numeros` pela posição."""
        linhas = [
            (run_id, c.id, c.texto, c.tipo, c.tags, c.literal_fonte, c.ancora.pagina, c.ancora.offset_inicio,
             c.ancora.offset_fim, origem)
            for c in claims
        ]
        numeros = [
            (run_id, c.id, ordem, n.bruto, n.valor, n.unidade, n.ancora.pagina, n.ancora.offset_inicio,
             n.ancora.offset_fim, run_id, n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim)
            for c in claims
            for ordem, n in enumerate(c.numeros, start=1)
        ]
        cursor.executemany(
            "insert into claims (run_id, claim_id, texto, tipo, tags, literal_fonte, pagina, offset_inicio,"
            " offset_fim, origem) values (%s, %s, %s, %s::tipo_claim, %s, %s, %s, %s, %s, %s::origem_dado)"
            " on conflict (run_id, claim_id) do nothing",
            linhas,
        )
        cursor.executemany(
            "insert into claim_numeros (run_id, claim_id, ordem, bruto, valor, unidade, pagina,"
            " offset_inicio, offset_fim, numero_id) values (%s, %s, %s, %s, %s, %s::unidade_numero, %s,"
            " %s, %s, (select id from numeros where run_id = %s and pagina = %s and offset_inicio = %s"
            " and offset_fim = %s)) on conflict (run_id, claim_id, ordem) do nothing",
            numeros,
        )

    def gravar_factsheet(self, run_id: str, factsheet: FactSheet) -> None:
        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                cursor.execute(
                    "insert into factsheets (run_id, doc_id, tipo_documento, emissor, data_documento, assinado)"
                    " values (%s, %s, %s::tipo_documento, %s, %s, %s) on conflict (run_id) do nothing",
                    (run_id, factsheet.doc_id, factsheet.tipo_documento, factsheet.emissor,
                     factsheet.data_documento, factsheet.assinado),
                )
                self._inserir_claims(cursor, run_id, factsheet.claims, "pipeline")

        self._executar(transacao)

    def gravar_quarentena(self, run_id: str, itens: list[ClaimQuarentena]) -> None:
        from psycopg.types.json import Jsonb

        linhas = [
            (run_id, item.proposto.id, item.rodada, Jsonb(item.proposto.model_dump(mode="json")), item.motivo)
            for item in itens
        ]

        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                cursor.executemany(
                    "insert into claims_quarentena (run_id, claim_id, rodada, proposto, motivo)"
                    " values (%s, %s, %s, %s, %s) on conflict (run_id, claim_id) do nothing",
                    linhas,
                )

        self._executar(transacao)

    def carregar_documento_processado(self, run_id: str) -> DocumentoProcessado:
        def consulta(conexao):
            metadados = conexao.execute(
                "select doc_id, doc_sha256, tipo_documento::text, emissor, data_documento"
                " from documentos_processados where run_id = %s",
                (run_id,),
            ).fetchone()
            if metadados is None:
                raise FalhaTerminal(f"run {run_id} não tem documento processado")
            paginas = conexao.execute(
                "select pagina, texto_limpo, ocr from documento_paginas where run_id = %s order by pagina", (run_id,)
            ).fetchall()
            chunks = conexao.execute(
                "select chunk_id, secao, pagina_inicio, offset_inicio, pagina_fim, offset_fim, texto from chunks"
                " where run_id = %s order by pagina_inicio, offset_inicio, chunk_id",
                (run_id,),
            ).fetchall()
            numeros = conexao.execute(
                "select valor, unidade::text, bruto, pagina, offset_inicio, offset_fim from numeros"
                " where run_id = %s order by pagina, offset_inicio, offset_fim",
                (run_id,),
            ).fetchall()
            return metadados, paginas, chunks, numeros

        (doc_id, doc_sha256, tipo, emissor, data), paginas, chunks, numeros = self._executar(consulta)
        return DocumentoProcessado(
            doc_id=doc_id,
            doc_sha256=doc_sha256,
            tipo_documento=tipo,
            emissor=emissor,
            data_documento=data,
            paginas=[Pagina(pagina=p, texto_limpo=t, ocr=o) for p, t, o in paginas],
            chunks=[
                Chunk(chunk_id=c, secao=s, pagina_inicio=pi, offset_inicio=oi, pagina_fim=pf, offset_fim=of, texto=t)
                for c, s, pi, oi, pf, of, t in chunks
            ],
            tabela_numeros=[
                Numero(valor=v, unidade=u, bruto=b, ancora=Ancora(pagina=p, offset_inicio=oi, offset_fim=of))
                for v, u, b, p, oi, of in numeros
            ],
        )

    def carregar_factsheet(self, run_id: str) -> FactSheet:
        def consulta(conexao):
            from psycopg import IsolationLevel
            from psycopg.rows import dict_row

            # As quatro leituras no mesmo snapshot: uma aceitação da quarentena no meio não separa o claim dos
            # seus números.
            conexao.isolation_level = IsolationLevel.REPEATABLE_READ
            with conexao.transaction(), conexao.cursor(row_factory=dict_row) as cursor:
                cabecalho = cursor.execute(
                    "select doc_id, tipo_documento::text as tipo_documento, emissor, data_documento, assinado"
                    " from factsheets where run_id = %s",
                    (run_id,),
                ).fetchone()
                if cabecalho is None:
                    raise FalhaTerminal(f"run {run_id} não tem FactSheet assinado")
                claims = cursor.execute(
                    "select claim_id, texto, tipo::text as tipo, tags::text[] as tags, literal_fonte, pagina,"
                    " offset_inicio, offset_fim from claims where run_id = %s order by length(claim_id), claim_id",
                    (run_id,),
                ).fetchall()
                numeros_claim = cursor.execute(
                    "select claim_id, valor, unidade::text as unidade, bruto, pagina, offset_inicio, offset_fim"
                    " from claim_numeros where run_id = %s order by claim_id, ordem",
                    (run_id,),
                ).fetchall()
                tabela = cursor.execute(
                    "select valor, unidade::text as unidade, bruto, pagina, offset_inicio, offset_fim from numeros"
                    " where run_id = %s order by pagina, offset_inicio, offset_fim",
                    (run_id,),
                ).fetchall()
            return cabecalho, claims, numeros_claim, tabela

        cabecalho, claims, numeros_claim, tabela = self._executar(consulta)

        def numero(linha: dict) -> Numero:
            return Numero(valor=linha["valor"], unidade=linha["unidade"], bruto=linha["bruto"], ancora=_ancora(linha))

        por_claim: dict[str, list[Numero]] = {}
        for linha in numeros_claim:
            por_claim.setdefault(linha["claim_id"], []).append(numero(linha))
        return FactSheet(
            **cabecalho,
            claims=[
                Claim(
                    id=c["claim_id"], texto=c["texto"], numeros=por_claim.get(c["claim_id"], []), tipo=c["tipo"],
                    tags=list(c["tags"]), ancora=_ancora(c), literal_fonte=c["literal_fonte"],
                )
                for c in claims
            ],
            tabela_numeros=[numero(linha) for linha in tabela],
        )

    def carregar_quarentena(self, run_id: str) -> list[ItemQuarentena]:
        def consulta(conexao):
            return conexao.execute(
                "select q.proposto, q.motivo, q.rodada, d.decisao::text from claims_quarentena q"
                " left join decisoes_quarentena d on d.run_id = q.run_id and d.claim_id = q.claim_id"
                " where q.run_id = %s order by q.claim_id",
                (run_id,),
            ).fetchall()

        return [
            ItemQuarentena(ClaimQuarentena(proposto=proposto, motivo=motivo, rodada=rodada), decisao)
            for proposto, motivo, rodada, decisao in self._executar(consulta)
        ]

    def gravar_decisao_quarentena(
        self,
        run_id: str,
        claim_id: str,
        decisao: DecisaoQuarentena,
        autor: str,
        claim: Claim | None,
        numeros_novos: list[Numero],
    ) -> None:
        novos = [
            (run_id, n.bruto, n.valor, n.unidade, n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim)
            for n in numeros_novos
        ]

        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                inserida = cursor.execute(
                    "insert into decisoes_quarentena (run_id, claim_id, decisao, autor)"
                    " values (%s, %s, %s::decisao_quarentena, %s) on conflict (run_id, claim_id) do nothing"
                    " returning decisao",
                    (run_id, claim_id, decisao, autor),
                ).fetchone()
                if inserida is None:
                    (gravada,) = cursor.execute(
                        "select decisao::text from decisoes_quarentena where run_id = %s and claim_id = %s",
                        (run_id, claim_id),
                    ).fetchone()
                    if gravada != decisao:
                        raise FalhaTerminal(f"o claim {claim_id} do run {run_id} já foi revisado: {gravada}")
                    return
                if claim is None:
                    return
                if cursor.execute("select 1 from factsheets where run_id = %s", (run_id,)).fetchone() is None:
                    raise FalhaTerminal(
                        f"run {run_id} não tem FactSheet assinado; o claim {claim_id} não pode ser aceito"
                    )
                cursor.executemany(
                    "insert into numeros (run_id, bruto, valor, unidade, pagina, offset_inicio, offset_fim, origem)"
                    " values (%s, %s, %s, %s::unidade_numero, %s, %s, %s, 'humano')"
                    " on conflict (run_id, pagina, offset_inicio, offset_fim) do nothing",
                    novos,
                )
                self._inserir_claims(cursor, run_id, [claim], "humano")

        self._executar(transacao)

    def gravar_validacao_plano(
        self, run_id: str, rodada: int, problemas: list[ProblemaPlano], saida: SaidaPlanejador
    ) -> None:
        from psycopg.types.json import Jsonb

        linhas = [(run_id, rodada, ordem, p.nivel, p.regra, p.detalhe) for ordem, p in enumerate(problemas, start=1)]

        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                cursor.execute(
                    "insert into rodadas_plano (run_id, rodada, saida) values (%s, %s, %s)"
                    " on conflict (run_id, rodada) do nothing",
                    (run_id, rodada, Jsonb(saida.model_dump(mode="json"))),
                )
                cursor.executemany(
                    "insert into validacoes_plano (run_id, rodada, ordem, nivel, regra, detalhe)"
                    " values (%s, %s, %s, %s::nivel, %s::regra_plano, %s)"
                    " on conflict (run_id, rodada, ordem) do nothing",
                    linhas,
                )

        self._executar(transacao)

    def apagar_validacoes_plano(self, run_id: str) -> None:
        def transacao(conexao):
            with conexao.transaction():
                conexao.execute("delete from validacoes_plano where run_id = %s", (run_id,))
                conexao.execute("delete from rodadas_plano where run_id = %s", (run_id,))

        self._executar(transacao)

    def gravar_plano(self, run_id: str, plano: PlanoEditorial, celulas: list[CelulaState]) -> None:
        from psycopg.types.json import Jsonb

        niveis = [
            (run_id, p.nivel, p.tese, p.tese_claim_ids,
             Jsonb([c.model_dump(mode="json") for c in p.claims]),
             Jsonb([d.model_dump(mode="json") for d in p.descartes]),
             Jsonb([a.model_dump(mode="json") for a in p.analogias]))
            for p in plano.niveis
        ]
        linhas_celulas = [(run_id, c.celula_id, c.nivel, c.formato, c.estado, c.tentativa) for c in celulas]

        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                inserido = cursor.execute(
                    "insert into planos_editoriais (run_id, nucleo, rodadas, aviso) values (%s, %s, %s, %s)"
                    " on conflict (run_id) do nothing returning run_id",
                    (run_id, plano.nucleo, plano.rodadas, plano.aviso),
                ).fetchone()
                if inserido is None:
                    gravado, _ = self._ler_plano(conexao, run_id)
                    if gravado.model_dump(mode="json") != plano.model_dump(mode="json"):
                        raise FalhaTerminal(f"run {run_id} já tem outro plano editorial gravado")
                    return
                # Sem plano gravado não há nível nem célula do run: as inserções não têm conflito.
                cursor.executemany(
                    "insert into planos_nivel (run_id, nivel, tese, tese_claim_ids, claims, descartes, analogias)"
                    " values (%s, %s::nivel, %s, %s, %s, %s, %s)",
                    niveis,
                )
                cursor.executemany(
                    "insert into celulas (run_id, celula_id, nivel, formato, estado, tentativa)"
                    " values (%s, %s, %s::nivel, %s::formato, %s::estado_celula, %s)",
                    linhas_celulas,
                )

        self._executar(transacao)

    @staticmethod
    def _ler_plano(conexao, run_id: str) -> tuple[PlanoEditorial, list[CelulaState]] | tuple[None, None]:
        from psycopg.rows import dict_row

        with conexao.cursor(row_factory=dict_row) as cursor:
            cabecalho = cursor.execute(
                "select nucleo, rodadas, aviso from planos_editoriais where run_id = %s", (run_id,)
            ).fetchone()
            if cabecalho is None:
                return None, None
            niveis = cursor.execute(
                "select p.nivel::text as nivel, p.tese, p.tese_claim_ids, p.claims, p.descartes, p.analogias"
                " from planos_nivel p where p.run_id = %s order by p.nivel",
                (run_id,),
            ).fetchall()
            celulas = cursor.execute(
                "select c.celula_id, c.nivel::text as nivel, c.formato::text as formato,"
                " c.estado::text as estado, c.tentativa, c.motivo_reprovacao::text as motivo_reprovacao,"
                " c.erro_classe::text as erro_classe, c.erro_mensagem from celulas c where c.run_id = %s"
                " order by c.nivel, c.formato",
                (run_id,),
            ).fetchall()
        plano = PlanoEditorial(
            nucleo=list(cabecalho["nucleo"]), rodadas=cabecalho["rodadas"], aviso=cabecalho["aviso"],
            niveis=[{**n, "tese_claim_ids": list(n["tese_claim_ids"])} for n in niveis],
        )
        return plano, [CelulaState(**c) for c in celulas]

    def carregar_plano(self, run_id: str) -> tuple[PlanoEditorial, list[CelulaState]] | None:
        plano, celulas = self._executar(lambda conexao: self._ler_plano(conexao, run_id))
        return None if plano is None else (plano, celulas)

    @staticmethod
    def _atualizar_celula(cursor, run_id: str, celula: CelulaState) -> None:
        """Update da célula num cursor aberto, para a gravação da peça usar a mesma transação."""
        alterada = cursor.execute(
            "update celulas set estado = %s::estado_celula, tentativa = %s,"
            " motivo_reprovacao = %s::motivo_reprovacao, erro_classe = %s::classe_falha, erro_mensagem = %s,"
            " atualizado_em = now() where run_id = %s and celula_id = %s",
            (celula.estado, celula.tentativa, celula.motivo_reprovacao, celula.erro_classe, celula.erro_mensagem,
             run_id, celula.celula_id),
        )
        if alterada.rowcount != 1:
            raise FalhaTerminal(f"célula {celula.celula_id} do run {run_id} não existe em celulas")

    def atualizar_celula(self, run_id: str, celula: CelulaState) -> None:
        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                self._atualizar_celula(cursor, run_id, celula)

        self._executar(transacao)

    def gravar_peca(self, run_id: str, peca: Peca, celula: CelulaState) -> None:
        from psycopg.types.json import Jsonb

        payload = peca.payload.model_dump(mode="json")
        chave = (run_id, peca.celula_id, peca.tentativa)
        unidades = [(i, list(dict.fromkeys(ids))) for i, ids in enumerate(peca.payload.claim_ids_por_unidade())]
        numeros = [
            (*chave, n.campo, n.unidade_indice, n.inicio, n.fim, n.claim_id, n.ordem) for n in peca.numeros_usados
        ]

        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                inserido = cursor.execute(
                    "insert into pecas (run_id, celula_id, tentativa, nivel, formato, payload, claim_ids)"
                    " values (%s, %s, %s, %s::nivel, %s::formato, %s, %s)"
                    " on conflict (run_id, celula_id, tentativa) do nothing returning tentativa",
                    (*chave, peca.nivel, peca.formato, Jsonb(payload), peca.claim_ids),
                ).fetchone()
                if inserido is None:
                    gravado = cursor.execute(
                        "select payload, claim_ids from pecas where run_id = %s and celula_id = %s and tentativa = %s",
                        chave,
                    ).fetchone()
                    if (gravado[0], list(gravado[1])) != (payload, peca.claim_ids):
                        raise FalhaTerminal(
                            f"run {run_id} já tem outra peça gravada em {peca.celula_id} tentativa {peca.tentativa}"
                        )
                    return
                existentes = {
                    linha[0] for linha in cursor.execute(
                        "select claim_id from claims where run_id = %s and claim_id = any(%s)",
                        (run_id, peca.claim_ids),
                    ).fetchall()
                }
                cursor.executemany(
                    "insert into peca_unidade_claims (run_id, celula_id, tentativa, unidade_indice, claim_id)"
                    " values (%s, %s, %s, %s, %s)",
                    [(*chave, indice, c) for indice, ids in unidades for c in ids if c in existentes],
                )
                cursor.executemany(
                    "insert into peca_unidade_numeros (run_id, celula_id, tentativa, campo, unidade_indice, inicio,"
                    " fim, claim_id, ordem) values (%s, %s, %s, %s::campo_numero, %s, %s, %s, %s, %s)",
                    numeros,
                )
                self._atualizar_celula(cursor, run_id, celula)

        self._executar(transacao)

    def gravar_scorecard(self, run_id: str, scorecard: Scorecard) -> None:
        from psycopg.types.json import Jsonb

        chave = (run_id, scorecard.celula_id, scorecard.tentativa)

        def transacao(conexao):
            with conexao.transaction(), conexao.cursor() as cursor:
                if cursor.execute(
                    "select 1 from pecas where run_id = %s and celula_id = %s and tentativa = %s", chave
                ).fetchone() is None:
                    raise FalhaTerminal(
                        f"run {run_id} não tem peça em {scorecard.celula_id} tentativa {scorecard.tentativa}"
                    )
                inserido = cursor.execute(
                    "insert into scorecards (run_id, celula_id, tentativa, veredito, custo_tokens, duracao_ms)"
                    " values (%s, %s, %s, %s::veredito, %s, %s)"
                    " on conflict (run_id, celula_id, tentativa) do nothing returning tentativa",
                    (*chave, scorecard.veredito, scorecard.custo_tokens, scorecard.duracao_ms),
                ).fetchone()
                if inserido is None:
                    gravado = self._ler_scorecard(conexao, *chave)
                    if gravado.model_dump(mode="json") != scorecard.model_dump(mode="json"):
                        raise FalhaTerminal(
                            f"run {run_id} já tem outro scorecard em {scorecard.celula_id}"
                            f" tentativa {scorecard.tentativa}"
                        )
                    return
                for m in scorecard.metricas:
                    booleano = isinstance(m.valor, bool)
                    (metrica_id,) = cursor.execute(
                        "insert into metricas (run_id, celula_id, tentativa, nome, valor_num, valor_bool,"
                        " faixa_esperada, passou, detalhes) values (%s, %s, %s, %s::metrica, %s, %s, %s, %s, %s)"
                        " returning id",
                        (*chave, m.nome, None if booleano else m.valor, m.valor if booleano else None,
                         m.faixa_esperada, m.passou, Jsonb(m.detalhes.model_dump(mode="json"))),
                    ).fetchone()
                    cursor.executemany(
                        "insert into violacoes (metrica_id, metrica, trecho, esperado, obtido, instrucao_corretiva,"
                        " unidade_indice, campo, inicio, fim)"
                        " values (%s, %s::metrica, %s, %s, %s, %s, %s, %s::campo_peca, %s, %s)",
                        [(metrica_id, v.metrica, v.trecho, v.esperado, v.obtido, v.instrucao_corretiva,
                          v.unidade_indice, v.campo, v.inicio, v.fim) for v in m.violacoes],
                    )

        self._executar(transacao)

    @staticmethod
    def _ler_scorecard(conexao, run_id: str, celula_id: str, tentativa: int) -> Scorecard | None:
        from psycopg.rows import dict_row

        with conexao.cursor(row_factory=dict_row) as cursor:
            cabecalho = cursor.execute(
                "select veredito::text as veredito, custo_tokens, duracao_ms from scorecards"
                " where run_id = %s and celula_id = %s and tentativa = %s",
                (run_id, celula_id, tentativa),
            ).fetchone()
            if cabecalho is None:
                return None
            metricas = cursor.execute(
                "select id, nome::text as nome, valor_num, valor_bool, faixa_esperada, passou, detalhes from metricas"
                " where run_id = %s and celula_id = %s and tentativa = %s order by id",
                (run_id, celula_id, tentativa),
            ).fetchall()
            violacoes: dict[int, list[dict]] = {}
            for v in cursor.execute(
                "select metrica_id, metrica::text as metrica, trecho, esperado, obtido, instrucao_corretiva,"
                " unidade_indice, campo::text as campo, inicio, fim from violacoes"
                " where metrica_id = any(%s) order by id",
                ([m["id"] for m in metricas],),
            ).fetchall():
                violacoes.setdefault(v.pop("metrica_id"), []).append(v)
        return Scorecard(
            celula_id=celula_id, tentativa=tentativa, **cabecalho,
            metricas=[
                {"nome": m["nome"], "valor": m["valor_bool"] if m["valor_bool"] is not None else m["valor_num"],
                 "faixa_esperada": m["faixa_esperada"], "passou": m["passou"], "detalhes": m["detalhes"],
                 "violacoes": violacoes.get(m["id"], [])}
                for m in metricas
            ],
        )

    def carregar_scorecard(self, run_id: str, celula_id: str, tentativa: int) -> Scorecard | None:
        return self._executar(lambda conexao: self._ler_scorecard(conexao, run_id, celula_id, tentativa))

    def criar_run(self, manifest: RunManifest) -> None:
        from psycopg.types.json import Jsonb

        def insercao(conexao):
            conexao.execute(
                'insert into runs (run_id, "timestamp", modo, fonte, modelos, prompts, level_specs,'
                ' glossario_versao, arquivos_sha256, "K", max_concorrencia)'
                ' values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)'
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
                    manifest.max_concorrencia,
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

    def abortar_run(self, run_id: str, erro_classe: str, erro_mensagem: str, etapa_erro: str | None) -> None:
        def atualizacao(conexao):
            conexao.execute(
                "update runs set status = 'abortado', erro_classe = %s, erro_mensagem = %s, etapa_erro = %s"
                " where run_id = %s and status = 'em_andamento'",
                (erro_classe, erro_mensagem, etapa_erro, run_id),
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
        """Resumo dos runs, do mais recente ao mais antigo. `claims` é nulo sem FactSheet assinado;
        `quarentena` conta os claims em quarentena ainda sem decisão, nulo quando não há."""

        def consulta(conexao):
            return conexao.execute(
                'select r.run_id, r."timestamp", r.modo, r.fonte, r.status, r.doc_id, r.custo_total_tokens,'
                " r.custo_total_brl, r.erro_classe, r.erro_mensagem, r.etapa_erro,"
                " nullif((select count(*) from claims c where c.run_id = r.run_id), 0) as claims,"
                " nullif((select count(*) from claims_quarentena q where q.run_id = r.run_id and not exists"
                " (select 1 from decisoes_quarentena d where d.run_id = q.run_id and d.claim_id = q.claim_id)), 0)"
                " as quarentena"
                ' from runs r order by r."timestamp" desc, r.run_id limit %s',
                (limite,),
            ).fetchall()

        return self._executar(consulta)

    def niveis_percebidos(self, run_ids: Sequence[str] | None = None) -> list[dict]:
        """Para a matriz de confusão de níveis: o `nivel_percebido` da última tentativa julgada de cada célula,
        com o nível pedido e o formato (`run_id`, `celula_id`, `nivel`, `formato`, `tentativa` e
        `nivel_percebido`). Sem `run_ids`, todos os runs."""

        def consulta(conexao):
            filtro, parametros = ("and m.run_id = any(%s)", (list(run_ids),)) if run_ids else ("", ())
            return conexao.execute(
                "select distinct on (m.run_id, m.celula_id) m.run_id, m.celula_id, c.nivel::text as nivel,"
                " c.formato::text as formato, m.tentativa, m.detalhes->>'nivel_percebido' as nivel_percebido"
                " from metricas m join celulas c on c.run_id = m.run_id and c.celula_id = m.celula_id"
                f" where m.nome = 'llm_judge' {filtro}"
                " order by m.run_id, m.celula_id, m.tentativa desc",
                parametros,
            ).fetchall()

        return self._executar(consulta)

    def niveis_estatisticos(self, run_ids: Sequence[str] | None = None) -> list[dict]:
        """Para a matriz de confusão estatística: o `nivel_estatistico` da M3 (`readability`) na última tentativa de
        cada célula, com o nível pedido e o formato (`run_id`, `celula_id`, `nivel`, `formato`, `tentativa` e
        `nivel_estatistico`). Sem `run_ids`, todos os runs. Só entram as tentativas em que o nível tem lado."""

        def consulta(conexao):
            filtro, parametros = ("and m.run_id = any(%s)", (list(run_ids),)) if run_ids else ("", ())
            return conexao.execute(
                "select distinct on (m.run_id, m.celula_id) m.run_id, m.celula_id, c.nivel::text as nivel,"
                " c.formato::text as formato, m.tentativa, m.detalhes->>'nivel_estatistico' as nivel_estatistico"
                " from metricas m join celulas c on c.run_id = m.run_id and c.celula_id = m.celula_id"
                f" where m.nome = 'readability' and m.detalhes->>'nivel_estatistico' is not null {filtro}"
                " order by m.run_id, m.celula_id, m.tentativa desc",
                parametros,
            ).fetchall()

        return self._executar(consulta)

    def balanco(self) -> dict:
        """Balanço de todos os runs: runs por status e por fonte, custo total, células por estado, tentativas por
        célula e a taxa de aprovação de cada métrica na última tentativa de cada célula."""

        def consulta(conexao):
            runs = conexao.execute(
                "select status::text as status, fonte::text as fonte, count(*) as total,"
                " coalesce(sum(custo_total_tokens), 0) as tokens, coalesce(sum(custo_total_brl), 0) as brl"
                " from runs group by status, fonte"
            ).fetchall()
            celulas = conexao.execute(
                "select estado::text as estado, count(*) as total, avg(tentativa) as tentativas"
                " from celulas group by estado"
            ).fetchall()
            metricas = conexao.execute(
                "select m.nome::text as nome, count(*) as total, count(*) filter (where m.passou) as passou"
                " from (select distinct on (run_id, celula_id, nome) run_id, celula_id, nome, passou, tentativa"
                " from metricas order by run_id, celula_id, nome, tentativa desc) m group by m.nome order by m.nome"
            ).fetchall()
            return {"runs": runs, "celulas": celulas, "metricas": metricas}

        return self._executar(consulta)

    def biblioteca(self) -> list[dict]:
        """Uma linha por célula com peça gravada, em todos os runs, na última tentativa e sem o conteúdo (`run_id`,
        `celula_id`, `nivel`, `formato`, `estado`, `tentativa`, `titulo`, `fonte`, `timestamp`). Do run mais recente
        ao mais antigo."""

        def consulta(conexao):
            return conexao.execute(
                'select p.run_id, p.celula_id, c.nivel::text as nivel, c.formato::text as formato,'
                ' c.estado::text as estado, p.tentativa, p.payload->>\'titulo\' as titulo, r.fonte::text as fonte,'
                ' r."timestamp" from (select distinct on (run_id, celula_id) run_id, celula_id, tentativa, payload'
                " from pecas order by run_id, celula_id, tentativa desc) p"
                " join celulas c on c.run_id = p.run_id and c.celula_id = p.celula_id"
                " join runs r on r.run_id = p.run_id"
                ' order by r."timestamp" desc, p.run_id, p.celula_id'
            ).fetchall()

        return self._executar(consulta)

    def detalhar_run(self, run_id: str) -> dict | None:
        """Run, documento processado (resumido), FactSheet, validações por rodada, quarentena, spans, plano
        editorial, rodadas do gate do plano e as células (peças, números usados e scorecards por tentativa).

        None se o run não existe.
        """

        def consulta(conexao):
            manifest = conexao.execute(
                'select run_id, "timestamp", modo, fonte, status, doc_id, doc_sha256, coleta_id, url_origem,'
                ' coletado_em, modelos, prompts, level_specs, glossario_versao, arquivos_sha256, "K",'
                " max_concorrencia, custo_total_tokens, custo_total_brl, erro_classe, erro_mensagem, etapa_erro"
                " from runs where run_id = %s",
                (run_id,),
            ).fetchone()
            if manifest is None:
                return None
            return {
                "manifest": manifest,
                "documento": self._documento(conexao, run_id),
                "factsheet": self._factsheet(conexao, run_id),
                "validacoes": self._validacoes(conexao, run_id),
                "quarentena": self._quarentena(conexao, run_id),
                "spans": conexao.execute(
                    "select span_id, no, nivel, celula_id, tentativa, rodada, modelo, tokens_entrada, tokens_saida,"
                    " custo_brl, duracao_ms, falha_classe, falha_mensagem, criado_em"
                    " from llm_spans where run_id = %s order by criado_em, span_id",
                    (run_id,),
                ).fetchall(),
                "plano": self._plano(conexao, run_id),
                "validacoes_plano": self._validacoes_plano(conexao, run_id),
                "celulas": self._celulas(conexao, run_id),
            }

        return self._executar(consulta)

    def peca(self, run_id: str, celula_id: str, tentativa: int | None = None) -> dict | None:
        """Peça de uma célula (`celula_id`, `tentativa`, `formato`, `payload` e `claim_ids`); sem `tentativa`, a
        última gravada. None se a célula não tem essa peça."""

        def consulta(conexao):
            filtro, parametros = ("and tentativa = %s", (tentativa,)) if tentativa is not None else ("", ())
            return conexao.execute(
                "select celula_id, tentativa, formato::text as formato, payload, claim_ids from pecas"
                f" where run_id = %s and celula_id = %s {filtro} order by tentativa desc limit 1",
                (run_id, celula_id, *parametros),
            ).fetchone()

        return self._executar(consulta)

    @staticmethod
    def _celulas(conexao, run_id: str) -> list[dict]:
        """As células do run com o estado e, por tentativa, a peça, os números usados e o scorecard (métricas e
        violações com a posição). Vazio antes de a E4 gravar o plano."""
        celulas = conexao.execute(
            "select celula_id, nivel::text as nivel, formato::text as formato, estado::text as estado, tentativa,"
            " motivo_reprovacao::text as motivo_reprovacao, erro_classe::text as erro_classe, erro_mensagem"
            " from celulas where run_id = %s order by celula_id",
            (run_id,),
        ).fetchall()
        tentativas: dict[str, dict[int, dict]] = {}
        for p in conexao.execute(
            "select celula_id, tentativa, payload, claim_ids from pecas where run_id = %s order by tentativa",
            (run_id,),
        ):
            tentativas.setdefault(p["celula_id"], {})[p["tentativa"]] = {
                "tentativa": p["tentativa"], "payload": p["payload"], "claim_ids": p["claim_ids"],
                "numeros_usados": [], "scorecard": None,
            }
        for n in conexao.execute(
            "select celula_id, tentativa, campo::text as campo, unidade_indice, inicio, fim, claim_id, ordem"
            " from peca_unidade_numeros where run_id = %s order by celula_id, tentativa, unidade_indice, inicio",
            (run_id,),
        ):
            tentativas[n["celula_id"]][n["tentativa"]]["numeros_usados"].append(
                {k: n[k] for k in ("campo", "unidade_indice", "inicio", "fim", "claim_id", "ordem")}
            )
        violacoes: dict[int, list[dict]] = {}
        for v in conexao.execute(
            "select v.metrica_id, v.metrica::text as metrica, v.trecho, v.esperado, v.obtido, v.instrucao_corretiva,"
            " v.unidade_indice, v.campo::text as campo, v.inicio, v.fim from violacoes v join metricas m"
            " on m.id = v.metrica_id where m.run_id = %s order by v.id",
            (run_id,),
        ):
            violacoes.setdefault(v.pop("metrica_id"), []).append(v)
        for s in conexao.execute(
            "select celula_id, tentativa, veredito::text as veredito, custo_tokens, duracao_ms from scorecards"
            " where run_id = %s",
            (run_id,),
        ):
            tentativas[s["celula_id"]][s["tentativa"]]["scorecard"] = {
                "veredito": s["veredito"], "custo_tokens": s["custo_tokens"], "duracao_ms": s["duracao_ms"],
                "metricas": [],
            }
        for m in conexao.execute(
            "select id, celula_id, tentativa, nome::text as nome, valor_num, valor_bool, faixa_esperada, passou,"
            " detalhes from metricas where run_id = %s order by id",
            (run_id,),
        ):
            tentativas[m["celula_id"]][m["tentativa"]]["scorecard"]["metricas"].append({
                "nome": m["nome"],
                "valor": m["valor_bool"] if m["valor_bool"] is not None else m["valor_num"],
                "faixa_esperada": m["faixa_esperada"], "passou": m["passou"], "detalhes": m["detalhes"],
                "violacoes": violacoes.get(m["id"], []),
            })
        return [{**c, "tentativas": list(tentativas.get(c["celula_id"], {}).values())} for c in celulas]

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
                "origem": c["origem"],
                "numeros": numeros.get(c["claim_id"], []),
            }
            for c in conexao.execute(
                "select claim_id, texto, tipo, tags, literal_fonte, pagina, offset_inicio, offset_fim, origem"
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

    @staticmethod
    def _plano(conexao, run_id: str) -> dict | None:
        """Plano editorial da E4 com os 3 planos de nível, na ordem iniciante, intermediario e avancado.
        None se a E4 não gravou o plano do run."""
        plano = conexao.execute(
            "select nucleo, rodadas, aviso from planos_editoriais where run_id = %s", (run_id,)
        ).fetchone()
        if plano is None:
            return None
        niveis = conexao.execute(
            "select p.nivel::text as nivel, p.tese, p.tese_claim_ids, p.claims, p.descartes, p.analogias"
            " from planos_nivel p where p.run_id = %s order by p.nivel",
            (run_id,),
        ).fetchall()
        return {**plano, "niveis": niveis}

    @staticmethod
    def _validacoes_plano(conexao, run_id: str) -> list[dict]:
        """Problemas do gate da E4 por rodada. A rodada que passou não tem linha."""
        rodadas: dict[int, list[dict]] = {}
        for v in conexao.execute(
            "select rodada, ordem, nivel::text as nivel, regra::text as regra, detalhe from validacoes_plano"
            " where run_id = %s order by rodada, ordem",
            (run_id,),
        ):
            rodadas.setdefault(v["rodada"], []).append(
                {"nivel": v["nivel"], "regra": v["regra"], "detalhe": v["detalhe"]}
            )
        return [{"rodada": rodada, "problemas": problemas} for rodada, problemas in rodadas.items()]

    @staticmethod
    def _quarentena(conexao, run_id: str) -> list[dict]:
        """Claims em quarentena, em ordem de id, com a decisão humana (nula enquanto não revisado)."""
        return conexao.execute(
            "select q.claim_id, q.rodada, q.motivo, q.proposto, d.decisao, d.autor, d.decidido_em"
            " from claims_quarentena q left join decisoes_quarentena d"
            " on d.run_id = q.run_id and d.claim_id = q.claim_id where q.run_id = %s order by q.claim_id",
            (run_id,),
        ).fetchall()


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
