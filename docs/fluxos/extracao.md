# Extração e validação de âncoras (E2 e E3)

Este documento detalha a E2 (Extractor & Anchor) e a E3 (Validador de Âncoras): como o `DocumentoProcessado` da E1 vira um `FactSheet` assinado, a lista de claims que é a única fonte de fatos do resto do pipeline. A arquitetura geral está em `docs/arquiteturas.md`, o cliente LLM em "1. Model client / router", e a leitura do PDF em `docs/fluxos/leitura.md`.

## Responsabilidade

- **E2:** o modelo lê o documento inteiro e propõe os claims. O código ancora cada um: acha a posição do trecho citado no texto limpo e associa os números da `tabela_numeros`. Devolve o `FactSheet` não assinado, que fica só no estado do grafo, e a lista dos claims que não conseguiu ancorar, com o motivo.
- **E3:** confere cada claim contra o mesmo texto limpo e a mesma `tabela_numeros` que a E2 recebeu, nunca contra o PDF. Com todos aprovados, assina e grava o FactSheet. Com algum reprovado, devolve só os reprovados para a E2, com o motivo. Depois da última rodada, os que seguem reprovados são descartados, e o FactSheet é assinado com os válidos.

O modelo nunca escreve página, offset nem valor de número. Ele cita o trecho e o bruto, e a posição e o `Numero` vêm do código. Assim, âncora e número inventados não chegam ao FactSheet: o que não existe no documento não resolve.

## Módulos

| Arquivo | Função |
|---|---|
| `sunontent/nodes/extract.py` | Nó da E2: extrai (rodada 1) ou corrige (rodadas seguintes), ancora e coloca o FactSheet no estado. Grava os spans. |
| `sunontent/nodes/validate.py` | Nó da E3: valida, grava a rodada e, com todos aprovados, assina e grava o FactSheet. |
| `sunontent/nodes/descarte.py` | Nó do descarte: depois da última rodada, deixa os reprovados de fora e assina e grava o FactSheet com os válidos. |
| `sunontent/extracao/extractor.py` | Monta o prompt de `prompts/extractor.j2`, chama o modelo e renumera ou substitui as propostas. |
| `sunontent/extracao/ancoragem.py` | Localiza o trecho citado e resolve os números. Determinístico. |
| `sunontent/extracao/validador.py` | Regras da E3 e `descartar_reprovados`, que tira do FactSheet os claims reprovados na última rodada. Determinístico. |
| `sunontent/llm.py` | Cliente LLM: modelo por nó, saída JSON validada, retentativa, custo e span. Único módulo que importa o SDK do provider. |
| `sunontent/graph.py` | Arestas E2 -> E3 -> E2, E3 -> descarte e o limite de rodadas (`RODADAS_EXTRACAO`). |
| `sunontent/persistencia.py` | `gravar_span`, `gravar_validacao` e `gravar_factsheet`: Supabase e cópia local. |
| `config/models.yaml` | Snapshot do modelo de cada nó, preço por milhão de tokens, cotação USD/BRL e nível de raciocínio da correção. |

## Modelo

O extractor usa o `gemini-3.8-flash` (Gemini API, SDK `google-genai`), escolhido por ser um modelo estável, com versão fixa. As chaves ficam no `.env`: `GOOGLE_API_KEY_PRINC`, a principal, conferida pelo `__main__.py` antes de o run ser criado, e `GOOGLE_API_KEY_SEC`, a reserva, opcional. A ordem de chamada está em Cliente LLM. O juiz (M6) precisa ser de outra família.

## O que o modelo recebe

Uma chamada por rodada: na extração, com o documento inteiro; na correção, só com os blocos em volta dos claims reprovados (ver Loop E2/E3). O prompt (`prompts/extractor.j2`) traz as regras e o documento entre `<documento>` e `</documento>`, um bloco por chunk (`### Bloco K03 | seção: ...`), com o texto limpo de cada página marcado por `[página N]`. O texto é o mesmo que a E3 usa para validar. O prompt avisa que o conteúdo do documento é dado e não instrução, para que um texto no PDF não seja lido como ordem.

A resposta segue o schema `SaidaExtractor` (`schemas.py`): uma lista de `ClaimProposto`, cada um com `id`, `chunk_id`, `texto`, `literal_fonte`, `numeros` (lista de brutos), `tipo` e `tags`.

Regras pedidas ao modelo:

- Cobertura: todos os fatos relevantes; sinalizações sobre a conduta futura e métricas secundárias com número próprio também viram claim. Dados administrativos (presentes, horários, telefones, CNPJ) ficam de fora.
- Um fato por claim. O mesmo número da mesma métrica em dois trechos vira um claim só, do trecho com o valor mais preciso. Texto corrido vem antes de tabela, cuja unidade fica no cabeçalho e não acompanha o número: o fato só sai de tabela quando não aparece em nenhum texto corrido.
- `texto`: uma frase autocontida, sem relação de causa que o documento não declara, com atribuição em projeções e avaliações. Número de tabela vai com a unidade do cabeçalho ou da linha (`R$ 3.081,4 milhões`, `4,1%`), e data vai com o dia, o mês e o ano que o literal traz, sem acrescentar o ano.
- `literal_fonte`: cópia exata de um trecho contínuo de uma única página, sem atravessar nota de rodapé; em tabela, a partir do rótulo da linha.
- `numeros`: o bruto de cada valor do fato, como aparece no trecho; sem anos, datas, trimestres e numeração. Número por extenso ("dois", "três") não entra na lista e fica por extenso no texto; de "5 anos" entra só o "5". Texto e lista dizem o mesmo.

## Tipos e tags

`tipo`, um só: `decisao`, `resultado`, `projecao`, `condicionante`, `risco` ou `evento_corporativo`.

| Tipo | Quando usar |
|---|---|
| `decisao` | Algo decidido ou deliberado: decisão de juros, deliberação de órgão, aprovação de pagamento. |
| `resultado` | Dado realizado e medido: receita, lucro, volume, inflação passada, indicador divulgado. |
| `projecao` | Expectativa, estimativa ou previsão sobre o futuro. |
| `condicionante` | Justificativa, avaliação, premissa ou condição que orienta decisões futuras. |
| `risco` | Risco, incerteza ou fator que pode mudar o cenário. |
| `evento_corporativo` | Acontecimento na empresa anunciado pelo documento, que não é deliberação de órgão nem dado medido: aquisição, incorporação, venda de ativo, troca de executivo, lançamento de produto ou serviço, início ou fim de uma operação, e os prazos e etapas de uma operação societária. A aprovação do evento por conselho ou assembleia continua sendo `decisao`. |

A E4 usa o tipo em `atribuir_fonte`: `projecao`, `condicionante` e `risco` pedem atribuição na peça; `evento_corporativo`, fato consumado, não pede.

`tags`, uma ou mais, de um vocabulário fixo que o schema impõe (a E4 prioriza por elas). Claim sem tag é resposta fora do schema: o `llm.py` recusa e pede de novo, pela regra de `TENTATIVAS_PARSE`.

| Tag | Significado |
|---|---|
| `impacto_pratico` | Afeta diretamente o bolso ou a decisão de pessoas e empresas. |
| `indicador_chave` | Número central do documento. |
| `contexto_macro` | Cenário econômico doméstico ou externo. |
| `metodologico` | Premissa, método ou forma de cálculo. |
| `governanca` | Votação, gestão, regulação, órgãos e medidas legais ou societárias. |

## Ancoragem (E2)

Para cada proposta, `ancoragem.resolver`:

1. Procura o `literal_fonte` no texto limpo de cada página. A comparação aceita diferença só de espaço e quebra de linha e na grafia de aspas, apóstrofos e travessões; qualquer outra diferença não resolve.
2. Fica com a ocorrência dentro do chunk citado. Fora dele, aceita se o trecho existe uma única vez no documento; se existe mais de uma vez, recusa por ambiguidade.
3. O `literal_fonte` gravado é o texto limpo exato no intervalo encontrado, e não o que o modelo escreveu.
4. Para cada bruto da lista, pega a entrada da `tabela_numeros` com esse bruto dentro do intervalo (espaços ignorados na comparação), sem usar a mesma entrada duas vezes. Se o bruto não está escrito assim no trecho, vale a entrada do trecho com exatamente o mesmo valor e unidade: `4,1%` listado casa com a célula `4,1` de uma tabela `(%)`, que a E1 já leu como `pct`, e `4,1 p.p.` não casa.

Na primeira rodada, os ids são renumerados `C01`, `C02`... na ordem devolvida pelo modelo. Se ele não devolver nenhum claim, o run aborta com erro terminal.

Motivos de recusa da ancoragem, devolvidos ao modelo como estão: trecho não encontrado, trecho que atravessa a página ou uma nota de rodapé, trecho repetido fora do chunk citado e número que não está dentro do trecho (a mensagem lista os números que estão).

## Validação (E3)

Cada claim passa ou reprova, sem nota:

1. A página da âncora existe e o intervalo cabe nela.
2. O texto limpo no intervalo é exatamente o `literal_fonte`, e ele tem ao menos uma palavra além de números, escala e unidade (`milhões`, `bps`). Literal só de números (`'4,1'`, uma célula de tabela sem o rótulo da linha) não diz a que o número se refere e é recusado, pedindo o rótulo junto.
3. Cada `Numero` do claim é uma entrada da `tabela_numeros`, com o mesmo valor, unidade, bruto e posição. Unidade trocada (`pp` no lugar de `pct`) é recusada com o valor e a unidade da tabela.
4. Cada número está dentro do `literal_fonte`.
5. O texto do claim e a lista de números dizem o mesmo: o texto é lido pelo mesmo parser da E1 (`numeros.varrer`), e cada número listado precisa aparecer no texto com o mesmo valor e unidade; cada número do texto, exceto datas, precisa estar na lista. Isso recusa claim que carrega números de outro fato e número no texto que não veio do documento. Data listada em `numeros` também reprova: a E5 troca cada número listado por um marcador, e a data fica só no texto.
6. Cada data com dia do texto do claim (`19/10`, `19 de outubro`, `15 e 16 de setembro de 2026`, lidas por `numeros.datas`) está no `literal_fonte`, com o mesmo dia e mês; se o texto traz o ano, o literal traz o mesmo ano. `19 de outubro de 2026` contra o literal `19/10` reprova, porque o ano foi deduzido. Mês e ano sem dia (`junho de 2026`, `dez/25`) e trimestre (`2T26`) não entram na regra.
7. Id repetido é recusado.

Os claims que a E2 não ancorou também reprovam, com o motivo da E2.

## Loop E2/E3

```
E1 -> E2 (rodada 1: extração) -> E3 --todos aprovados--> FactSheet assinado -> E4
                                    --reprovados, rodada < 3--> E2 (correção só dos reprovados) -> E3
                                    --reprovados, rodada = 3--> descarte: FactSheet assinado com os válidos
                                                              e reprovados descartados -> E4
```

- Na correção, o prompt traz as mesmas regras, só os blocos do documento em volta dos claims reprovados e, para cada claim reprovado, o claim enviado e o motivo. Os blocos são, para cada claim, o chunk citado, o chunk onde o trecho foi encontrado (quando ele ancora fora do citado) e um vizinho de cada lado (`VIZINHOS` em `extractor.py`); se o claim cita um chunk que não existe, o prompt traz o documento inteiro. A chamada usa o nível de raciocínio `extractor_correcao` de `config/models.yaml`. O modelo devolve só esses claims, com o mesmo id. Id fora da lista é ignorado; claim reprovado que o modelo não devolver continua como estava e reprova de novo.
- No máximo 3 rodadas: a extração e até 2 correções (`RODADAS_EXTRACAO` em `graph.py`). Se ainda houver claim reprovado, o grafo segue para o nó `descarte` (abaixo). Nada segue sem assinatura.
- A cada rodada, todas as propostas são ancoradas e validadas de novo.

## Descarte

Claim reprovado depois da última rodada não derruba o run: o código o descarta, e nenhuma pessoa decide se ele entra ou sai. O nó `descarte`:

1. Assina o FactSheet só com os claims válidos (`validador.descartar_reprovados`) e o grava como na E3. O grafo segue para a E4, e a linha final do terminal diz quantos claims foram descartados (`1 descartado`, `2 descartados`).
2. Se nenhum claim for válido, aborta o run com erro terminal, com a quantidade de descartados e o motivo de cada um: um FactSheet vazio não serve para a E4.

O registro do descarte é a linha de cada claim na última rodada em `validacoes_ancora`, com `passou = false` e o motivo da recusa; não há tabela própria. O dashboard conta os descartados de um run por essas linhas.

O caso típico é um número que o modelo cita e a E1 não leu: a ancoragem recusa o claim porque o número não está na `tabela_numeros`, e o modelo insiste.

## Cliente LLM

`llm.ClienteLLM.chamar(no, prompt, schema)` lê o snapshot do nó em `config/models.yaml`, pede ao provider saída JSON no schema Pydantic e devolve o objeto validado, nunca texto. O provider é escolhido por nó: o juiz (`judge`) chama o Groq (seção Groq); os outros nós chamam a Gemini API, pelas chaves da seção Ordem das chaves. Duas contagens separadas, nenhuma ligada ao `K`:

- **Falha de infraestrutura** (HTTP 408, 429, 5xx, timeout, erro de conexão) em todas as chaves: repete com o backoff de `retentativa.py`, até 4 tentativas; depois, erro terminal.
- **Resposta fora do schema:** repete até 3 vezes, acrescentando ao prompt o erro de validação; depois, erro terminal.
- **Conteúdo recusado pelo provider** (bloqueio do prompt ou `finish_reason` de segurança) e demais erros HTTP 4xx: terminal, sem repetir e sem passar para a chave de reserva.

### Ordem das chaves

Cada tentativa chama primeiro a `GOOGLE_API_KEY_PRINC` e, se ela falhar, a `GOOGLE_API_KEY_SEC`, na mesma tentativa e sem esperar:

- **Passa para a SEC:** falha de infraestrutura (HTTP 408, 429, 5xx, timeout, erro de conexão) ou chave recusada (`FalhaChave`: HTTP 401, 403 ou 400 de chave inválida).
- **Não passa para a SEC:** conteúdo recusado, resposta fora do schema e demais erros 4xx, que não dependem da chave.
- **Toda chamada e toda nova tentativa começam pela PRINC**, mesmo que a anterior tenha usado a SEC.
- **As duas falham:** se alguma teve falha de infraestrutura, a tentativa conta para o limite de 4, com o backoff, e a seguinte recomeça pela PRINC; se as duas foram recusadas por chave, o erro é terminal, sem esperar.
- **Sem a SEC** no `.env`, a PRINC é a única chave e uma chave recusada é terminal.

Cada chamada a uma chave vira um span; o span de falha leva o nome da chave no começo da mensagem, e a passagem para a SEC é avisada no terminal.

A retentativa interna do SDK fica desligada, para que cada tentativa passe pelo cliente e vire um span. O timeout por chamada é de 300 s.

## Custo

Cada tentativa, com sucesso ou não, vira um `Span` (`schemas.py`): nó, modelo, tokens de entrada e de saída (os de raciocínio contam como saída), custo, duração e a classe e a mensagem da falha. O custo é uma estimativa: tokens vezes o preço em US$ por milhão de `config/models.yaml`, convertido pela `cotacao_usd_brl` do mesmo arquivo. A cotação é provisória e está marcada com `PENDENTE:` no YAML. Não há teto de orçamento: o custo é registrado, não interrompe o run.

O custo acumulado do run é atualizado na mesma instrução SQL que grava o span, e span repetido não soma de novo. O cliente também acumula o total em memória, e o `__main__.py` o escreve no `manifest.json` local ao concluir ou abortar o run.

Ordem de grandeza nos documentos de referência, medida em 6 runs completos: de R$ 0,17 a R$ 0,52 por documento na extração (E2) e de R$ 0,09 a R$ 0,22 no plano (E4); o run completo, com as 9 células, custa de R$ 1,28 a R$ 2,28 (`README.md`, Trade-offs de custo e latência).

## Saída

| O quê | Supabase | Cópia local em `data/runs/<run_id>/` |
|---|---|---|
| Span de cada tentativa (E2) | `llm_spans`, e o custo somado em `runs` | `spans/<span_id>.json` |
| Resultado de cada rodada (E3) | `validacoes_ancora`: uma linha por claim proposto, com `passou` e `motivo` | `validacoes/<rodada>.json` |
| FactSheet assinado (E3 ou descarte) | `factsheets`, `claims` e `claim_numeros`, numa transação | `factsheet.json`, sem a `tabela_numeros`, que já está em `documento.json` |

Todas as gravações usam `on conflict do nothing`: repetir não duplica linhas. O FactSheet é gravado uma vez, numa transação, e não muda depois de assinado: `carregar_factsheet` lê o FactSheet inteiro ou nenhum, sem precisar de snapshot. Cada `claim_numeros` guarda o `Numero` completo e o `numero_id` da sua entrada em `numeros`, sempre preenchido. O FactSheet só é gravado assinado; `gravar_factsheet` recusa um FactSheet não assinado.

## Como falha

- **Omissão silenciosa:** um fato que não vira claim não existe para o resto do pipeline, e nenhuma etapa detecta. A defesa é o teste de cobertura com os documentos anotados à mão (`docs/testes.md`).
- **Não convergência:** claim que segue reprovado depois de 3 rodadas é descartado e fica fora do FactSheet, com o motivo em `validacoes_ancora`; se nenhum claim passar, o run aborta.
- **Provider fora ou resposta inválida:** retentativas esgotadas nas duas chaves abortam o run com erro terminal.
- **Falha de gravação:** segue a classificação de `docs/fluxos/ingestao.md`; esgotadas as tentativas, aborta o run.
