# Plano editorial (E4)

Este documento detalha a E4: como o `FactSheet` assinado vira o `PlanoEditorial` dos 3 níveis e as 9 células em `PENDENTE`. O desenho da etapa (pré-filtro, planejador, complemento e gate, e os objetos `PlanoEditorial`, `PlanoNivel`, `Descarte`, `ClaimPlanejado` e `Analogia`) está em `docs/arquiteturas.md`, E4 e "Plano editorial". O glossário e a regra de casamento de termos estão em `docs/glossario.md`. A E2 e a E3, que entregam o FactSheet, estão em `docs/fluxos/extracao.md`.

## Estado

Implementada. O grafo termina na E4: o run fecha com o plano gravado e as 9 células em `PENDENTE`.

## Responsabilidade

- **Pré-filtro (código):** por nível, separa os candidatos dos descartados por regra (`metodologico`, `origem="prefiltro"`) e marca os claims candidatos nos 3 níveis, os únicos que podem ser núcleo.
- **Planejador (LLM, 1 chamada para os 3 níveis):** escolhe o núcleo, a seleção, a ordem, o papel, a tese, os descartes com motivo específico e as analogias `nova`. Na correção, o prompt traz as mesmas regras e claims, o plano enviado e só a lista de problemas do gate; o modelo devolve o plano inteiro de novo, com o raciocínio `adapter_correcao`.
- **Complemento e gate (código):** completa `atribuir_fonte`, os descartes `secundario` e as analogias do glossário, deixa na tese só os ids dos claims usados no nível, confere as 6 regras do gate e devolve só os problemas ao planejador, em até 3 rodadas.
- **Gravação:** `validacoes_plano` por rodada, `planos_editoriais`, `planos_nivel` e as 9 linhas de `celulas` em `PENDENTE`.

## Módulos

| Arquivo | Função |
|---|---|
| `sunontent/nodes/adapt.py` | Nó da E4: roda pré-filtro, planejador, complemento e gate em loop, grava as rodadas, o plano e as 9 células. Grava os spans. |
| `sunontent/adaptacao/` | Pré-filtro, planejador (monta o prompt de `prompts/adapter.j2` e chama o modelo), complemento e gate. |
| `sunontent/levelspec.py` | Leitura e validação de `config/levels/`. Usado pela E4 e, depois, pela E5 e pelas métricas. |
| `sunontent/glossario.py` | Leitura e validação de `config/glossario.yaml`, casamento de termos e escolha da analogia do glossário. Usado pela E4 e, depois, pela M4, pela M5 e pelo comando das analogias da E8. |
| `prompts/adapter.j2` | Template único do planejador, parametrizado pelo LevelSpec dos 3 níveis. |

## LevelSpec da E4

A E4 lê de cada `config/levels/<nivel>.yaml` os campos `foco`, `tags_priorizadas`, `tags_descartaveis` e `max_claims`. Os campos das métricas e dos formatos (legibilidade, densidade, analogia, contratos de formato) entram no LevelSpec junto com a M2 a M5 e a E5.

| Campo | Iniciante | Intermediário | Avançado |
|---|---|---|---|
| `foco` | impacto prático no bolso do leitor | alocação e tendências | análise técnica e institucional |
| `tags_descartaveis` | `[metodologico]` | `[]` | `[]` |
| `tags_priorizadas` | `[impacto_pratico]` | `[indicador_chave, contexto_macro]` | `[indicador_chave, metodologico, governanca]` |
| `max_claims` | 5 | 7 | 9 |

Por que esses valores:

- **Iniciante:** o foco é o efeito prático. Premissa e método de cálculo (no Copom, as hipóteses de câmbio, petróleo e bandeira tarifária do cenário de referência) são o que o leitor iniciante não usa, e saem por regra. `governanca` não é descartável: no fato relevante da CVM, o evento principal é societário.
- **Intermediário:** o foco é alocação e tendências. Os números centrais e o cenário macro orientam a alocação, e as premissas do cenário também interessam, por isso nada sai por regra; o planejador corta com `exige_base_tecnica` ou `fora_do_foco`.
- **Avançado:** o foco é analítico. Método e governança (hedge, alíquota efetiva, diretoria, órgãos) são material do leitor institucional.
- **`max_claims`:** o mesmo plano serve aos 3 formatos, e o roteiro de 60 s precisa cobrir todos os claims do plano. A 2 a 3 palavras por segundo, o roteiro tem de 120 a 180 palavras, cerca de 25 por claim. O Iniciante fica com 5, porque as analogias ocupam parte do roteiro; o Intermediário com 7; o Avançado com 9, com texto mais denso. O mínimo é 3, porque o núcleo tem até 3 claims e entra nos 3 níveis.

Os valores são de partida e sobem a `versao` de cada YAML quando mudam.

### Validação

O `levelspec.py` valida cada arquivo com Pydantic, sem aceitar campo desconhecido:

- `nivel` igual ao nome do arquivo;
- `tags_priorizadas` e `tags_descartaveis` só com tags do vocabulário `Tag` (`sunontent/schemas.py`), sem tag em comum entre as duas listas;
- `tags_descartaveis` só aceita `metodologico`, a única tag que corresponde a um motivo de descarte;
- `max_claims` inteiro maior ou igual a 3.

Arquivo inválido é erro terminal: o run aborta no início da E4.

## Glossário

### Leitura e validação

O `glossario.py` lê `config/glossario.yaml` e valida, ao carregar:

- todos os campos de cada termo, com os valores permitidos de `categoria`, `nivel_minimo` e `status` (`docs/glossario.md`, Formato e campos);
- `id` único, e cada forma (o `termo` e cada variante) numa única entrada, comparada sem maiúsculas e sem acentos;
- `limite` preenchido em toda analogia;
- nenhum dígito em `definicao_simples`, `definicao_tecnica`, `texto` e `limite` das analogias, porque a M1 trata todo número da peça como afirmação sobre o documento.

Glossário inválido é erro terminal: o run aborta no início da E4.

### Casamento de termos

O casamento segue a regra de `docs/glossario.md`, Casamento no texto: formas explícitas, sem lematizar, ignorando maiúsculas e acentos, sigla só com a caixa exata, termo `ambiguo` só pelas formas de mais de uma palavra, a forma mais longa vence, e os marcadores `{{...}}` ficam fora. Sigla é a forma escrita só com maiúsculas, dígitos e sinais (`CDI`, `IPCA-15`, `NTN-B`, `EMBI+`). Espaço e quebra de linha entre as palavras de uma forma se equivalem.

O casador devolve, para um texto, cada ocorrência com o `id` do termo, a forma casada e as posições de início e fim no texto. A E4 usa só os termos encontrados; a M4 e a M5 usam também as posições (termos únicos para a densidade, primeira ocorrência para a janela da analogia). Um filtro devolve só os termos com `nivel_minimo` acima de um nível.

Um teste roda o casador sobre os claims dos 3 documentos de referência (`tests/fixtures/referencia/`) e lista os termos do glossário escritos com flexão que não está em `variantes`, para completar o glossário.

### Analogia do glossário

Para um termo, a analogia do complemento é a primeira com `status: aprovada` ou, sem nenhuma aprovada, a primeira `rascunho`, com a referência `<id>#<n>` (posição na lista, começando em 1). `revisada` é `true` só para a aprovada. Termo sem analogia no glossário vai para a lista que o planejador recebe, para criar uma analogia `nova`.

A analogia `nova` vale para o termo da lista cujo nome casa, sem maiúsculas e sem acento, com o `termo` ou uma variante. O complemento deixa de fora a `nova` de termo fora da lista, de termo que já tem analogia no glossário e de termo fora dos claims usados.

### Gate

As 6 regras são as de `docs/arquiteturas.md`, E4.3. `contraditorio` também acusa o claim que aparece duas vezes nos claims do mesmo nível.

## Modelo

| Nó | Snapshot | Raciocínio |
|---|---|---|
| `adapter` | `gemini-3.8-flash` | padrão do modelo na rodada 1; `low` nas correções (chave `adapter_correcao` em `raciocinio` de `config/models.yaml`) |

O preço é o mesmo já registrado para o extractor em `config/models.yaml`. Uma rodada no release da B3 (89 claims) fica perto de 10 mil tokens de entrada e de 4 a 6 mil de saída, contando o raciocínio: cerca de US$ 0,03 por rodada, até 3 rodadas.

O nó troca para `gemini-4-argon` só pelo `config/models.yaml`, com o preço do snapshot, se os runs mostrarem o gate reprovando com frequência ou o revisor da E8 discordando das seleções. Preços e status consultados em 2026-10-14 em https://ai.google.dev/gemini-api/docs/pricing e https://ai.google.dev/gemini-api/docs/models: `gemini-3.8-flash` a US$ 0,75 de entrada e US$ 3,75 de saída por milhão de tokens até 2026-12-31 (US$ 1,50 e US$ 7,50 a partir de 2027-01-01); `gemini-4-argon` a US$ 2,00 e US$ 10,00 até 2026-11-30 (US$ 4,00 e US$ 20,00 a partir de 2026-12-01).

## Grafo

```
... validate (sem claim para corrigir) -> adapt -> END
... quarentena                         -> adapt -> END
```

- A E4 entra depois da E3 com todos os claims aprovados e depois da quarentena; o plano usa só os claims do FactSheet assinado.
- O loop do gate fica dentro do nó `adapt`, que é um nó só para os 3 níveis: cada rodada atualiza `rodada_plano` e grava as suas linhas em `validacoes_plano`.
- O grafo termina depois da E4. As 9 células ficam em `PENDENTE` em `celulas`, e o run fecha como `concluido`.
- O `thread_id` continua sendo o `run_id`: o formato `<run_id>:<celula_id>` começa no fan-out da E5.
- Na retomada, o nó confere se `planos_editoriais` já tem o plano do run; se tem, reaproveita o plano e as células, grava a cópia local que faltar e não chama o modelo. Sem plano gravado, o nó apaga as linhas de `validacoes_plano` do run e a cópia local delas antes da rodada 1.
- O limite de rodadas é `RODADAS_PLANO` (3), em `graph.py`, passado ao nó `adapt`.

## Saída

- Supabase: `validacoes_plano` (uma linha por problema, por rodada), `planos_editoriais`, `planos_nivel` e `celulas` (9 linhas, estado `PENDENTE`, tentativa 0). Plano e células são gravados numa transação.
- Cópia local em `data/runs/<run_id>/`: `validacoes_plano/<rodada>.json`, `plano.json` e `celulas/<celula_id>/estado.json`. `validacoes_plano/<rodada>.json` existe em toda rodada, com `problemas: []` na que passou.
- A retirada dos ids inexistentes, depois da última rodada, tira o id do núcleo, dos claims, dos descartes e da tese, e `relacionado` inexistente vira nulo.
- Estado do grafo: `rodada_plano`, `plano` e `celulas`.

## Terminal e dashboard

- O terminal ganha as linhas `etapa adapt: iniciada` e `etapa adapt: concluída` (`docs/execucao.md`, Linhas de etapa no terminal). A linha final diz em quantas rodadas o plano ficou pronto e, com `aviso`, que o plano tem aviso: `concluído: <doc> (<hash>), 22 claims, 1 em quarentena, plano em 2 rodadas (com aviso), 10836 tokens, R$ 0.1516`.
- No dashboard, a E4 entra como etapa da lista do fluxo, com o nó `adapt`. A subaba da E4 e a aba Resultado mostram o plano gravado: o núcleo, o aviso, e, por nível, a tese, os claims usados, as analogias e os descartes, além dos problemas do gate por rodada (`docs/fluxos/dashboard.md`, Plano editorial).

## Como falha

Os modos de falha do plano (trivialização, núcleo mal escolhido, analogia enganosa) estão em `docs/arquiteturas.md`, E4. Os próprios desta implementação:

- LevelSpec ou glossário inválido: erro terminal no início da E4, antes de chamar o modelo.
- Gate ainda com problema depois da 3ª rodada: o run segue com `aviso`, e as células dos níveis afetados chegam à E8 com esse aviso.
- Núcleo vazio ou nível sem claim depois da retirada dos ids inexistentes: erro terminal, depois de gravar as rodadas em `validacoes_plano`.
- Falha do modelo: a mesma regra do `llm.py` da E2 (backoff na falha de infraestrutura, nova tentativa na resposta fora do schema, troca de chave em `docs/fluxos/extracao.md`, Cliente LLM).
