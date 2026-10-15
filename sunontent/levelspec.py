"""Leitura e validação de config/levels/ (docs/fluxos/plano-editorial.md, LevelSpec da E4).

O LevelSpec existe só em config/levels/: o prompt e o avaliador leem o mesmo arquivo. Hoje o arquivo
tem os campos da E4 e o contrato de formato (`peso_papel` e `formatos`), lido pela E4 (o `max_claims` do
plano é o do artigo) e, depois, pela E5.1 e pela M2; os campos das métricas entram com a M2 a M5. Arquivo
inválido é FalhaTerminal: o run aborta no início da E4.
"""

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
from sunontent.schemas import Nivel, Tag

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


class LevelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nivel: Nivel
    versao: StrictInt = Field(ge=1)
    foco: str = Field(min_length=1)
    tags_priorizadas: list[Tag]
    tags_descartaveis: list[Tag]
    peso_papel: PesoPapel
    formatos: Formatos

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
        return LevelSpec.model_validate(dados)
    except ValidationError as erro:
        detalhes = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(raiz)'}: {e['msg']}" for e in erro.errors()
        )
        raise FalhaTerminal(f"{relativo}: LevelSpec inválido: {detalhes}") from erro


def carregar_todos(raiz: Path) -> dict[str, LevelSpec]:
    """Os 3 níveis, na ordem iniciante, intermediario e avancado."""
    return {nivel: carregar(raiz, nivel) for nivel in get_args(Nivel)}
