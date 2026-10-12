"""Quarentena do loop E2/E3 e a sua revisão humana (docs/fluxos/extracao.md, Quarentena).

- `separar`: depois da última rodada, o FactSheet assinado só com os claims válidos e os reprovados
  em quarentena. Determinístico.
- `preparar`: para quem revisa, a âncora do trecho (o do extractor ou o corrigido) e, para cada bruto, a
  entrada da tabela_numeros ou o número interpretado pelo numeros.py, a confirmar. Determinístico.
- `aceitar`: o claim com os números confirmados, aprovado pelas mesmas regras da E3 contra a
  tabela_numeros acrescida dos números novos. Determinístico.
- `preparar_revisao`, `aceitar_da_quarentena` e `descartar_da_quarentena`: leem o run do repositório,
  chamam as funções acima e gravam a decisão por persistencia.py. É o que o dashboard chama.

O humano nunca escreve posição: aponta o trecho e o bruto, e a âncora vem do código, como na E2.
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sunontent import numeros, persistencia
from sunontent.extracao import ancoragem, validador
from sunontent.extracao.ancoragem import NaoResolvido
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import (
    Ancora,
    Claim,
    ClaimProposto,
    ClaimQuarentena,
    DocumentoProcessado,
    FactSheet,
    Numero,
    Unidade,
)


def separar(
    factsheet: FactSheet, propostas: dict[str, ClaimProposto], correcoes: dict[str, str], rodada: int
) -> tuple[FactSheet, list[ClaimQuarentena]]:
    """FactSheet assinado com os claims fora de `correcoes` e a quarentena com os demais, em ordem de id."""
    validos = [c for c in factsheet.claims if c.id not in correcoes]
    quarentena = [
        ClaimQuarentena(proposto=propostas[claim_id], motivo=motivo, rodada=rodada)
        for claim_id, motivo in sorted(correcoes.items())
    ]
    return factsheet.model_copy(update={"claims": validos, "assinado": True}), quarentena


@dataclass(frozen=True)
class NumeroSugerido:
    """Um bruto do claim, como o código o resolveu, para quem revisa confirmar.

    `numero` é a entrada da tabela_numeros (`novo` False) ou o número interpretado na posição do bruto
    (`novo` True, gravado com origem humano ao aceitar). `numero` é None quando o numeros.py não
    interpreta o formato: quem revisa informa valor e unidade, e `ancora` traz a posição do bruto.
    """

    bruto: str
    ancora: Ancora
    numero: Numero | None
    novo: bool
    motivo: str | None = None


@dataclass(frozen=True)
class Preparo:
    ancora: Ancora
    literal_fonte: str
    numeros: list[NumeroSugerido]


def _dentro(numero_ancora: Ancora, ancora: Ancora) -> bool:
    return (
        numero_ancora.pagina == ancora.pagina
        and ancora.offset_inicio <= numero_ancora.offset_inicio
        and numero_ancora.offset_fim <= ancora.offset_fim
    )


def _sobrepoe(a: Ancora, b: Ancora) -> bool:
    return a.pagina == b.pagina and a.offset_inicio < b.offset_fim and b.offset_inicio < a.offset_fim


def _posicao_do_bruto(pagina: str, ancora: Ancora, bruto: str) -> Ancora:
    """Posição do bruto dentro do trecho (espaço flexível). Exige uma ocorrência só."""
    padrao = ancoragem.padrao_do_trecho(bruto)
    if padrao is None:
        raise NaoResolvido("bruto vazio")
    trecho = pagina[ancora.offset_inicio : ancora.offset_fim]
    achados = list(padrao.finditer(trecho))
    if not achados:
        raise NaoResolvido(f"o número {bruto!r} não está dentro do trecho citado")
    if len(achados) > 1:
        raise NaoResolvido(f"o número {bruto!r} aparece {len(achados)} vezes no trecho; aponte um bruto mais longo")
    inicio = ancora.offset_inicio + achados[0].start()
    return Ancora(pagina=ancora.pagina, offset_inicio=inicio, offset_fim=inicio + len(achados[0].group()))


def _trecho(documento: DocumentoProcessado, proposto: ClaimProposto, literal_fonte: str | None) -> tuple[Ancora, str]:
    ancora = ancoragem.localizar(documento, proposto.literal_fonte if literal_fonte is None else literal_fonte,
                                 proposto.chunk_id)
    return ancora, documento.paginas[ancora.pagina - 1].texto_limpo


def preparar(
    documento: DocumentoProcessado,
    proposto: ClaimProposto,
    literal_fonte: str | None = None,
    brutos: list[str] | None = None,
) -> Preparo:
    """Âncora do trecho e sugestão para cada bruto. `literal_fonte` corrige o trecho do extractor e
    `brutos` substitui a lista do extractor. NaoResolvido se o trecho ou um bruto não é encontrado."""
    ancora, pagina = _trecho(documento, proposto, literal_fonte)
    dentro = [n for n in documento.tabela_numeros if _dentro(n.ancora, ancora)]
    usados: list[Numero] = []
    sugeridos = []
    for bruto in proposto.numeros if brutos is None else brutos:
        alvo = "".join(bruto.split())
        existente = next((n for n in dentro if "".join(n.bruto.split()) == alvo and n not in usados), None)
        if existente is not None:
            usados.append(existente)
            sugeridos.append(NumeroSugerido(existente.bruto, existente.ancora, existente, novo=False))
            continue
        posicao = _posicao_do_bruto(pagina, ancora, bruto)
        texto = pagina[posicao.offset_inicio : posicao.offset_fim]
        try:
            lido = numeros.interpretar(texto)
        except numeros.NumeroNaoPrevisto as erro:
            sugeridos.append(NumeroSugerido(texto, posicao, None, novo=True, motivo=str(erro)))
            continue
        sugeridos.append(
            NumeroSugerido(texto, posicao, Numero(valor=lido.valor, unidade=lido.unidade, bruto=texto, ancora=posicao),
                           novo=True)
        )
    return Preparo(ancora, pagina[ancora.offset_inicio : ancora.offset_fim], sugeridos)


def numero_informado(sugerido: NumeroSugerido, valor: Decimal, unidade: Unidade) -> Numero:
    """Número com valor e unidade informados por quem revisa, na posição do bruto sugerido."""
    return Numero(valor=valor, unidade=unidade, bruto=sugerido.bruto, ancora=sugerido.ancora)


def aceitar(
    documento: DocumentoProcessado,
    proposto: ClaimProposto,
    confirmados: list[Numero],
    literal_fonte: str | None = None,
) -> tuple[Claim, list[Numero]]:
    """Claim com os números confirmados e a lista dos que não estão na tabela_numeros (novos).

    Cada número novo precisa estar dentro do trecho, com o bruto igual ao texto na posição, sem se
    sobrepor a um número que a E1 leu. O claim passa pelas regras da E3 contra a tabela acrescida dos novos.
    NaoResolvido com o motivo, se não passa.
    """
    ancora, pagina = _trecho(documento, proposto, literal_fonte)
    tabela = documento.tabela_numeros
    novos: list[Numero] = []
    for numero in confirmados:
        if numero in tabela:
            continue
        a = numero.ancora
        if not _dentro(a, ancora):
            raise NaoResolvido(f"o número {numero.bruto!r} está fora do trecho")
        if pagina[a.offset_inicio : a.offset_fim] != numero.bruto:
            raise NaoResolvido(f"o bruto {numero.bruto!r} não confere com o texto da página na posição informada")
        lido = next((n for n in tabela if _sobrepoe(n.ancora, a)), None)
        if lido is not None:
            raise NaoResolvido(
                f"a E1 já leu {lido.bruto!r} nessa posição como {lido.valor} {lido.unidade}; use esse número"
            )
        if any(_sobrepoe(n.ancora, a) for n in novos):
            raise NaoResolvido(f"o número {numero.bruto!r} se sobrepõe a outro número informado")
        novos.append(numero)
    claim = Claim(
        id=proposto.id,
        texto=proposto.texto.strip(),
        numeros=list(confirmados),
        tipo=proposto.tipo,
        tags=list(dict.fromkeys(proposto.tags)),
        ancora=ancora,
        literal_fonte=pagina[ancora.offset_inicio : ancora.offset_fim],
    )
    ampliado = documento.model_copy(update={"tabela_numeros": [*tabela, *novos]})
    motivo = validador.validar_claim(ampliado, claim)
    if motivo is not None:
        raise NaoResolvido(motivo)
    return claim, novos


def _pendente(repositorio, run_id: str, claim_id: str) -> ClaimQuarentena:
    item = next((i for i in persistencia.carregar_quarentena(repositorio, run_id) if i.claim.proposto.id == claim_id),
                None)
    if item is None:
        raise FalhaTerminal(f"o claim {claim_id} não está na quarentena do run {run_id}")
    if item.decisao is not None:
        raise FalhaTerminal(f"o claim {claim_id} do run {run_id} já foi revisado: {item.decisao}")
    return item.claim


def preparar_revisao(
    repositorio, run_id: str, claim_id: str, *, literal_fonte: str | None = None, brutos: list[str] | None = None
) -> Preparo:
    """`preparar` sobre o documento e o claim gravados no run."""
    proposto = _pendente(repositorio, run_id, claim_id).proposto
    documento = persistencia.carregar_documento_processado(repositorio, run_id)
    return preparar(documento, proposto, literal_fonte, brutos)


def aceitar_da_quarentena(
    repositorio,
    raiz_dados: Path,
    run_id: str,
    claim_id: str,
    confirmados: list[Numero],
    *,
    autor: str,
    literal_fonte: str | None = None,
) -> Claim:
    """Aceita o claim e o grava no FactSheet do run, com origem humano. NaoResolvido se não passa na E3."""
    proposto = _pendente(repositorio, run_id, claim_id).proposto
    documento = persistencia.carregar_documento_processado(repositorio, run_id)
    claim, novos = aceitar(documento, proposto, confirmados, literal_fonte)
    persistencia.gravar_decisao_quarentena(
        repositorio, raiz_dados, run_id, claim_id, "aceitar", autor, claim=claim, numeros_novos=novos
    )
    return claim


def descartar_da_quarentena(repositorio, raiz_dados: Path, run_id: str, claim_id: str, *, autor: str) -> None:
    _pendente(repositorio, run_id, claim_id)
    persistencia.gravar_decisao_quarentena(repositorio, raiz_dados, run_id, claim_id, "descartar", autor)
