# Métricas da E6

Este documento descreve as métricas que avaliam uma peça: o que cada uma confere, a regra exata, o que devolve e a instrução corretiva de cada violação. O lugar delas no pipeline, a ordem de execução e o refinement loop estão em `docs/arquiteturas.md` (E6, E7 e O Refinement Loop). Os testes estão em `docs/testes.md`, seção Métricas.

## Conjunto

| Nome | Número | Tipo | Código |
| ---- | ------ | ---- | ------ |
| `numeric_fidelity` | M1 | determinística | `sunontent/metrics/numeric_fidelity.py` |
| `text_size` | M2 | determinística | `sunontent/metrics/text_size.py` |
| `analogy_rule` | M5 | determinística | `sunontent/metrics/analogy_rule.py` |
| `plan_contract` | sem número | determinística | calculada na montagem da E5.3 |
| `llm_judge` | M6 | juiz LLM | ainda não implementada |

O nível é medido pela regra de analogia (M5), pelo tamanho do artigo por nível (M2) e pelo nível percebido pelo juiz (M6). As métricas determinísticas não importam o cliente LLM: rodam sem pipeline, sem banco e sem rede.

Arquivos de apoio em `sunontent/metrics/`:

- `resultado.py`: `montar(detalhes, violacoes, faixa_esperada="0")` devolve o `MetricaResultado` de qualquer métrica;
- `campos.py`: `campos_da_peca(payload)` devolve os textos da peça, um por campo, e `nome_da_unidade(payload)` devolve `seção`, `slide` ou `cena`.

Fora da pasta, as métricas usam `sunontent/texto.py` (contagem de palavras e de caracteres), `sunontent/numeros.py` (leitura de número e de data), `sunontent/glossario.py` (casamento de termos) e `sunontent/levelspec.py` (limites e marcadores do nível).

## Chamadas

```python
numeric_fidelity.avaliar(peca, claims_do_plano, tabela_numeros, data_documento, glossario) -> MetricaResultado
text_size.avaliar(peca, spec) -> MetricaResultado
analogy_rule.avaliar(peca, spec, plano_nivel, glossario) -> MetricaResultado
```

`claims_do_plano` são os claims do `PlanoNivel` da célula, lidos do FactSheet assinado, com os números na ordem de `Claim.numeros`; `tabela_numeros` é a do FactSheet; `data_documento` é a do FactSheet; `spec` é o LevelSpec do nível da célula.

## Resultado, valor e faixa

Toda métrica devolve um `MetricaResultado` (`nome`, `valor`, `faixa_esperada`, `passou`, `violacoes`, `detalhes`). `passou` é verdadeiro se, e somente se, a lista de violações está vazia, e toda violação tem a `metrica` do resultado. O `detalhes` é o modelo da métrica e leva o nome dela em `metrica`, o que permite ler o `jsonb` de volta.

| Métrica | `valor` | `faixa_esperada` |
| ------- | ------- | ---------------- |
| `numeric_fidelity`, `analogy_rule`, `plan_contract` | número de violações | `"0"` |
| `text_size` | número de unidades fora do limite | `"0 unidades fora do limite (<limite do LevelSpec>)"` |
| `llm_judge` | `true` se o juiz não reprova nada, `false` se reprova | `"true"` |

Cada `Violacao` tem `metrica`, `trecho` (o texto exato), `esperado`, `obtido`, `instrucao_corretiva` e a posição: `unidade_indice` (a seção, o slide ou a cena, a partir de 0), `campo` (`titulo`, `linha_fina`, `texto` ou `gancho_visual`; `texto` é o texto da seção, do slide ou a fala da cena), `inicio` e `fim` (o intervalo do trecho no campo, fim exclusivo). A posição é opcional: a violação do total do artigo não tem.

## Contagem de texto

`sunontent/texto.py` conta o texto final da peça, com os marcadores já trocados:

- palavra é a sequência sem espaço que tem pelo menos uma letra ou um dígito: `R$ 1,2 bi` tem 3 palavras, `IPCA-15` tem 1, o travessão solto não conta, e um marcador que ficou no texto vale 1;
- caractere é o do texto sem os espaços das pontas, contando os espaços do meio e a letra acentuada como 1.

## M1. `numeric_fidelity`

Todo número da peça que não seja data vem de um marcador: o seu intervalo toca o de um `NumeroUsado` do mesmo campo e da mesma unidade. O que não fecha reprova a célula, sem opinião de modelo. Número por extenso ("dois por cento") não é lido: fica para o juiz.

### Passos, em cada campo

A varredura roda no título, na linha fina, no texto de cada unidade e no gancho visual de cada cena.

1. **Marcador que sobrou.** A montagem troca os marcadores válidos, então todo `{{...}}` que ficou no texto é inválido (inexistente ou de claim fora do plano). Cada um é uma violação, e o trecho sai da varredura.
2. **Formas do glossário com dígito.** `IPCA-15` e as outras formas do glossário que têm dígito são mascaradas. Sem a máscara, o `15 de outubro` de "IPCA-15 de outubro" seria lido como uma data.
3. **Número em formato que o parser não lê** (`US$ 5`, `1.234.5`). É uma violação; o trecho sai da varredura e o resto do texto continua sendo lido.
4. **Números que não são data.** Cada um com marcador conta em `com_marcador`; cada um sem marcador é uma violação, classificada pelos desfechos abaixo.
5. **Datas**, pela regra da seção seguinte.

O título e o texto das unidades aceitam número por marcador. Na linha fina e no gancho visual nenhum número é aceito: o número é uma violação mesmo que bata com um claim, e a instrução é remover.

### Desfechos do número sem marcador

| Desfecho (campo de `desfechos`) | Quando | Instrução |
| ------------------------------- | ------ | --------- |
| `fora_de_marcador_do_plano` | mesmo valor e unidade de um número de claim do plano do nível | "Use o marcador {{C27.1}} no lugar do número 5,2%." |
| `fora_do_plano_no_documento` | está na `tabela_numeros`, em nenhum claim do plano | "O número 9,9% existe no documento, mas não é fato do plano deste nível: remova-o." |
| `ausente_do_documento` | não está na `tabela_numeros` (alucinação) | "O número 7,77% não existe no documento: remova-o." |
| `marcador_invalido` | marcador que sobrou no texto | "O marcador {{C99.1}} não existe na tabela de marcadores deste nível: use só os marcadores fornecidos ou escreva o texto sem o número." |
| `formato_nao_previsto` | número que o parser não lê | "O trecho «US$ 5» tem um número em formato que não se lê: escreva o texto sem ele ou use o marcador do número." |

Valor e unidade se comparam juntos: `5,2%` e `5,2 p.p.` são números diferentes. O marcador sugerido é o do primeiro claim que tem o número, com preferência para os claims que a peça usa (`Peca.claim_ids`); a `ordem` é a posição do número na lista do claim. Na linha fina e no gancho visual a instrução é "Remova o número 13,75% da linha fina: número só vale no título e no texto, por marcador."

### Datas

`datas_conferidas` conta as datas e os componentes de data conferidos.

- **Data com dia** (`19/10`, `19 de outubro`, `15 e 16 de setembro de 2026`): vale se algum claim do plano tem o mesmo dia e mês e, quando a peça escreve o ano, o mesmo ano. Se o claim não traz o ano, a peça só pode dar o ano da `data_documento`.
- **Ano, trimestre e número de reunião** (o que sobra de data depois de tirar as datas com dia): vale por valor. O valor precisa aparecer como componente de data em algum claim do plano, ou ser o ano da `data_documento` (inteiro ou de dois dígitos).

A regra por valor é leve de propósito: `2T26` passa quando algum claim tem o componente `2` e o documento é de 2026, mesmo que nenhum claim tenha o trimestre inteiro. Ela evita o falso positivo, que é o pior defeito da M1, ao custo de deixar passar um trimestre trocado por outro quando os dois números aparecem nos claims.

Instruções: "A data «20/10» não está nos claims do plano: remova-a ou escreva a data como o claim a traz." e "«2028» não aparece nos claims do plano nem é o ano do documento: remova-o."

### Detalhes

`NumericFidelityDetalhes`: `numeros_varridos` (números que não são data), `com_marcador`, `datas_conferidas` e `desfechos` (a contagem de cada linha da tabela acima, mais `data_sem_lastro`).

## M2. `text_size`

Aritmética sobre o payload, com o contrato de formato do LevelSpec e a contagem de `sunontent/texto.py`. O limite registrado é o `faixa_esperada` de `levelspec.instrucoes_redator`, a mesma função que escreve o orçamento do prompt do redator.

| Formato | Unidade medida | Limite |
| ------- | -------------- | ------ |
| Artigo | o total de palavras do texto das seções (o título e a linha fina ficam de fora) | `formatos.artigo.palavras`, de `min` a `max` |
| Carrossel | os caracteres de cada slide | `formatos.carrossel.caracteres` do papel (gancho, corpo ou conclusão), no máximo |
| Roteiro | as palavras da fala de cada cena, e o total | duração da cena vezes `palavras_por_segundo`, e `duracao_s` vezes `palavras_por_segundo` no total, ambos arredondados para baixo |

Cada unidade fora do limite é uma violação, e o `valor` é a contagem delas. A quantidade de slides e a soma das cenas até `duracao_s` não são da M2: são do `plan_contract`.

| Violação | Instrução |
| -------- | --------- |
| artigo curto | "Acrescente texto às seções: de 100 para pelo menos 350 palavras, sem mudar os números nem os claims." |
| artigo longo | "Reduza o texto das seções: de 600 para no máximo 550 palavras, sem mudar os números nem os claims." |
| slide | "Reduza o slide 1 (gancho) de 121 para no máximo 120 caracteres, sem mudar os números." |
| cena | "Reduza a fala da cena 1 de 16 para no máximo 15 palavras, que cabem em 6 s ao falar." |
| total do roteiro | "Reduza o roteiro de 180 para no máximo 150 palavras no total, cortando as falas mais longas." |

`TextSizeDetalhes`: `grandeza` (`palavras` ou `caracteres`), `limite` (o texto renderizado) e `unidades`, uma `MedidaUnidade` por unidade medida (`unidade_indice`, `papel`, `medida`, `limite_min`, `limite_max` e `dentro`). No artigo há uma só, a do total; no roteiro, a última é o total, sem `unidade_indice`.

## M5. `analogy_rule`

A regra vale só no nível Iniciante. Nos outros, o resultado é `aplicavel = False`, sem termos e sem violações.

No Iniciante, para cada termo do glossário com `nivel_minimo` acima de iniciante encontrado no texto das unidades:

1. o termo é achado pela regra de casamento de `docs/glossario.md`, e só no texto das unidades (seção, slide ou fala da cena); o título, a linha fina e o gancho visual não são procurados;
2. conta a primeira ocorrência do termo, na primeira unidade em que ele aparece;
3. o plano do nível precisa ter uma `Analogia` para o termo: a do glossário traz a referência `<id>#<n>`, e a `nova` é ligada ao termo pela forma escrita;
4. a mesma unidade precisa ter um marcador de analogia, uma das expressões de `analogia.marcadores` do LevelSpec do Iniciante. A busca ignora maiúsculas e acentos, exige fronteira de palavra e não tem janela: a expressão em qualquer ponto da unidade vale para todos os termos dela.

| Violação | Instrução |
| -------- | --------- |
| termo sem `Analogia` no plano | "O termo «CDI» aparece na seção 1 sem analogia no plano: substitua-o por uma explicação em palavras simples." |
| termo com `Analogia` e sem marcador na unidade | "Explique «Selic» na seção 1, na primeira vez que o usar, com a analogia do plano (o preço do dinheiro), usando uma destas expressões: "é como", "funciona como", "imagine que", "na prática"." |

A unidade é nomeada `seção`, `slide` ou `cena` conforme o formato, contada a partir de 1 no texto da instrução. Um termo gera no máximo uma violação.

`AnalogyRuleDetalhes`: `aplicavel` e `termos`, uma `TermoAnalogia` por termo encontrado (`termo`, `glossario_id`, `unidade_indice`, `analogia_no_plano` e `marcador_na_unidade`).

## `plan_contract`

As regras de estrutura que dependem do LevelSpec ou do plano. A montagem da E5.3 as calcula e monta o resultado com `resultado.montar(PlanContractDetalhes(...), violacoes)`:

| Checagem (`nome`) | Confere |
| ----------------- | ------- |
| `quantidade_de_slides` | a quantidade de slides do carrossel dentro de `slides.min` e `slides.max` |
| `soma_das_cenas` | o fim da última cena até `duracao_s` |
| `claim_ids_no_plano` | os `claim_ids` de cada unidade dentro do plano do nível |
| `cobertura_do_formato` | todo claim do formato aparece em alguma unidade |

As regras que valem para qualquer célula (um gancho no início e uma conclusão no fim, cenas contíguas a partir de 0 s, `claim_ids` não vazio onde exigido, nenhum marcador na linha fina nem no gancho visual) são validadores do schema do redator, em `sunontent/schemas.py`. A resposta que as quebra volta ao modelo sem consumir o `K`; uma violação do `plan_contract` consome.

`PlanContractDetalhes`: `checagens`, cada uma com `nome`, `esperado`, `obtido` e `passou`.

## M6. `llm_judge`

O juiz roda uma vez por célula, só depois de M1, M2, M5 e `plan_contract` passarem, e só pode reprovar. Responde se cada `Analogia` explica o conceito corretamente e se cada unidade é implicada pelos claims que cita, e devolve o `nivel_percebido` da peça. O `LlmJudgeDetalhes` já existe em `sunontent/schemas.py`: `analogias` (termo, justificativa e `correta`), `unidades` (índice, justificativa, `implicada` e trecho problemático) e `nivel_percebido`. O desenho está em `docs/arquiteturas.md`, E6.

## Limites conhecidos

- O número por extenso não é lido pela M1.
- A data é conferida por valor para ano, trimestre e reunião (regra leve da seção Datas).
- O parser lê todo número solto entre 1900 e 2099 como ano; uma contagem nessa faixa escrita à mão sai como data sem lastro. A pendência está em `pendentes/pendencias-futuras.md`.
