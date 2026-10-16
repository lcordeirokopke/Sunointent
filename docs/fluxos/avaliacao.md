# Avaliação (E6) e Router (E7)

Este documento detalha a E6 e a E7 de uma célula: como a peça de uma tentativa vira um scorecard e como o Router decide entre aprovar, reprocessar ou reprovar. O desenho das duas etapas, o `Scorecard`, a `Violacao` e o refinement loop estão em `docs/arquiteturas.md` (Scorecard e violação, E6, E7 e O Refinement Loop). As regras de cada métrica estão em `docs/metricas.md`. A peça que a E6 recebe está em `docs/fluxos/sintese.md`.

## Estado

Implementadas, com os nós `evaluate` e `router` e o subgrafo da célula (`graph.construir_grafo_celula`). O grafo principal dispara o subgrafo para as 9 células depois da E4, até 3 em paralelo, e o nó `fechamento` conta os estados finais (`docs/execucao.md`). O juiz (M6) entra no config pelo `__main__.py` em todo run; sem ele no config (nos testes), a E6 roda só as 4 determinísticas.

## Subgrafo da célula

`graph.construir_grafo_celula()` compila um `StateGraph` sobre `CelulaGrafoState` (`sunontent/schemas.py`):

```
START -> synth
synth -> evaluate     (a E5 gravou a peça)
synth -> router       (a E5 pegou uma Falha)
evaluate -> router
router -> synth       (retry: o relatório corretivo volta à E5)
router -> END         (APROVADA, REPROVADA ou ERRO_INFRA)
```

O `K` (2) é a constante de `sunontent/graph.py`, a mesma que entra no `RunManifest`, e chega ao nó por `partial(router, K=K)`. As dependências vêm de `config["configurable"]`: `repositorio`, `raiz_dados`, `raiz` e `llm`, como na E5, e, opcional, `juiz`.

O `CelulaGrafoState` leva, além do `run_id`, da `CelulaState` e do `relatorio` do retry:

| Campo | Escrito por | Uso |
| ----- | ----------- | --- |
| `falha` | E5 ou E6 | a `Falha` da tentativa (`classe` e `mensagem`), que a E7 grava como `ERRO_INFRA` |
| `tokens_tentativa` | E5 e E6 | tokens das chamadas de LLM da tentativa (o redator e o juiz), somados pelos spans que cada nó grava |
| `inicio_tentativa` | E5 | `time.monotonic()` no início da E5 da tentativa |

## E6. Avaliação

`nodes/evaluate.py`:

1. lê o LevelSpec do nível, o glossário, o plano por `carregar_plano` (sem plano, `FalhaTerminal`) e o FactSheet assinado por `carregar_factsheet`;
2. roda as 4 verificações determinísticas, sempre, todas, nesta ordem: `numeric_fidelity` (com os claims do FactSheet que estão no plano do nível, a `tabela_numeros` e a `data_documento`), `text_size`, `analogy_rule` e o `plan_contract` que a montagem da E5.3 deixou na célula;
3. se as 4 passaram e o config traz um `juiz`, lê os chunks do documento processado da E1 (`carregar_documento_processado`) e chama o juiz, que devolve o `MetricaResultado` da `llm_judge`, acrescentado no fim do scorecard. Sem `juiz`, o scorecard tem as 4;
4. monta o `Scorecard` da tentativa: o veredito é `APROVADA` se todas as métricas passaram, `REPROVADA` se alguma falhou e `ERRO_INFRA` se o juiz ou a leitura dos chunks levantou uma `Falha` (o scorecard leva então só as 4 determinísticas); `custo_tokens` é o `tokens_tentativa`; `duracao_ms` vai do `inicio_tentativa` ao fim da E6;
5. grava o scorecard por `persistencia.gravar_scorecard` e devolve a célula com o `scorecard`.

A E6 não grava estado de célula: a célula segue em `AVALIANDO` até a E7.

O juiz é chamado como `juiz(peca, plano_nivel, factsheet, chunks, glossario, run_id=..., registrar_span=...)`; o `registrar_span` grava o span e soma os tokens à tentativa. O juiz está em `docs/metricas.md`, M6.

## E7. Router

`nodes/router.py` avalia, em ordem:

| Regra | Condição | Resultado |
| ----- | -------- | --------- |
| 1 | `falha` no estado | `ERRO_INFRA`, com `erro_classe` e `erro_mensagem` da `Falha` |
| 2 | veredito `APROVADA` | `APROVADA` |
| 5 | `K >= 3`, tentativa 2 em diante, e a tentativa não melhorou a anterior | `REPROVADA`, motivo `nao_convergencia` |
| 3 | tentativa < `K` | relatório corretivo e volta à E5, sem gravar nada |
| 4 | tentativa == `K` | `REPROVADA`, motivo `k_esgotado` |

A regra 5 é avaliada antes das regras 3 e 4 e só com `K >= 3`; com o `K` de hoje (2), não age. "Não melhorou" é: as métricas reprovadas na anterior continuam reprovadas (podem aparecer mais) e nenhuma contagem de violações diminuiu. O scorecard da tentativa anterior é lido por `persistencia.carregar_scorecard`.

O estado final (`APROVADA`, `REPROVADA` ou `ERRO_INFRA`) é gravado por `persistencia.atualizar_celula`, com a tentativa da última peça gerada (0 quando a E5 falhou antes da primeira peça). A E7 é o único nó que grava estado final de célula. No retry, a E7 não grava nada: a E5 grava `GERANDO` ao começar a tentativa seguinte.

### Relatório corretivo

`router.relatorio_corretivo(scorecard)` monta o `RelatorioCorretivo` que o `prompts/synthesizer.j2` renderiza no retry:

- `certos`: uma linha por métrica que passou, `<métrica>: <valor> (esperado: <faixa>)`, ex.: `plan_contract: 0 (esperado: 0)`;
- `violacoes`: as violações de cada métrica, até 5 por métrica, na ordem do scorecard;
- `restantes`: por métrica, quantas violações ficaram de fora das 5.

O template agrupa as violações pelo lugar da peça (`docs/fluxos/sintese.md`, Prompt do redator).

## Falhas

| Onde | O que acontece |
| ---- | -------------- |
| E5: leitura, provider, resposta fora do schema esgotada ou gravação | a `Falha` vai para `falha`, o subgrafo segue direto à E7, e a célula termina em `ERRO_INFRA` sem scorecard |
| E6: leitura ou gravação do scorecard | a `Falha` vai para `falha`, e a célula termina em `ERRO_INFRA`; a peça da tentativa fica gravada, sem scorecard |
| E6: juiz | o scorecard é gravado com veredito `ERRO_INFRA`, e a célula termina em `ERRO_INFRA` |
| Exceção que não é `Falha` | sobe do nó: é bug e aborta o run |

`ERRO_INFRA` é final: não há retomada de célula.

## Saída

`persistencia.gravar_scorecard` grava numa transação, no Supabase:

- `scorecards`: a tentativa, o veredito, `custo_tokens` e `duracao_ms`; exige a peça da tentativa em `pecas`;
- `metricas`: uma linha por métrica, com o nome (enum `metrica`), o valor em `valor_num` ou, na `llm_judge`, em `valor_bool`, a faixa, `passou` e os `detalhes` (`jsonb`, com o modelo de cada métrica);
- `violacoes`: uma linha por violação, ligada à sua métrica, com o trecho, o esperado, o obtido, a instrução corretiva e a posição (`unidade_indice`, `campo`, `inicio` e `fim`, anuláveis), na ordem do scorecard.

O mesmo scorecard gravado de novo na mesma tentativa não grava nada; outro na mesma tentativa é `FalhaTerminal`. Depois, a cópia local: `data/runs/<run_id>/celulas/<celula_id>/scorecard_<tentativa>.json`.

`persistencia.atualizar_celula` grava em `celulas` o estado, a tentativa, o `motivo_reprovacao` (enum, só em `REPROVADA`) e `erro_classe` e `erro_mensagem` (só em `ERRO_INFRA`); os checks da tabela recusam qualquer outra combinação. A cópia local é o `estado.json` da célula.
