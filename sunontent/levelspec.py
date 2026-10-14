"""Leitura e validação de config/levels/ (docs/fluxos/plano-editorial.md, LevelSpec da E4).

O LevelSpec existe só em config/levels/: o prompt e o avaliador leem o mesmo arquivo. Hoje o arquivo
tem só os campos da E4; os das métricas e dos formatos entram com a M2 a M5 e a E5. Arquivo inválido
é FalhaTerminal: o run aborta no início da E4.
"""

from pathlib import Path
from typing import get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator, model_validator

from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import Nivel, Tag

# Única tag que corresponde a um motivo de descarte (docs/arquiteturas.md, E4.1).
TAGS_DESCARTAVEIS_PERMITIDAS = ("metodologico",)


class LevelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nivel: Nivel
    versao: StrictInt = Field(ge=1)
    foco: str = Field(min_length=1)
    tags_priorizadas: list[Tag]
    tags_descartaveis: list[Tag]
    max_claims: StrictInt = Field(ge=3)  # o núcleo tem até 3 claims e entra nos 3 níveis

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
