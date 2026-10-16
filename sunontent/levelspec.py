"""Leitura e validação de config/levels/ (docs/fluxos/plano-editorial.md, LevelSpec da E4).

O LevelSpec existe só em config/levels/: o prompt e o avaliador leem o mesmo arquivo. O arquivo tem os campos
da E4, o contrato de formato (`peso_papel` e `formatos`), lido pela E4 (o `max_claims` do plano é o do artigo),
pela E5.1 e pela M2, no Iniciante, o bloco `analogia` com os marcadores que a M5 procura, e, em qualquer nível, o bloco
opcional `legibilidade` (piso do IFL por formato, lido pela M3, e meta de frase do redator). As frases do
prompt do redator e o limite da M2 saem da mesma função, `instrucoes_redator`. Arquivo inválido é
FalhaTerminal: o run aborta no início da E4.
"""

import warnings
from pathlib import Path
from typing import get_args

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import Formato, Nivel, Tag

# Única tag que corresponde a um motivo de descarte (docs/arquiteturas.md, E4.1).
TAGS_DESCARTAVEIS_PERMITIDAS = ("metodologico",)


class _Fechado(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Faixa(_Fechado):
    min: StrictInt = Field(ge=1)
    max: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def _ordem(self) -> "Faixa":
        if self.min > self.max:
            raise ValueError(f"min ({self.min}) maior que max ({self.max})")
        return self


class PesoPapel(_Fechado):
    """Peso de cada papel na divisão das palavras do artigo e das cenas de claim do roteiro."""

    principal: StrictInt = Field(ge=1)
    apoio: StrictInt = Field(ge=1)
    contexto: StrictInt = Field(ge=1)


class Artigo(_Fechado):
    max_claims: StrictInt = Field(ge=3)  # o núcleo tem até 3 claims e entra nos 3 níveis e nos 3 formatos
    palavras: Faixa


class CaracteresSlide(_Fechado):
    gancho: StrictInt = Field(ge=1)
    corpo: StrictInt = Field(ge=1)
    conclusao: StrictInt = Field(ge=1)


class Carrossel(_Fechado):
    max_claims: StrictInt = Field(ge=3)
    slides: Faixa  # contando o gancho e a conclusão
    caracteres: CaracteresSlide  # máximo por slide, por papel

    @field_validator("slides")
    @classmethod
    def _gancho_corpo_conclusao(cls, slides: Faixa) -> Faixa:
        if slides.min < 3:
            raise ValueError("o carrossel tem pelo menos 3 slides: gancho, corpo e conclusão")
        return slides


class Roteiro(_Fechado):
    """Cenas: uma de abertura (gancho), uma por claim e uma de fechamento."""

    max_claims: StrictInt = Field(ge=3)
    duracao_s: StrictInt = Field(ge=1)
    palavras_por_segundo: StrictFloat = Field(gt=0)
    abertura_s: StrictInt = Field(ge=1)
    fechamento_s: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def _sobra_tempo_para_os_claims(self) -> "Roteiro":
        if self.abertura_s + self.fechamento_s >= self.duracao_s:
            raise ValueError("abertura_s + fechamento_s precisa ser menor que duracao_s")
        return self


class Formatos(_Fechado):
    artigo: Artigo
    carrossel: Carrossel
    roteiro: Roteiro

    @model_validator(mode="after")
    def _formato_menor_nao_usa_mais_claims(self) -> "Formatos":
        if not self.artigo.max_claims >= self.carrossel.max_claims >= self.roteiro.max_claims:
            raise ValueError(
                "max_claims precisa ser artigo >= carrossel >= roteiro (encontrado: "
                f"{self.artigo.max_claims}, {self.carrossel.max_claims} e {self.roteiro.max_claims})"
            )
        return self


class RegraAnalogia(_Fechado):
    """Regra da M5 (só no Iniciante): expressões que marcam uma analogia no texto, na mesma unidade da
    primeira ocorrência do termo."""

    marcadores: list[str] = Field(min_length=1)

    @field_validator("marcadores")
    @classmethod
    def _expressoes(cls, marcadores: list[str]) -> list[str]:
        curtos = [m for m in marcadores if len(m.split()) < 2]
        if curtos:
            raise ValueError(f"cada marcador tem pelo menos duas palavras (encontrado: {', '.join(map(repr, curtos))})")
        normalizados = [" ".join(m.lower().split()) for m in marcadores]
        if len(set(normalizados)) != len(normalizados):
            raise ValueError("marcadores repetidos")
        return marcadores


class IflMinimo(_Fechado):
    """Piso do IFL por formato (M3): abaixo dele a peça é reprovada."""

    artigo: StrictFloat | StrictInt
    carrossel: StrictFloat | StrictInt
    roteiro: StrictFloat | StrictInt


class RegraLegibilidade(_Fechado):
    """Regra da M3 num nível: o piso do IFL por formato e a meta de tamanho de frase que o redator recebe. O nível
    sem o bloco só mede a legibilidade."""

    ifl_minimo: IflMinimo
    frase_max_palavras: StrictInt = Field(ge=5)


class LevelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nivel: Nivel
    versao: StrictInt = Field(ge=1)
    foco: str = Field(min_length=1)
    tags_priorizadas: list[Tag]
    tags_descartaveis: list[Tag]
    peso_papel: PesoPapel
    formatos: Formatos
    analogia: RegraAnalogia | None = None
    legibilidade: RegraLegibilidade | None = None

    @property
    def max_claims(self) -> int:
        """Claims usados no plano do nível: o máximo do artigo, o formato mais longo."""
        return self.formatos.artigo.max_claims

    @field_validator("tags_descartaveis")
    @classmethod
    def _so_metodologico(cls, tags: list[str]) -> list[str]:
        invalidas = [t for t in tags if t not in TAGS_DESCARTAVEIS_PERMITIDAS]
        if invalidas:
            raise ValueError(f"só aceita {', '.join(TAGS_DESCARTAVEIS_PERMITIDAS)} (encontrado: {', '.join(invalidas)})")
        return tags

    @model_validator(mode="after")
    def _sem_tag_em_comum(self) -> "LevelSpec":
        comuns = sorted(set(self.tags_priorizadas) & set(self.tags_descartaveis))
        if comuns:
            raise ValueError(f"tag em tags_priorizadas e em tags_descartaveis: {', '.join(comuns)}")
        return self

    @model_validator(mode="after")
    def _analogia_so_no_iniciante(self) -> "LevelSpec":
        if self.nivel == "iniciante" and self.analogia is None:
            raise ValueError("o bloco `analogia` é obrigatório no Iniciante")
        if self.nivel != "iniciante" and self.analogia is not None:
            raise ValueError("o bloco `analogia` só vale no Iniciante")
        return self


def carregar(raiz: Path, nivel: str) -> LevelSpec:
    """Lê e valida config/levels/<nivel>.yaml. `nivel` do arquivo precisa ser igual ao nome do arquivo."""
    relativo = f"config/levels/{nivel}.yaml"
    try:
        dados = yaml.safe_load((Path(raiz) / relativo).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as erro:
        raise FalhaTerminal(f"{relativo}: não foi possível ler ({erro})") from erro
    if not isinstance(dados, dict):
        raise FalhaTerminal(f"{relativo}: arquivo vazio ou fora do formato")
    if dados.get("nivel") != nivel:
        raise FalhaTerminal(f"{relativo}: `nivel` é {dados.get('nivel')!r}, esperado {nivel!r}")
    try:
        spec = LevelSpec.model_validate(dados)
    except ValidationError as erro:
        detalhes = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(raiz)'}: {e['msg']}" for e in erro.errors()
        )
        raise FalhaTerminal(f"{relativo}: LevelSpec inválido: {detalhes}") from erro
    carrossel = spec.formatos.carrossel
    if carrossel.max_claims > carrossel.slides.max - 2:
        warnings.warn(
            f"{relativo}: carrossel.max_claims ({carrossel.max_claims}) é maior que os slides de corpo "
            f"possíveis (slides.max - 2 = {carrossel.slides.max - 2}): um slide de corpo terá mais de um claim",
            UserWarning,
            stacklevel=2,
        )
    return spec


def carregar_todos(raiz: Path) -> dict[str, LevelSpec]:
    """Os 3 níveis, na ordem iniciante, intermediario e avancado. As palavras mínimas do artigo crescem com o nível."""
    specs = {nivel: carregar(raiz, nivel) for nivel in get_args(Nivel)}
    minimos = [specs[nivel].formatos.artigo.palavras.min for nivel in specs]
    if minimos != sorted(minimos):
        raise FalhaTerminal(
            "config/levels/: formatos.artigo.palavras.min precisa crescer do Iniciante ao Avançado "
            f"(encontrado: {', '.join(f'{n} {specs[n].formatos.artigo.palavras.min}' for n in specs)})"
        )
    return specs


class InstrucoesRedator(_Fechado):
    """O que o prompt do redator (E5.2) e a M2 leem do LevelSpec para um formato."""

    orcamento: str  # o contrato do formato, em frases
    marcadores: str  # as expressões de analogia do Iniciante, em frase; vazio nos outros níveis
    faixa_esperada: str  # o limite renderizado, que a M2 registra no scorecard
    legibilidade: str  # a meta de tamanho de frase do nível, em frase; vazio quando o nível não tem


def _numero(valor: float) -> str:
    """Número no formato do texto da peça: vírgula decimal, sem zero à toa."""
    return f"{valor:g}".replace(".", ",")


def instrucoes_redator(spec: LevelSpec, formato: Formato) -> InstrucoesRedator:
    f = spec.formatos
    if formato == "artigo":
        faixa = f"de {f.artigo.palavras.min} a {f.artigo.palavras.max} palavras no texto das seções"
        orcamento = (
            f"Escreva o artigo com {faixa} (o título e a linha fina não entram na contagem), "
            f"usando até {f.artigo.max_claims} claims."
        )
    elif formato == "carrossel":
        c = f.carrossel
        faixa = (
            f"até {c.caracteres.gancho} caracteres no gancho, {c.caracteres.corpo} em cada slide de corpo "
            f"e {c.caracteres.conclusao} na conclusão"
        )
        orcamento = (
            f"Escreva de {c.slides.min} a {c.slides.max} slides, contando o gancho e a conclusão: um gancho no "
            f"início, os slides de corpo e uma conclusão no fim, com {faixa}, usando até {c.max_claims} claims."
        )
    else:
        r = f.roteiro
        total = int(r.duracao_s * r.palavras_por_segundo)
        faixa = (
            f"até {_numero(r.palavras_por_segundo)} palavras por segundo em cada cena e até {total} palavras "
            f"em {r.duracao_s} segundos"
        )
        orcamento = (
            f"Escreva um roteiro de até {r.duracao_s} segundos, com {faixa}. Comece em 0 s, sem lacuna nem "
            f"sobreposição entre as cenas: uma cena de abertura com o gancho (cerca de {r.abertura_s} s), uma "
            f"cena por claim, até {r.max_claims} claims, e uma cena de fechamento (cerca de {r.fechamento_s} s)."
        )
    marcadores = ""
    if spec.analogia is not None:
        expressoes = ", ".join('"' + m + '"' for m in spec.analogia.marcadores)
        marcadores = (
            "Na primeira vez que usar um termo com analogia no plano, explique-o com a analogia na mesma seção, "
            f"slide ou cena, usando uma destas expressões: {expressoes}."
        )
    legibilidade = ""
    if spec.legibilidade is not None:
        legibilidade = (
            f"Escreva frases de até {spec.legibilidade.frase_max_palavras} palavras e prefira palavras curtas "
            "e comuns: a legibilidade do texto é medida."
        )
    return InstrucoesRedator(orcamento=orcamento, marcadores=marcadores, faixa_esperada=faixa, legibilidade=legibilidade)
