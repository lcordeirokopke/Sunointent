"""Configuração comum dos testes.

- Trava: interrompe o pytest antes da coleta se SUPABASE_URL ou SUPABASE_DB_URL apontarem
  para um host diferente de localhost, 127.0.0.1 ou ::1. O .env não é carregado aqui.
- Marker `supabase`: testes de integração com a cópia local do Supabase CLI.
- Marker `gemini`: testes que chamam a Gemini API de verdade (custam tokens); só rodam com `-m gemini`.
- Repositório em memória no lugar do Supabase e retentativa sem espera.
- `ProviderFalso`: provider de LLM com respostas prontas, no lugar do Gemini.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pytest

from sunontent import retentativa
from sunontent.llm import ClienteLLM, Preco, RespostaBruta
from sunontent.persistencia import Coleta
from sunontent.retentativa import FalhaRetriavel, FalhaTerminal

HOSTS_LOCAIS = {"localhost", "127.0.0.1", "::1"}
FIXTURES = Path(__file__).parent / "fixtures"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "supabase: integração com a cópia local do Supabase CLI (supabase start)"
    )
    config.addinivalue_line(
        "markers", "gemini: chama a Gemini API de verdade (GOOGLE_API_KEY); só roda com -m gemini"
    )
    for variavel in ("SUPABASE_URL", "SUPABASE_DB_URL"):
        valor = os.environ.get(variavel)
        if not valor:
            continue
        host = urlparse(valor).hostname
        if host not in HOSTS_LOCAIS:
            raise pytest.UsageError(
                f"{variavel} aponta para {host!r}. Os testes só usam o Supabase local "
                f"({', '.join(sorted(HOSTS_LOCAIS))})."
            )


def pytest_collection_modifyitems(config, items):
    """Testes `gemini` gastam tokens: ficam de fora, a menos que o -m os peça."""
    if "gemini" in (config.getoption("-m") or ""):
        return
    pular = pytest.mark.skip(reason="chama a Gemini API; rode com -m gemini")
    for item in items:
        if "gemini" in item.keywords:
            item.add_marker(pular)


@pytest.fixture(autouse=True)
def sem_espera(monkeypatch):
    esperas = []
    monkeypatch.setattr(retentativa, "_dormir", esperas.append)
    return esperas


@dataclass
class RepositorioMemoria:
    """Repositório em memória com as mesmas regras do Supabase usadas pela E0.

    `falhas` injeta exceções por método, consumidas uma a cada chamada.
    `perder_resposta_da_transacao` grava a transação e depois levanta FalhaRetriavel.
    """

    runs: dict[str, dict] = field(default_factory=dict)
    documentos: dict[str, dict] = field(default_factory=dict)
    objetos: dict[str, bytes] = field(default_factory=dict)
    coletas: list[Coleta] = field(default_factory=list)
    falhas: dict[str, list[Exception]] = field(default_factory=dict)
    perder_resposta_da_transacao: int = 0
    chamadas: dict[str, int] = field(default_factory=dict)
    paginas: dict[tuple, dict] = field(default_factory=dict)
    chunks: dict[tuple, dict] = field(default_factory=dict)
    numeros: dict[tuple, dict] = field(default_factory=dict)
    spans: dict[str, object] = field(default_factory=dict)
    validacoes: dict[tuple, dict] = field(default_factory=dict)
    factsheets: dict[str, object] = field(default_factory=dict)

    def registrar_run(self, run_id: str) -> None:
        self.runs[run_id] = {"coleta_id": None}

    def _entrar(self, metodo: str) -> None:
        self.chamadas[metodo] = self.chamadas.get(metodo, 0) + 1
        fila = self.falhas.get(metodo)
        if fila:
            raise fila.pop(0)

    def documento_existe(self, doc_sha256: str) -> bool:
        self._entrar("documento_existe")
        return doc_sha256 in self.documentos

    def enviar_pdf(self, caminho_storage: str, dados: bytes) -> None:
        self._entrar("enviar_pdf")
        if caminho_storage in self.objetos:
            return  # Asset Already Exists: reaproveita
        self.objetos[caminho_storage] = dados

    def registrar_coleta(self, *, run_id, fonte, tipo_documento, doc_sha256, tamanho_bytes,
                         doc_id, url_origem, coletado_em: datetime) -> Coleta:
        self._entrar("registrar_coleta")
        run = self.runs.get(run_id)
        if run is None or run["coleta_id"] is not None:
            raise FalhaTerminal(f"run {run_id} não existe em runs ou já tem coleta vinculada")
        self.documentos.setdefault(
            doc_sha256,
            {"fonte": fonte, "tipo_documento": tipo_documento,
             "storage_path": f"{fonte}/{doc_sha256}.pdf", "tamanho_bytes": tamanho_bytes},
        )
        coleta = Coleta(len(self.coletas) + 1, doc_id, doc_sha256, url_origem, coletado_em)
        self.coletas.append(coleta)
        run["coleta_id"] = coleta.coleta_id
        run["coleta"] = coleta
        if self.perder_resposta_da_transacao:
            self.perder_resposta_da_transacao -= 1
            raise FalhaRetriavel("conexão perdida depois do commit")
        return coleta

    def coleta_do_run(self, run_id: str) -> Coleta | None:
        self._entrar("coleta_do_run")
        run = self.runs.get(run_id, {})
        return run.get("coleta")

    def registrar_documento_referencia(self, *, run_id, fonte, tipo_documento, doc_sha256,
                                       tamanho_bytes, doc_id, url_origem, coletado_em: datetime) -> None:
        self._entrar("registrar_documento_referencia")
        run = self.runs.get(run_id)
        if run is None or run.get("doc_sha256") not in (None, doc_sha256):
            raise FalhaTerminal(f"run {run_id} não existe em runs ou já está vinculado a outro documento")
        self.documentos.setdefault(
            doc_sha256,
            {"fonte": fonte, "tipo_documento": tipo_documento,
             "storage_path": f"{fonte}/{doc_sha256}.pdf", "tamanho_bytes": tamanho_bytes},
        )
        run.update(doc_id=doc_id, doc_sha256=doc_sha256, url_origem=url_origem, coletado_em=coletado_em)

    def gravar_documento_processado(self, run_id, documento) -> None:
        """Mesmas regras do Supabase: o run existe, linhas repetidas ficam como estão, tudo ou nada."""
        self._entrar("gravar_documento_processado")
        if run_id not in self.runs:
            raise FalhaTerminal(f"run {run_id} não existe em runs")
        paginas = {(run_id, p.pagina): {"texto_limpo": p.texto_limpo, "ocr": p.ocr} for p in documento.paginas}
        chunks = {(run_id, c.chunk_id): c.model_dump() for c in documento.chunks}
        numeros = {
            (run_id, n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim): n.model_dump()
            for n in documento.tabela_numeros
        }
        for destino, novos in ((self.paginas, paginas), (self.chunks, chunks), (self.numeros, numeros)):
            for chave, valor in novos.items():
                destino.setdefault(chave, valor)

    def gravar_span(self, span) -> None:
        """Mesma regra do Supabase: span repetido não soma de novo o custo do run."""
        self._entrar("gravar_span")
        run = self.runs.get(span.run_id)
        if run is None:
            raise FalhaTerminal(f"run {span.run_id} não existe em runs")
        if span.span_id in self.spans:
            return
        self.spans[span.span_id] = span
        run["custo_total_tokens"] = run.get("custo_total_tokens", 0) + span.tokens_entrada + span.tokens_saida
        run["custo_total_brl"] = run.get("custo_total_brl", Decimal(0)) + span.custo_brl

    def gravar_validacao(self, run_id, rodada, resultados) -> None:
        self._entrar("gravar_validacao")
        if run_id not in self.runs:
            raise FalhaTerminal(f"run {run_id} não existe em runs")
        for claim_id, motivo in resultados:
            self.validacoes.setdefault((run_id, rodada, claim_id), {"passou": motivo is None, "motivo": motivo})

    def gravar_factsheet(self, run_id, factsheet) -> None:
        """Mesmas regras do Supabase: todo número do claim existe em numeros na mesma posição."""
        self._entrar("gravar_factsheet")
        if run_id not in self.runs:
            raise FalhaTerminal(f"run {run_id} não existe em runs")
        for claim in factsheet.claims:
            for n in claim.numeros:
                if (run_id, n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim) not in self.numeros:
                    raise FalhaTerminal(f"claim_numeros.numero_id nulo: {claim.id} {n.bruto!r}")
        self.factsheets.setdefault(run_id, factsheet)

    def criar_run(self, manifest) -> None:
        self._entrar("criar_run")
        self.runs.setdefault(manifest.run_id, {"coleta_id": None, "manifest": manifest,
                                               "status": "em_andamento"})

    def concluir_run(self, run_id) -> None:
        self._entrar("concluir_run")
        run = self.runs.get(run_id)
        if run is None or run["status"] != "em_andamento":
            raise FalhaTerminal(f"run {run_id} não existe em runs ou não está em_andamento")
        run["status"] = "concluido"

    def abortar_run(self, run_id, erro_classe, erro_mensagem) -> None:
        self._entrar("abortar_run")
        run = self.runs.get(run_id)
        if run is not None and run["status"] == "em_andamento":
            run.update(status="abortado", erro_classe=erro_classe, erro_mensagem=erro_mensagem)


@pytest.fixture
def repositorio():
    repo = RepositorioMemoria()
    repo.registrar_run("copom_20261004T142205Z_9f2c1a")
    return repo


@pytest.fixture
def run_id():
    return "copom_20261004T142205Z_9f2c1a"


def referencia(fonte: str) -> tuple[bytes, dict] | None:
    """Bytes e metadados do documento de referência, ou None se ainda não foi congelado."""
    pdf = FIXTURES / "referencia" / f"{fonte}.pdf"
    meta = FIXTURES / "referencia" / f"{fonte}.json"
    if not (pdf.exists() and meta.exists()):
        return None
    return pdf.read_bytes(), json.loads(meta.read_text(encoding="utf-8"))


class ProviderFalso:
    """Provider de LLM com respostas prontas, consumidas em ordem.

    Cada resposta é uma string (o JSON devolvido), um RespostaBruta ou uma exceção (levantada).
    Guarda os prompts recebidos em `prompts`.
    """

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.prompts: list[str] = []

    def gerar(self, modelo, prompt, json_schema):
        self.prompts.append(prompt)
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        if isinstance(resposta, str):
            return RespostaBruta(resposta, 1000, 200)
        return resposta


def cliente_falso(respostas) -> ClienteLLM:
    """ClienteLLM do extractor com o ProviderFalso; preço de 1 US$ por milhão nos dois sentidos, cotação 5."""
    return ClienteLLM(
        ProviderFalso(respostas),
        {"extractor": "modelo-falso"},
        {"modelo-falso": Preco(Decimal(1), Decimal(1))},
        Decimal(5),
    )
