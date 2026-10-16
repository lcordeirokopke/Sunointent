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
from sunontent.persistencia import Coleta, ItemQuarentena
from sunontent.retentativa import FalhaRetriavel, FalhaTerminal
from sunontent.schemas import Chunk, DocumentoProcessado, FactSheet, Numero, Pagina, PlanoEditorial, Scorecard

HOSTS_LOCAIS = {"localhost", "127.0.0.1", "::1"}
FIXTURES = Path(__file__).parent / "fixtures"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "supabase: integração com a cópia local do Supabase CLI (supabase start)"
    )
    config.addinivalue_line(
        "markers", "gemini: chama a Gemini API de verdade (GOOGLE_API_KEY_PRINC e GOOGLE_API_KEY_SEC); só roda com -m gemini"
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
    Da E1 em diante, guarda o que o pipeline grava, com as mesmas recusas do banco. O FactSheet, o plano e a saída
    do planejador ficam como JSON (model_dump(mode="json")) e voltam por model_validate, como pelo Postgres.
    """

    runs: dict[str, dict] = field(default_factory=dict)
    documentos: dict[str, dict] = field(default_factory=dict)
    objetos: dict[str, bytes] = field(default_factory=dict)
    coletas: list[Coleta] = field(default_factory=list)
    falhas: dict[str, list[Exception]] = field(default_factory=dict)
    perder_resposta_da_transacao: int = 0
    chamadas: dict[str, int] = field(default_factory=dict)
    documentos_processados: dict[str, dict] = field(default_factory=dict)
    paginas: dict[tuple, dict] = field(default_factory=dict)
    chunks: dict[tuple, dict] = field(default_factory=dict)
    numeros: dict[tuple, dict] = field(default_factory=dict)
    spans: dict[str, object] = field(default_factory=dict)
    validacoes: dict[tuple, dict] = field(default_factory=dict)
    factsheets: dict[str, object] = field(default_factory=dict)
    quarentena: dict[tuple, object] = field(default_factory=dict)
    decisoes_quarentena: dict[tuple, dict] = field(default_factory=dict)
    claims_humanos: dict[tuple, object] = field(default_factory=dict)
    validacoes_plano: dict[tuple, object] = field(default_factory=dict)
    rodadas_plano: dict[tuple, dict] = field(default_factory=dict)
    planos: dict[str, dict] = field(default_factory=dict)
    celulas: dict[tuple, object] = field(default_factory=dict)
    pecas: dict[tuple, dict] = field(default_factory=dict)
    peca_unidade_claims: dict[tuple, None] = field(default_factory=dict)
    peca_unidade_numeros: dict[tuple, dict] = field(default_factory=dict)
    scorecards: dict[tuple, dict] = field(default_factory=dict)

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
        """Mesmas regras do Supabase: o documento é o vinculado ao run, linhas repetidas ficam como estão, tudo ou nada."""
        self._entrar("gravar_documento_processado")
        run = self.runs.get(run_id)
        if run is None:
            raise FalhaTerminal(f"run {run_id} não existe em runs")
        coleta = run.get("coleta")
        vinculado = (coleta.doc_id, coleta.doc_sha256) if coleta else (run.get("doc_id"), run.get("doc_sha256"))
        if vinculado != (documento.doc_id, documento.doc_sha256):
            raise FalhaTerminal(f"documento {documento.doc_id} não é o vinculado ao run {run_id}")
        self.documentos_processados.setdefault(run_id, {
            "doc_id": documento.doc_id, "doc_sha256": documento.doc_sha256,
            "tipo_documento": documento.tipo_documento, "emissor": documento.emissor,
            "data_documento": documento.data_documento,
        })
        paginas = {(run_id, p.pagina): {"texto_limpo": p.texto_limpo, "ocr": p.ocr} for p in documento.paginas}
        chunks = {(run_id, c.chunk_id): c.model_dump() for c in documento.chunks}
        numeros = {
            (run_id, n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim): {**n.model_dump(), "origem": "pipeline"}
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
        if span.rodada is not None and span.no != "adapter":
            raise FalhaTerminal("llm_spans: rodada só no adapter")
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
        self.factsheets.setdefault(run_id, factsheet.model_dump(mode="json", exclude={"tabela_numeros"}))

    def factsheet_gravado(self, run_id) -> FactSheet:
        """O FactSheet como foi gravado, sem os claims aceitos na quarentena (atalho dos testes)."""
        return FactSheet.model_validate({**self.factsheets[run_id], "tabela_numeros": []})

    def plano_gravado(self, run_id) -> PlanoEditorial:
        """Atalho dos testes."""
        return PlanoEditorial.model_validate(self.planos[run_id])

    def carregar_factsheet(self, run_id):
        """Mesma leitura do Supabase: claims em ordem de id, com os aceitos na quarentena, e a tabela de números
        do run (de origem pipeline e humano)."""
        self._entrar("carregar_factsheet")
        if run_id not in self.factsheets:
            raise FalhaTerminal(f"run {run_id} não tem FactSheet assinado")
        dados = self.factsheets[run_id]
        humanos = [c.model_dump(mode="json") for (r, _), c in self.claims_humanos.items() if r == run_id]
        claims = sorted([*dados["claims"], *humanos], key=lambda c: (len(c["id"]), c["id"]))
        tabela = [
            Numero(**{c: v for c, v in valor.items() if c != "origem"}).model_dump(mode="json")
            for k, valor in sorted(self.numeros.items()) if k[0] == run_id
        ]
        return FactSheet.model_validate({**dados, "claims": claims, "tabela_numeros": tabela})

    def gravar_quarentena(self, run_id, itens) -> None:
        self._entrar("gravar_quarentena")
        if run_id not in self.runs:
            raise FalhaTerminal(f"run {run_id} não existe em runs")
        for item in itens:
            self.quarentena.setdefault((run_id, item.proposto.id), item)

    def carregar_documento_processado(self, run_id):
        self._entrar("carregar_documento_processado")
        meta = self.documentos_processados.get(run_id)
        if meta is None:
            raise FalhaTerminal(f"run {run_id} não tem documento processado")
        return DocumentoProcessado(
            doc_id=meta["doc_id"], doc_sha256=meta["doc_sha256"], tipo_documento=meta["tipo_documento"],
            emissor=meta["emissor"], data_documento=meta["data_documento"],
            paginas=[Pagina(pagina=k[1], **v) for k, v in sorted(self.paginas.items()) if k[0] == run_id],
            chunks=[Chunk(**v) for k, v in self.chunks.items() if k[0] == run_id],
            tabela_numeros=[
                Numero(**{c: v for c, v in valor.items() if c != "origem"})
                for k, valor in sorted(self.numeros.items()) if k[0] == run_id
            ],
        )

    def carregar_quarentena(self, run_id):
        self._entrar("carregar_quarentena")
        return [
            ItemQuarentena(item, self.decisoes_quarentena.get(chave, {}).get("decisao"))
            for chave, item in sorted(self.quarentena.items()) if chave[0] == run_id
        ]

    def gravar_decisao_quarentena(self, run_id, claim_id, decisao, autor, claim, numeros_novos) -> None:
        """Mesmas regras do Supabase: uma decisão por claim; aceitar exige o FactSheet e grava tudo ou nada."""
        self._entrar("gravar_decisao_quarentena")
        chave = (run_id, claim_id)
        if chave not in self.quarentena:
            raise FalhaTerminal(f"claim {claim_id} não está em claims_quarentena")
        gravada = self.decisoes_quarentena.get(chave)
        if gravada is not None:
            if gravada["decisao"] != decisao:
                raise FalhaTerminal(f"o claim {claim_id} do run {run_id} já foi revisado: {gravada['decisao']}")
            return
        if claim is not None:
            if run_id not in self.factsheets:
                raise FalhaTerminal(f"run {run_id} não tem FactSheet assinado; o claim {claim_id} não pode ser aceito")
            novos = {
                (run_id, n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim): {**n.model_dump(), "origem": "humano"}
                for n in numeros_novos
            }
            for n in claim.numeros:
                posicao = (run_id, n.ancora.pagina, n.ancora.offset_inicio, n.ancora.offset_fim)
                if posicao not in self.numeros and posicao not in novos:
                    raise FalhaTerminal(f"claim_numeros.numero_id nulo: {claim.id} {n.bruto!r}")
            for posicao, valor in novos.items():
                self.numeros.setdefault(posicao, valor)
            self.claims_humanos[chave] = claim
        self.decisoes_quarentena[chave] = {"decisao": decisao, "autor": autor}

    def gravar_validacao_plano(self, run_id, rodada, problemas, saida) -> None:
        self._entrar("gravar_validacao_plano")
        if run_id not in self.runs:
            raise FalhaTerminal(f"run {run_id} não existe em runs")
        if not 1 <= rodada <= 3:
            raise FalhaTerminal(f"validacoes_plano.rodada fora de 1 a 3: {rodada}")
        self.rodadas_plano.setdefault((run_id, rodada), saida.model_dump(mode="json"))
        for ordem, problema in enumerate(problemas, start=1):
            self.validacoes_plano.setdefault((run_id, rodada, ordem), problema)

    def apagar_validacoes_plano(self, run_id) -> None:
        self._entrar("apagar_validacoes_plano")
        for tabela in (self.validacoes_plano, self.rodadas_plano):
            for chave in [c for c in tabela if c[0] == run_id]:
                del tabela[chave]

    def gravar_plano(self, run_id, plano, celulas) -> None:
        """Mesmas regras do Supabase: o plano exige o FactSheet do run; tudo numa transação; outro plano já
        gravado é FalhaTerminal, o mesmo plano não grava nada."""
        self._entrar("gravar_plano")
        if run_id not in self.factsheets:
            raise FalhaTerminal(f"run {run_id} não tem FactSheet em factsheets; o plano não pode ser gravado")
        if not 1 <= len(plano.nucleo) <= 3:
            raise FalhaTerminal("planos_editoriais.nucleo precisa ter de 1 a 3 claims")
        novo = plano.model_dump(mode="json")
        if run_id in self.planos:
            if self.planos[run_id] != novo:
                raise FalhaTerminal(f"run {run_id} já tem outro plano editorial gravado")
            return
        self.planos[run_id] = novo
        for celula in celulas:
            self.celulas[(run_id, celula.celula_id)] = celula.model_copy()

    def carregar_plano(self, run_id):
        self._entrar("carregar_plano")
        if run_id not in self.planos:
            return None
        celulas = [c.model_copy() for (r, _), c in self.celulas.items() if r == run_id]
        return PlanoEditorial.model_validate(self.planos[run_id]), celulas

    def atualizar_celula(self, run_id, celula) -> None:
        self._entrar("atualizar_celula")
        chave = (run_id, celula.celula_id)
        if chave not in self.celulas:
            raise FalhaTerminal(f"célula {celula.celula_id} do run {run_id} não existe em celulas")
        self.celulas[chave] = celula.model_copy()

    def gravar_peca(self, run_id, peca, celula) -> None:
        """Mesmas regras do Supabase: a célula existe; a mesma peça já gravada não grava nada, outra na mesma
        tentativa é FalhaTerminal; cada número usado aponta para um número de claim do run; peca_unidade_claims
        recebe só os claims do run; tudo, com a célula, numa transação."""
        self._entrar("gravar_peca")
        if (run_id, peca.celula_id) not in self.celulas:
            raise FalhaTerminal(f"célula {peca.celula_id} do run {run_id} não existe em celulas")
        chave = (run_id, peca.celula_id, peca.tentativa)
        nova = peca.model_dump(mode="json")
        if chave in self.pecas:
            if self.pecas[chave] != nova:
                raise FalhaTerminal(f"run {run_id} já tem outra peça gravada em {peca.celula_id} tentativa {peca.tentativa}")
            return
        claims = {c["id"]: len(c["numeros"]) for c in self.factsheets.get(run_id, {}).get("claims", [])}
        claims |= {c.id: len(c.numeros) for (r, _), c in self.claims_humanos.items() if r == run_id}
        for n in peca.numeros_usados:
            if not 1 <= n.ordem <= claims.get(n.claim_id, 0):
                raise FalhaTerminal(f"peca_unidade_numeros: {n.claim_id}.{n.ordem} não existe em claim_numeros")
        posicoes = [(n.campo, n.unidade_indice, n.inicio) for n in peca.numeros_usados]
        if len(set(posicoes)) != len(posicoes):
            raise FalhaTerminal("peca_unidade_numeros: posição repetida")
        self.pecas[chave] = nova
        for indice, ids in enumerate(peca.payload.claim_ids_por_unidade()):
            for claim_id in dict.fromkeys(ids):
                if claim_id in claims:
                    self.peca_unidade_claims[(*chave, indice, claim_id)] = None
        for n in peca.numeros_usados:
            self.peca_unidade_numeros[(*chave, n.campo, n.unidade_indice, n.inicio)] = n.model_dump()
        self.celulas[(run_id, peca.celula_id)] = celula.model_copy()

    def gravar_scorecard(self, run_id, scorecard) -> None:
        """Mesmas regras do Supabase: exige a peça da tentativa; o mesmo scorecard já gravado não grava nada, outro
        na mesma tentativa é FalhaTerminal; scorecard, métricas e violações numa transação."""
        self._entrar("gravar_scorecard")
        chave = (run_id, scorecard.celula_id, scorecard.tentativa)
        if chave not in self.pecas:
            raise FalhaTerminal(f"run {run_id} não tem peça em {scorecard.celula_id} tentativa {scorecard.tentativa}")
        novo = scorecard.model_dump(mode="json")
        if chave in self.scorecards:
            if self.scorecards[chave] != novo:
                raise FalhaTerminal(
                    f"run {run_id} já tem outro scorecard em {scorecard.celula_id} tentativa {scorecard.tentativa}")
            return
        self.scorecards[chave] = novo

    def carregar_scorecard(self, run_id, celula_id, tentativa):
        self._entrar("carregar_scorecard")
        gravado = self.scorecards.get((run_id, celula_id, tentativa))
        return None if gravado is None else Scorecard.model_validate(gravado)

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

    def abortar_run(self, run_id, erro_classe, erro_mensagem, etapa_erro) -> None:
        self._entrar("abortar_run")
        run = self.runs.get(run_id)
        if run is not None and run["status"] == "em_andamento":
            run.update(status="abortado", erro_classe=erro_classe, erro_mensagem=erro_mensagem, etapa_erro=etapa_erro)


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
    Guarda os prompts recebidos em `prompts` e o nível de raciocínio de cada chamada em `raciocinios`.
    """

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.prompts: list[str] = []
        self.raciocinios: list[str | None] = []

    def gerar(self, modelo, prompt, json_schema, raciocinio=None):
        self.prompts.append(prompt)
        self.raciocinios.append(raciocinio)
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        if isinstance(resposta, str):
            return RespostaBruta(resposta, 1000, 200)
        return resposta


def resposta_plano(*claim_ids: str, nucleo=None) -> str:
    """Resposta do planejador (E4) que usa os claims dados nos 3 níveis, com o primeiro no núcleo."""
    ids = list(claim_ids or ("C01",))
    nivel = lambda n: {  # noqa: E731
        "nivel": n, "descartes": [],
        "claims": [{"claim_id": c, "papel": "principal", "prioridade": i} for i, c in enumerate(ids, start=1)],
        "tese": f"Tese do {n}.", "tese_claim_ids": ids[:1], "analogias": [],
    }
    return json.dumps({"nucleo": nucleo or ids[:1], "niveis": [nivel(n) for n in ("iniciante", "intermediario", "avancado")]})


def cliente_falso(respostas) -> ClienteLLM:
    """ClienteLLM do extractor, do adapter e do synthesizer com o ProviderFalso; preço de 1 US$ por milhão nos
    dois sentidos, cotação 5, raciocínio low nas correções."""
    return ClienteLLM(
        ProviderFalso(respostas),
        {"extractor": "modelo-falso", "adapter": "modelo-falso", "synthesizer": "modelo-falso"},
        {"modelo-falso": Preco(Decimal(1), Decimal(1))},
        Decimal(5),
        raciocinio={"extractor_correcao": "low", "adapter_correcao": "low", "synthesizer_correcao": "low"},
    )
