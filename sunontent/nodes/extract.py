"""E2. Extractor & Anchor: chama extracao/ e coloca o FactSheet não assinado no estado.

Na primeira rodada extrai do documento inteiro; nas seguintes, corrige só os claims que a E3
devolveu. Grava só os spans (llm_spans e custo em runs); o FactSheet é gravado pela E3.
Dependências vêm de config["configurable"]:
- repositorio: persistencia.Repositorio;
- raiz_dados: pasta raiz de data/;
- raiz: raiz do repositório, de onde vem prompts/;
- llm: llm.ClienteLLM.
"""

from pathlib import Path

from sunontent import persistencia
from sunontent.extracao import extractor
from sunontent.schemas import FactSheet


def extract(state, config) -> dict:
    configuravel = config["configurable"]
    repositorio, raiz_dados = configuravel["repositorio"], Path(configuravel["raiz_dados"])
    documento = state.documento
    rodada = state.rodada + 1

    def registrar_span(span):
        persistencia.gravar_span(repositorio, raiz_dados, span)

    argumentos = dict(run_id=state.run_id, registrar_span=registrar_span)
    if rodada == 1:
        propostas = extractor.extrair(configuravel["llm"], configuravel["raiz"], documento, **argumentos)
    else:
        propostas = extractor.corrigir(
            configuravel["llm"], configuravel["raiz"], documento, state.propostas, state.correcoes, **argumentos
        )
    claims, pendencias = extractor.ancorar(documento, propostas)
    factsheet = FactSheet(
        doc_id=documento.doc_id,
        tipo_documento=documento.tipo_documento,
        emissor=documento.emissor,
        data_documento=documento.data_documento,
        claims=claims,
        tabela_numeros=documento.tabela_numeros,
    )
    return {"rodada": rodada, "propostas": propostas, "pendencias": pendencias, "correcoes": {}, "factsheet": factsheet}
