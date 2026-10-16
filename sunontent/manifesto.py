"""Monta o RunManifest a partir de config/ e prompts/ (docs/arquiteturas.md, Run manifest).

Versão: chave `versao` no topo de cada YAML e linha `{# versao: N #}` no início de cada template.
Hash: SHA-256 do conteúdo com fim de linha normalizado para `\\n`.
Template vazio (etapa ainda sem agente) fica fora de `prompts` e de `arquivos_sha256`.
"""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import get_args

import yaml

from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import Fonte, Modo, Nivel, RunManifest

_CABECALHO_PROMPT = re.compile(r"\A\{#\s*versao:\s*(\d+)\s*#\}")


def _ler(raiz: Path, relativo: str) -> bytes:
    caminho = raiz / relativo
    try:
        return caminho.read_bytes().replace(b"\r\n", b"\n")
    except OSError as erro:
        raise FalhaTerminal(f"{relativo}: não foi possível ler ({erro})") from erro


def _yaml(relativo: str, conteudo: bytes) -> dict:
    try:
        dados = yaml.safe_load(conteudo)
    except yaml.YAMLError as erro:
        raise FalhaTerminal(f"{relativo}: YAML inválido: {erro}") from erro
    if not isinstance(dados, dict):
        raise FalhaTerminal(f"{relativo}: arquivo vazio ou sem a chave `versao` no topo")
    return dados


def _versao(relativo: str, dados: dict) -> int:
    versao = dados.get("versao")
    if not isinstance(versao, int) or isinstance(versao, bool) or versao < 1:
        raise FalhaTerminal(f"{relativo}: `versao` precisa ser um inteiro >= 1 (encontrado: {versao!r})")
    return versao


def _sha256(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def montar(
    raiz: Path,
    *,
    run_id: str,
    timestamp: datetime,
    modo: Modo,
    fonte: Fonte,
    k: int,
    max_concorrencia: int,
) -> RunManifest:
    """Lê os arquivos versionados em `raiz` e devolve o manifest sem os campos do documento."""
    raiz = Path(raiz)
    hashes: dict[str, str] = {}

    def yaml_versionado(relativo: str) -> dict:
        conteudo = _ler(raiz, relativo)
        hashes[relativo] = _sha256(conteudo)
        dados = _yaml(relativo, conteudo)
        _versao(relativo, dados)
        return dados

    modelos_yaml = yaml_versionado("config/models.yaml")
    modelos = modelos_yaml.get("modelos") or {}
    if not isinstance(modelos, dict):
        raise FalhaTerminal("config/models.yaml: `modelos` precisa ser um mapa nó -> snapshot")

    glossario = yaml_versionado("config/glossario.yaml")

    level_specs: dict[str, int] = {}
    for nivel in get_args(Nivel):
        relativo = f"config/levels/{nivel}.yaml"
        dados = yaml_versionado(relativo)
        if dados.get("nivel", nivel) != nivel:
            raise FalhaTerminal(f"{relativo}: `nivel` é {dados['nivel']!r}, esperado {nivel!r}")
        level_specs[nivel] = dados["versao"]

    prompts: dict[str, int] = {}
    for template in sorted((raiz / "prompts").glob("*.j2")):
        relativo = f"prompts/{template.name}"
        conteudo = _ler(raiz, relativo)
        if not conteudo.strip():
            continue
        cabecalho = _CABECALHO_PROMPT.match(conteudo.decode("utf-8", errors="replace"))
        if cabecalho is None:
            raise FalhaTerminal(f"{relativo}: falta a linha `{{# versao: N #}}` no início do arquivo")
        prompts[template.stem] = int(cabecalho.group(1))
        hashes[relativo] = _sha256(conteudo)

    return RunManifest(
        run_id=run_id,
        timestamp=timestamp,
        modo=modo,
        fonte=fonte,
        modelos={str(no): str(snapshot) for no, snapshot in modelos.items()},
        prompts=prompts,
        level_specs=level_specs,
        glossario_versao=glossario["versao"],
        arquivos_sha256=dict(sorted(hashes.items())),
        K=k,
        max_concorrencia=max_concorrencia,
    )
