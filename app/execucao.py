"""Execução do pipeline pelo dashboard: um processo `python -m sunontent [fonte] [--dev]` por vez.

O processo roda na raiz do repositório, com o mesmo Python do servidor. Cada linha do terminal (stdout e
stderr) vira um evento numerado e classificado por `classificar`, guardado em memória até a próxima execução.
O log não é gravado em disco: o resultado de cada run fica em data/runs/, gravado pelo próprio pipeline.
"""

import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

FONTES = ("copom", "cvm", "b3")

_RUN = re.compile(r"^run (?P<run_id>\S+) \((?P<modo>normal|dev)\)$")
_ETAPA = re.compile(r"^etapa (?P<no>\w+): (?P<estado>iniciada|concluída)$")
_CONCLUIDO = re.compile(r"^concluído: (?P<resumo>.*)$")
_ABORTADO = re.compile(r"^abortado \((?P<classe>\w+)\): (?P<mensagem>.*)$")
_ERRO = re.compile(r"^erro \((?P<classe>\w+)\): (?P<mensagem>.*)$")
_PADROES = (("run", _RUN), ("etapa", _ETAPA), ("concluido", _CONCLUIDO), ("abortado", _ABORTADO), ("erro", _ERRO))


def classificar(texto: str) -> dict:
    """Tipo da linha escrita pelo __main__.py e os campos extraídos dela; o resto é `log`."""
    for tipo, padrao in _PADROES:
        encontrado = padrao.match(texto)
        if encontrado:
            return {"tipo": tipo, **encontrado.groupdict()}
    return {"tipo": "log"}


def montar_comando(fonte: str | None, dev: bool) -> list[str]:
    if fonte is not None and fonte not in FONTES:
        raise ValueError(f"fonte desconhecida: {fonte}")
    return [sys.executable, "-m", "sunontent", *([fonte] if fonte else []), *(["--dev"] if dev else [])]


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Execucao:
    """Um processo do pipeline e os eventos que ele gerou."""

    def __init__(self, comando: list[str], raiz: Path, fonte: str | None, dev: bool):
        self.comando = comando
        self.fonte = fonte
        self.dev = dev
        self.inicio = _agora()
        self.estado = "rodando"  # rodando, concluida, falhou, parada
        self.codigo: int | None = None
        self.eventos: list[dict] = []
        self._trava = threading.Lock()
        self._parada = False
        ambiente = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self._processo = subprocess.Popen(
            comando,
            cwd=raiz,
            env=ambiente,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._leitores = [
            threading.Thread(target=self._ler, args=(self._processo.stdout, "stdout"), daemon=True),
            threading.Thread(target=self._ler, args=(self._processo.stderr, "stderr"), daemon=True),
        ]
        for leitor in self._leitores:
            leitor.start()
        threading.Thread(target=self._aguardar, daemon=True).start()

    def _adicionar(self, evento: dict) -> None:
        with self._trava:
            self.eventos.append({"n": len(self.eventos), "hora": _agora(), **evento})

    def _ler(self, fluxo, canal: str) -> None:
        for linha in fluxo:
            texto = linha.rstrip("\r\n")
            if texto:
                self._adicionar({"canal": canal, "texto": texto, **classificar(texto)})

    def _aguardar(self) -> None:
        codigo = self._processo.wait()
        for leitor in self._leitores:
            leitor.join()
        self.codigo = codigo
        self.estado = "parada" if self._parada else ("concluida" if codigo == 0 else "falhou")
        self._adicionar({"canal": "sistema", "tipo": "fim", "codigo": codigo, "estado": self.estado, "texto": ""})

    @property
    def terminou(self) -> bool:
        return self.estado != "rodando"

    def parar(self) -> None:
        if not self.terminou:
            self._parada = True
            self._processo.terminate()

    def eventos_desde(self, n: int) -> list[dict]:
        with self._trava:
            return self.eventos[n:]

    def resumo(self) -> dict:
        return {
            "comando": " ".join(["python", *self.comando[1:]]),
            "fonte": self.fonte,
            "dev": self.dev,
            "inicio": self.inicio,
            "estado": self.estado,
            "codigo": self.codigo,
        }


class Gerenciador:
    """Guarda a execução atual e recusa uma nova enquanto ela roda."""

    def __init__(self, raiz: Path):
        self.raiz = raiz
        self.atual: Execucao | None = None
        self._trava = threading.Lock()

    def iniciar(self, fonte: str | None, dev: bool) -> Execucao:
        comando = montar_comando(fonte, dev)
        with self._trava:
            if self.atual is not None and not self.atual.terminou:
                raise RuntimeError("já existe uma execução em andamento")
            self.atual = Execucao(comando, self.raiz, fonte, dev)
            return self.atual
