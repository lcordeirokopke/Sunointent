"""Cliente LLM único (docs/arquiteturas.md, 1. Model client / router).

`ClienteLLM.chamar(no, prompt, schema)` escolhe o snapshot do nó em config/models.yaml, pede saída
JSON no schema Pydantic, valida e devolve o objeto, nunca texto. Cada tentativa vira um Span,
entregue a `registrar_span` (gravado por persistencia.py), com tokens e custo estimado em BRL.

Duas contagens separadas, nenhuma ligada ao K de qualidade:
- falha de infraestrutura (429, 5xx, timeout, conexão): repete com backoff até retentativa.TENTATIVAS;
- resposta fora do schema: repete até TENTATIVAS_PARSE, reinjetando o erro de validação no prompt.
Bloqueio de conteúdo e erro 4xx são terminais.

Só este módulo importa o SDK do provider (google-genai).
"""

import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from sunontent import retentativa
from sunontent.retentativa import FalhaRetriavel, FalhaTerminal
from sunontent.schemas import Span

T = TypeVar("T", bound=BaseModel)

TENTATIVAS_PARSE = 3
TIMEOUT_S = 300
MAX_TOKENS_SAIDA = 65536
VARIAVEL_CHAVE = "GOOGLE_API_KEY"
NIVEIS_RACIOCINIO = ("minimal", "low", "medium", "high")


@dataclass(frozen=True)
class Preco:
    """US$ por 1 milhão de tokens."""

    entrada: Decimal
    saida: Decimal


@dataclass(frozen=True)
class RespostaBruta:
    texto: str
    tokens_entrada: int
    tokens_saida: int
    bloqueio: str | None = None  # motivo, quando o provider recusou o conteúdo


class Provider(Protocol):
    def gerar(self, modelo: str, prompt: str, json_schema: dict, raciocinio: str | None = None) -> RespostaBruta:
        """Uma chamada. `raciocinio` é um de NIVEIS_RACIOCINIO, ou None para o padrão do modelo.

        Levanta FalhaRetriavel (429, 5xx, timeout, conexão) ou FalhaTerminal (demais erros)."""
        ...


class ClienteLLM:
    def __init__(
        self,
        provider: Provider,
        modelos: Mapping[str, str],
        precos: Mapping[str, Preco],
        cotacao_usd_brl: Decimal,
        relogio: Callable[[], float] = time.monotonic,
        raciocinio: Mapping[str, str] | None = None,
    ):
        sem_preco = sorted({m for m in modelos.values() if m not in precos})
        if sem_preco:
            raise FalhaTerminal(f"config/models.yaml: sem preço para {', '.join(sem_preco)}")
        invalidos = sorted(f"{chave}: {nivel}" for chave, nivel in (raciocinio or {}).items()
                           if nivel not in NIVEIS_RACIOCINIO)
        if invalidos:
            raise FalhaTerminal(
                f"config/models.yaml: nível de raciocínio inválido ({', '.join(invalidos)}); "
                f"use {', '.join(NIVEIS_RACIOCINIO)}"
            )
        self._raciocinio = dict(raciocinio or {})
        self._provider = provider
        self._modelos = dict(modelos)
        self._precos = dict(precos)
        self._cotacao = cotacao_usd_brl
        self._relogio = relogio
        self.custo_total_tokens = 0
        self.custo_total_brl = Decimal(0)

    @classmethod
    def de_config(cls, raiz: Path, provider: Provider) -> "ClienteLLM":
        """Lê modelos, preços, cotação e níveis de raciocínio de config/models.yaml."""
        relativo = "config/models.yaml"
        try:
            dados = yaml.safe_load((Path(raiz) / relativo).read_text(encoding="utf-8")) or {}
            precos = {
                str(modelo): Preco(Decimal(str(p["entrada"])), Decimal(str(p["saida"])))
                for modelo, p in (dados.get("precos_usd_por_milhao") or {}).items()
            }
            cotacao = Decimal(str(dados["cotacao_usd_brl"]))
        except (OSError, yaml.YAMLError, KeyError, TypeError, ArithmeticError) as erro:
            raise FalhaTerminal(f"{relativo}: preços ou cotacao_usd_brl inválidos ({erro!r})") from erro
        return cls(provider, dados.get("modelos") or {}, precos, cotacao, raciocinio=dados.get("raciocinio") or {})

    def custo_brl(self, modelo: str, tokens_entrada: int, tokens_saida: int) -> Decimal:
        preco = self._precos[modelo]
        usd = (tokens_entrada * preco.entrada + tokens_saida * preco.saida) / Decimal(1_000_000)
        return (usd * self._cotacao).quantize(Decimal("0.000001"))

    def chamar(
        self,
        no: str,
        prompt: str,
        schema: type[T],
        *,
        run_id: str,
        registrar_span: Callable[[Span], None],
        nivel: str | None = None,
        celula_id: str | None = None,
        tentativa: int | None = None,
        raciocinio: str | None = None,
    ) -> T:
        """`raciocinio` é uma chave de `raciocinio` em config/models.yaml (ex: extractor_correcao); chave
        ausente no YAML ou None usa o padrão do modelo."""
        modelo = self._modelos.get(no)
        nivel_raciocinio = self._raciocinio.get(raciocinio) if raciocinio else None
        if modelo is None:
            raise FalhaTerminal(f"config/models.yaml: nenhum modelo para o nó {no!r}")
        json_schema = schema.model_json_schema()
        falhas_infra = 0
        erros_parse = 0
        prompt_atual = prompt

        def span(inicio: float, tokens_entrada=0, tokens_saida=0, classe=None, mensagem=None) -> None:
            custo = self.custo_brl(modelo, tokens_entrada, tokens_saida)
            self.custo_total_tokens += tokens_entrada + tokens_saida
            self.custo_total_brl += custo
            registrar_span(
                Span(
                    span_id=str(uuid.uuid4()),
                    run_id=run_id,
                    no=no,
                    nivel=nivel,
                    celula_id=celula_id,
                    tentativa=tentativa,
                    modelo=modelo,
                    tokens_entrada=tokens_entrada,
                    tokens_saida=tokens_saida,
                    custo_brl=custo,
                    duracao_ms=max(0, int((self._relogio() - inicio) * 1000)),
                    falha_classe=classe,
                    falha_mensagem=mensagem,
                )
            )

        while True:
            inicio = self._relogio()
            try:
                bruta = self._provider.gerar(modelo, prompt_atual, json_schema, nivel_raciocinio)
            except FalhaRetriavel as falha:
                span(inicio, classe="retriavel", mensagem=falha.mensagem)
                falhas_infra += 1
                if falhas_infra >= retentativa.TENTATIVAS:
                    raise FalhaTerminal(
                        f"{no} ({modelo}): {falhas_infra} tentativas esgotadas. Última falha: {falha.mensagem}"
                    ) from falha
                atraso = retentativa.espera(falhas_infra)
                print(
                    f"{no} ({modelo}): tentativa {falhas_infra} falhou ({falha.mensagem}); nova tentativa em {atraso:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
                retentativa._dormir(atraso)
                continue
            except FalhaTerminal as falha:
                span(inicio, classe="terminal", mensagem=falha.mensagem)
                raise

            if bruta.bloqueio is not None:
                mensagem = f"{no} ({modelo}): conteúdo recusado pelo provider ({bruta.bloqueio})"
                span(inicio, bruta.tokens_entrada, bruta.tokens_saida, "terminal", mensagem)
                raise FalhaTerminal(mensagem)

            try:
                objeto = schema.model_validate_json(bruta.texto)
            except ValidationError as erro:
                erros_parse += 1
                resumo = _resumo_validacao(erro)
                span(inicio, bruta.tokens_entrada, bruta.tokens_saida, "retriavel", f"resposta fora do schema: {resumo}")
                if erros_parse >= TENTATIVAS_PARSE:
                    raise FalhaTerminal(
                        f"{no} ({modelo}): {erros_parse} respostas fora do schema. Último erro: {resumo}"
                    ) from erro
                prompt_atual = (
                    f"{prompt}\n\nA resposta anterior não seguiu o schema JSON pedido. Erro de validação:\n"
                    f"{resumo}\nDevolva a resposta completa de novo, só o JSON, seguindo o schema."
                )
                continue

            span(inicio, bruta.tokens_entrada, bruta.tokens_saida)
            return objeto


def _resumo_validacao(erro: ValidationError, limite: int = 10) -> str:
    linhas = [
        f"{'.'.join(str(p) for p in e['loc']) or '(raiz)'}: {e['msg']}" for e in erro.errors()[:limite]
    ]
    if erro.error_count() > limite:
        linhas.append(f"... mais {erro.error_count() - limite} erros")
    return "; ".join(linhas)


class ProviderGemini:
    """Gemini API pelo SDK google-genai, com saída JSON no schema pedido."""

    _BLOQUEIOS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE"}

    def __init__(self, api_key: str, timeout_s: int = TIMEOUT_S):
        from google import genai
        from google.genai import types

        # Sem retentativa no SDK: a retentativa é a do ClienteLLM, que registra cada tentativa.
        self._types = types
        self._cliente = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=timeout_s * 1000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )

    def gerar(self, modelo: str, prompt: str, json_schema: dict, raciocinio: str | None = None) -> RespostaBruta:
        import httpx
        from google.genai import errors

        config = self._types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=json_schema,
            max_output_tokens=MAX_TOKENS_SAIDA,
            thinking_config=(
                self._types.ThinkingConfig(thinking_level=raciocinio.upper()) if raciocinio else None
            ),
        )
        try:
            resposta = self._cliente.models.generate_content(model=modelo, contents=prompt, config=config)
        except errors.APIError as erro:
            codigo = erro.code or 0
            if codigo in (408, 429) or codigo >= 500:
                raise FalhaRetriavel(f"HTTP {codigo}: {erro.message}") from erro
            raise FalhaTerminal(f"HTTP {codigo}: {erro.message}") from erro
        except (httpx.TimeoutException, httpx.TransportError) as erro:
            raise FalhaRetriavel(f"{type(erro).__name__}: {erro}") from erro

        uso = resposta.usage_metadata
        tokens_entrada = (uso.prompt_token_count or 0) if uso else 0
        tokens_saida = ((uso.candidates_token_count or 0) + (uso.thoughts_token_count or 0)) if uso else 0
        bloqueio = None
        feedback = resposta.prompt_feedback
        if feedback is not None and feedback.block_reason:
            bloqueio = f"prompt bloqueado: {feedback.block_reason}"
        elif not resposta.candidates:
            bloqueio = "nenhuma resposta candidata"
        else:
            motivo = resposta.candidates[0].finish_reason
            nome = getattr(motivo, "name", str(motivo))
            if nome in self._BLOQUEIOS:
                bloqueio = f"finish_reason {nome}"
        return RespostaBruta(resposta.text or "", tokens_entrada, tokens_saida, bloqueio)


def conferir_credenciais(ambiente: Mapping[str, str]) -> None:
    if not ambiente.get(VARIAVEL_CHAVE):
        raise FalhaTerminal(f"credencial do Gemini ausente: {VARIAVEL_CHAVE}. Preencha o .env a partir do .env.example.")


def criar_cliente(raiz: Path, ambiente: Mapping[str, str]) -> ClienteLLM:
    """Confere a chave e monta o cliente com o provider Gemini e a config de config/models.yaml."""
    conferir_credenciais(ambiente)
    return ClienteLLM.de_config(raiz, ProviderGemini(ambiente[VARIAVEL_CHAVE]))
