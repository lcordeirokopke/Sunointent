"""Cliente LLM único (docs/arquiteturas.md, 1. Model client / router).

`ClienteLLM.chamar(no, prompt, schema)` escolhe o snapshot do nó em config/models.yaml, pede saída
JSON no schema Pydantic, valida e devolve o objeto, nunca texto. Cada tentativa vira um Span,
entregue a `registrar_span` (gravado por persistencia.py), com tokens e custo estimado em BRL.

Provider por nó: o juiz (judge) chama o OpenRouter com a API_KEY_ROUTER, sem chave de reserva; os outros nós
chamam a Gemini API. Chaves da Gemini API em ordem de chamada: GOOGLE_API_KEY_PRINC e, como reserva,
GOOGLE_API_KEY_SEC. Cada tentativa começa pela PRINC e só passa para a SEC quando a PRINC tem falha de
infraestrutura ou de chave (FalhaChave: 401, 403 ou chave inválida). Quando todas as chaves falham, a tentativa
conta como uma falha de infraestrutura; se todas foram recusadas por chave, a falha é terminal.

Duas contagens separadas, nenhuma ligada ao K de qualidade:
- falha de infraestrutura (429, 5xx, timeout, conexão) em todas as chaves: repete com backoff até
  retentativa.TENTATIVAS;
- resposta fora do schema: repete até TENTATIVAS_PARSE, reinjetando o erro de validação no prompt.
Bloqueio de conteúdo e os demais erros 4xx são terminais, sem passar para a chave seguinte.

Só este módulo importa o SDK do provider (google-genai) e chama o OpenRouter (por httpx, sem SDK).
"""

import json
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
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
CHAVE_PRINCIPAL = "GOOGLE_API_KEY_PRINC"
CHAVE_RESERVA = "GOOGLE_API_KEY_SEC"
CHAVE_ROUTER = "API_KEY_ROUTER"
URL_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
# Saída máxima do endpoint DeepInfra do juiz; o raciocínio conta dentro dela.
MAX_TOKENS_SAIDA_ROUTER = 16384
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


class FalhaChave(FalhaTerminal):
    """Chave recusada pelo provider (401, 403 ou chave inválida): a tentativa passa para a chave seguinte."""


class Provider(Protocol):
    def gerar(self, modelo: str, prompt: str, json_schema: dict, raciocinio: str | None = None) -> RespostaBruta:
        """Uma chamada. `raciocinio` é um de NIVEIS_RACIOCINIO, ou None para o padrão do modelo.

        Levanta FalhaRetriavel (429, 5xx, timeout, conexão), FalhaChave (chave recusada) ou
        FalhaTerminal (demais erros)."""
        ...


Cadeia = Provider | Sequence[tuple[str, Provider]]


def _cadeia(provider: Cadeia) -> list[tuple[str, Provider]]:
    return list(provider) if isinstance(provider, Sequence) else [("", provider)]


class ClienteLLM:
    """`provider` é um Provider só ou a lista de pares (nome da chave, Provider) na ordem de chamada, usada pelos
    nós sem entrada em `por_no`; `por_no` dá a cadeia de um nó (o juiz no OpenRouter)."""

    def __init__(
        self,
        provider: Cadeia,
        modelos: Mapping[str, str],
        precos: Mapping[str, Preco],
        cotacao_usd_brl: Decimal,
        relogio: Callable[[], float] = time.monotonic,
        raciocinio: Mapping[str, str] | None = None,
        por_no: Mapping[str, Cadeia] | None = None,
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
        self._providers = _cadeia(provider)
        self._provider = self._providers[0][1]  # o da chave principal
        self._por_no = {no: _cadeia(cadeia) for no, cadeia in (por_no or {}).items()}
        self._modelos = dict(modelos)
        self._precos = dict(precos)
        self._cotacao = cotacao_usd_brl
        self._relogio = relogio
        # As células rodam em threads (graph.MAX_CONCORRENCIA): as somas do run passam por esta trava.
        self._trava_custo = threading.Lock()
        self.custo_total_tokens = 0
        self.custo_total_brl = Decimal(0)

    @classmethod
    def de_config(cls, raiz: Path, provider: Cadeia, por_no: Mapping[str, Cadeia] | None = None) -> "ClienteLLM":
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
        return cls(
            provider, dados.get("modelos") or {}, precos, cotacao, raciocinio=dados.get("raciocinio") or {}, por_no=por_no
        )

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
        rodada: int | None = None,
        raciocinio: str | None = None,
    ) -> T:
        """`raciocinio` é uma chave de `raciocinio` em config/models.yaml (ex: extractor_correcao); chave
        ausente no YAML ou None usa o padrão do modelo."""
        modelo = self._modelos.get(no)
        nivel_raciocinio = self._raciocinio.get(raciocinio) if raciocinio else None
        if modelo is None:
            raise FalhaTerminal(f"config/models.yaml: nenhum modelo para o nó {no!r}")
        json_schema = schema.model_json_schema()
        providers = self._por_no.get(no, self._providers)
        falhas_infra = 0
        erros_parse = 0
        prompt_atual = prompt

        def span(inicio: float, tokens_entrada=0, tokens_saida=0, classe=None, mensagem=None) -> None:
            custo = self.custo_brl(modelo, tokens_entrada, tokens_saida)
            with self._trava_custo:
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
                    rodada=rodada,
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
            bruta = None
            falhas: list[tuple[FalhaRetriavel | FalhaChave, str]] = []  # (falha, mensagem com a chave)
            for posicao, (chave, provider) in enumerate(providers):
                inicio = self._relogio()
                try:
                    bruta = provider.gerar(modelo, prompt_atual, json_schema, nivel_raciocinio)
                    break
                except (FalhaRetriavel, FalhaChave) as falha:
                    mensagem = _com_chave(chave, falha.mensagem)
                    span(inicio, classe=falha.classe, mensagem=mensagem)
                    falhas.append((falha, mensagem))
                    if posicao + 1 < len(providers):
                        print(
                            f"{no} ({modelo}): {mensagem}; chamando {providers[posicao + 1][0]}",
                            file=sys.stderr,
                            flush=True,
                        )
                except FalhaTerminal as falha:
                    span(inicio, classe="terminal", mensagem=_com_chave(chave, falha.mensagem))
                    raise

            if bruta is None:
                ultima, mensagens = falhas[-1][0], "; ".join(m for _, m in falhas)
                if all(isinstance(f, FalhaChave) for f, _ in falhas):
                    raise FalhaTerminal(f"{no} ({modelo}): chave recusada. {mensagens}") from ultima
                falhas_infra += 1
                if falhas_infra >= retentativa.TENTATIVAS:
                    raise FalhaTerminal(
                        f"{no} ({modelo}): {falhas_infra} tentativas esgotadas. Última falha: {mensagens}"
                    ) from ultima
                atraso = retentativa.espera(falhas_infra)
                print(
                    f"{no} ({modelo}): tentativa {falhas_infra} falhou ({mensagens}); nova tentativa em {atraso:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )
                retentativa._dormir(atraso)
                continue

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


def _com_chave(chave: str, mensagem: str) -> str:
    return f"{chave}: {mensagem}" if chave else mensagem


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
            # A Gemini API responde 400 com "API key not valid" para chave inválida.
            if codigo in (401, 403) or (codigo == 400 and "api key" in (erro.message or "").lower()):
                raise FalhaChave(f"HTTP {codigo}: {erro.message}") from erro
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


class ProviderOpenRouter:
    """OpenRouter por HTTP (httpx), com saída JSON no schema pedido (`strict`) e só para endpoints que aceitam
    todos os parâmetros da chamada (`require_parameters`).

    Falhas: 408, 429 e 5xx retriáveis; 401 chave recusada; 402 (sem crédito), 403 sem motivo de moderação (como o
    teto de gasto da chave) e os demais 4xx terminais; 403 com motivos de moderação e `finish_reason`
    `content_filter` são bloqueio de conteúdo. Resposta vazia com `finish_reason`
    `length` (o raciocínio esgotou a saída) volta como texto vazio, que o ClienteLLM trata como fora do schema.
    Os tokens de raciocínio já vêm dentro de `completion_tokens` e contam como saída.
    """

    def __init__(self, api_key: str, timeout_s: int = TIMEOUT_S, cliente_http=None):
        import httpx

        self._api_key = api_key
        self._http = cliente_http or httpx.Client(timeout=timeout_s)

    def gerar(self, modelo: str, prompt: str, json_schema: dict, raciocinio: str | None = None) -> RespostaBruta:
        import httpx

        corpo = {
            "model": modelo,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "resposta", "strict": True, "schema": schema_estrito(json_schema)},
            },
            "provider": {"require_parameters": True},
            "max_tokens": MAX_TOKENS_SAIDA_ROUTER,
        }
        if raciocinio:
            corpo["reasoning"] = {"effort": raciocinio, "exclude": True}
        try:
            resposta = self._http.post(URL_OPENROUTER, json=corpo, headers={"Authorization": f"Bearer {self._api_key}"})
        except (httpx.TimeoutException, httpx.TransportError) as erro:
            raise FalhaRetriavel(f"{type(erro).__name__}: {erro}") from erro
        try:
            dados = resposta.json()
        except ValueError:
            dados = {}
        if not isinstance(dados, dict):
            dados = {}
        if resposta.status_code != 200:
            return _erro_openrouter(resposta.status_code, dados.get("error") or {}, resposta.text)
        # Erro durante a geração vem com HTTP 200: no corpo, sem `choices`, ou dentro de `choices[0]`.
        if dados.get("error") and not dados.get("choices"):
            erro = dados["error"]
            return _erro_openrouter(int(erro.get("code") or 502), erro, "")

        uso = dados.get("usage") or {}
        tokens_entrada = int(uso.get("prompt_tokens") or 0)
        tokens_saida = int(uso.get("completion_tokens") or 0)
        escolhas = dados.get("choices") or []
        if not escolhas:
            return RespostaBruta("", tokens_entrada, tokens_saida, "nenhuma resposta candidata")
        escolha = escolhas[0]
        motivo = escolha.get("finish_reason")
        if motivo == "error":
            erro = escolha.get("error") or {}
            raise FalhaRetriavel(f"erro durante a geração: {erro.get('message') or 'sem mensagem'}")
        bloqueio = "finish_reason content_filter" if motivo == "content_filter" else None
        texto = (escolha.get("message") or {}).get("content") or ""
        return RespostaBruta(texto, tokens_entrada, tokens_saida, bloqueio)


def _erro_openrouter(codigo: int, erro: dict, texto: str) -> RespostaBruta:
    """Classifica um erro do OpenRouter; o 403 de moderação (com `metadata.reasons`) volta como bloqueio de
    conteúdo."""
    mensagem = f"HTTP {codigo}: {erro.get('message') or texto[:300] or 'sem mensagem'}"
    if codigo in (408, 429) or codigo >= 500:
        raise FalhaRetriavel(mensagem)
    if codigo == 401:
        raise FalhaChave(mensagem)
    motivos = (erro.get("metadata") or {}).get("reasons") if codigo == 403 else None
    if motivos:
        return RespostaBruta("", 0, 0, f"{mensagem} ({', '.join(map(str, motivos))})")
    raise FalhaTerminal(mensagem)


def schema_estrito(schema: dict) -> dict:
    """O JSON Schema do Pydantic no formato do modo `strict`: todo objeto com todas as propriedades em `required`
    e `additionalProperties` falso. Campo com valor padrão continua aceitando o valor do seu tipo (ex.: null)."""

    def ajustar(no):
        if isinstance(no, dict):
            novo = {chave: ajustar(valor) for chave, valor in no.items() if chave != "default"}
            if novo.get("type") == "object" and isinstance(novo.get("properties"), dict):
                novo["required"] = list(novo["properties"])
                novo["additionalProperties"] = False
            return novo
        if isinstance(no, list):
            return [ajustar(item) for item in no]
        return no

    return ajustar(schema)


def conferir_credenciais(ambiente: Mapping[str, str]) -> None:
    """A chave principal do Gemini e a do OpenRouter (juiz) são obrigatórias; a reserva do Gemini é opcional."""
    if not ambiente.get(CHAVE_PRINCIPAL):
        raise FalhaTerminal(f"credencial do Gemini ausente: {CHAVE_PRINCIPAL}. Preencha o .env a partir do .env.example.")
    if not ambiente.get(CHAVE_ROUTER):
        raise FalhaTerminal(f"credencial do OpenRouter ausente: {CHAVE_ROUTER}. Preencha o .env a partir do .env.example.")


def criar_cliente(raiz: Path, ambiente: Mapping[str, str]) -> ClienteLLM:
    """Confere as chaves e monta o cliente com um provider Gemini por chave, na ordem PRINC e SEC, o juiz no
    OpenRouter e a config de config/models.yaml."""
    conferir_credenciais(ambiente)
    chaves = [c for c in (CHAVE_PRINCIPAL, CHAVE_RESERVA) if ambiente.get(c)]
    return ClienteLLM.de_config(
        raiz,
        [(c, ProviderGemini(ambiente[c])) for c in chaves],
        por_no={"judge": [(CHAVE_ROUTER, ProviderOpenRouter(ambiente[CHAVE_ROUTER]))]},
    )
