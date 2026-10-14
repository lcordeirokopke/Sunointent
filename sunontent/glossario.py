"""Leitura e validação de config/glossario.yaml, casamento de termos e analogia do glossário.

Fonte única dos termos que pedem analogia: a E4, a M4 e a M5 leem o mesmo arquivo por este módulo e
casam os termos pela mesma regra (docs/glossario.md, Casamento no texto):
- cada forma (o `termo` e cada variante) casa como está escrita, sem lematizar, ignorando maiúsculas e
  acentos, com fronteira de palavra nos dois lados; espaço e quebra de linha entre as palavras da forma
  se equivalem;
- sigla (forma só com maiúsculas, dígitos e sinais, como CDI, IPCA-15, NTN-B, EMBI+) casa só com a caixa exata;
- termo `ambiguo` casa só pelas formas de mais de uma palavra;
- quando duas ocorrências se sobrepõem, vale a mais longa;
- os marcadores de número da peça ({{<claim_id>.<ordem>}}) ficam fora.
Glossário inválido é FalhaTerminal: o run aborta no início da E4.
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import NIVEIS, Analogia, Nivel

RELATIVO = "config/glossario.yaml"
Categoria = Literal[
    "juros", "inflacao", "atividade", "cambio", "fiscal", "mercado", "contabil", "societario", "regulatorio"
]
_MARCADOR = re.compile(r"\{\{[^{}]*\}\}")
_DIGITO = re.compile(r"\d")


class AnalogiaGlossario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    texto: StrictStr = Field(min_length=1)
    limite: StrictStr = Field(min_length=1)
    status: Literal["rascunho", "aprovada"]


class FonteGlossario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nome: StrictStr = Field(min_length=1)
    url: StrictStr = Field(min_length=1)


class Termo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: StrictStr = Field(pattern=r"^[a-z0-9_]+$")
    termo: StrictStr = Field(min_length=1)
    variantes: list[StrictStr]
    categoria: Categoria
    nivel_minimo: Nivel
    definicao_simples: StrictStr = Field(min_length=1)
    definicao_tecnica: StrictStr = Field(min_length=1)
    analogias: list[AnalogiaGlossario]
    ambiguo: StrictBool
    fonte: FonteGlossario

    @property
    def formas(self) -> list[str]:
        return [self.termo, *self.variantes]


class _Arquivo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versao: StrictInt = Field(ge=1)
    termos: list[Termo]


@dataclass(frozen=True)
class Ocorrencia:
    """Um termo encontrado num texto: `forma` é a forma do glossário que casou; `inicio` e `fim` são
    posições no texto recebido (fim exclusivo)."""

    id: str
    forma: str
    inicio: int
    fim: int


def dobrar(texto: str) -> str:
    """Sem maiúsculas e sem acentos."""
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _dobrar_com_posicoes(texto: str) -> tuple[str, list[int]]:
    """Texto dobrado e, para cada caractere dele, a posição do caractere de origem."""
    partes, origem = [], []
    for posicao, caractere in enumerate(texto):
        dobrado = dobrar(caractere)
        partes.append(dobrado)
        origem.extend([posicao] * len(dobrado))
    return "".join(partes), origem


def e_sigla(forma: str) -> bool:
    return any(c.isalpha() for c in forma) and not any(c.islower() for c in forma)


def _padrao(forma: str) -> re.Pattern:
    palavras = forma.split()
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(p) for p in palavras) + r"(?!\w)")


class Glossario:
    def __init__(self, versao: int, termos: list[Termo]):
        self.versao = versao
        self.termos = {t.id: t for t in termos}

    @cached_property
    def _padroes(self) -> list[tuple[str, str, bool, re.Pattern]]:
        """(id, forma, sigla, padrão) de cada forma que casa no texto."""
        padroes = []
        for termo in self.termos.values():
            for forma in termo.formas:
                if termo.ambiguo and len(forma.split()) < 2:
                    continue
                sigla = e_sigla(forma)
                padroes.append((termo.id, forma, sigla, _padrao(forma if sigla else dobrar(forma))))
        return padroes

    def casar(self, texto: str) -> list[Ocorrencia]:
        """Ocorrências dos termos no texto, em ordem de posição, sem sobreposição."""
        limpo = _MARCADOR.sub(lambda m: " " * len(m.group()), texto)
        dobrado, origem = _dobrar_com_posicoes(limpo)
        candidatas = []
        for termo_id, forma, sigla, padrao in self._padroes:
            if sigla:
                candidatas += [(m.start(), m.end(), termo_id, forma) for m in padrao.finditer(limpo)]
            else:
                candidatas += [
                    (origem[m.start()], origem[m.end() - 1] + 1, termo_id, forma) for m in padrao.finditer(dobrado)
                ]
        escolhidas: list[Ocorrencia] = []
        for inicio, fim, termo_id, forma in sorted(candidatas, key=lambda c: (c[0] - c[1], c[0])):
            if all(fim <= o.inicio or inicio >= o.fim for o in escolhidas):
                escolhidas.append(Ocorrencia(termo_id, forma, inicio, fim))
        return sorted(escolhidas, key=lambda o: o.inicio)

    def termos_em(self, texto: str) -> list[str]:
        """Ids dos termos encontrados no texto, na ordem da primeira ocorrência, sem repetir."""
        return list(dict.fromkeys(o.id for o in self.casar(texto)))

    def acima_de(self, ids, nivel: str) -> list[str]:
        """Só os termos com `nivel_minimo` acima do nível, na ordem recebida."""
        limite = NIVEIS.index(nivel)
        return [i for i in ids if NIVEIS.index(self.termos[i].nivel_minimo) > limite]

    def termo_da_forma(self, forma: str) -> str | None:
        """Id do termo que tem a forma (o termo ou uma variante), comparada sem maiúsculas e sem acentos."""
        return self._por_forma.get(dobrar(forma.strip()))

    @cached_property
    def _por_forma(self) -> dict[str, str]:
        return {dobrar(f): t.id for t in self.termos.values() for f in t.formas}

    def analogia(self, termo_id: str) -> Analogia | None:
        """A primeira analogia aprovada do termo ou, sem nenhuma aprovada, a primeira em rascunho, com a
        referência <id>#<n>. None quando o termo não tem analogia."""
        termo = self.termos[termo_id]
        if not termo.analogias:
            return None
        posicao = next(
            (n for n, a in enumerate(termo.analogias, start=1) if a.status == "aprovada"), 1
        )
        escolhida = termo.analogias[posicao - 1]
        return Analogia(
            termo=termo.termo,
            explicacao=escolhida.texto,
            origem="glossario",
            glossario_ref=f"{termo.id}#{posicao}",
            revisada=escolhida.status == "aprovada",
        )


def validar(dados) -> Glossario:
    """Valida o conteúdo do arquivo. Levanta FalhaTerminal com todos os problemas encontrados."""
    if not isinstance(dados, dict):
        raise FalhaTerminal(f"{RELATIVO}: arquivo vazio ou fora do formato")
    try:
        arquivo = _Arquivo.model_validate(dados)
    except ValidationError as erro:
        detalhes = []
        for e in erro.errors()[:20]:
            loc = list(e["loc"])
            if len(loc) >= 2 and loc[0] == "termos" and isinstance(loc[1], int):
                termos = dados.get("termos") or []
                termo = termos[loc[1]] if loc[1] < len(termos) and isinstance(termos[loc[1]], dict) else {}
                loc[1] = termo.get("id", loc[1])
            detalhes.append(f"{'.'.join(str(p) for p in loc)}: {e['msg']}")
        raise FalhaTerminal(f"{RELATIVO}: glossário inválido: {'; '.join(detalhes)}") from erro

    problemas = []
    ids: set[str] = set()
    donos: dict[str, str] = {}
    for termo in arquivo.termos:
        if termo.id in ids:
            problemas.append(f"id repetido: {termo.id}")
        ids.add(termo.id)
        for forma in termo.formas:
            chave = dobrar(forma.strip())
            if chave in donos and donos[chave] != termo.id:
                problemas.append(f"forma {forma!r} em {donos[chave]} e em {termo.id}")
            elif chave in donos:
                problemas.append(f"forma {forma!r} repetida em {termo.id}")
            donos.setdefault(chave, termo.id)
        textos = {"definicao_simples": termo.definicao_simples, "definicao_tecnica": termo.definicao_tecnica}
        for n, analogia in enumerate(termo.analogias, start=1):
            textos[f"analogias#{n}.texto"] = analogia.texto
            textos[f"analogias#{n}.limite"] = analogia.limite
        for campo, texto in textos.items():
            if _DIGITO.search(texto):
                problemas.append(f"{termo.id}.{campo} tem dígito")
    if problemas:
        raise FalhaTerminal(f"{RELATIVO}: glossário inválido: {'; '.join(problemas)}")
    return Glossario(arquivo.versao, arquivo.termos)


def carregar(raiz: Path) -> Glossario:
    try:
        dados = yaml.safe_load((Path(raiz) / RELATIVO).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as erro:
        raise FalhaTerminal(f"{RELATIVO}: não foi possível ler ({erro})") from erro
    return validar(dados)
