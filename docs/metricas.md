# Métricas da E6

Este documento descreve as métricas que avaliam uma peça: o que cada uma confere, a regra exata, o que devolve e a instrução corretiva de cada violação. O lugar delas no pipeline, a ordem de execução e o refinement loop estão em `docs/arquiteturas.md` (E6, E7 e O Refinement Loop). Os testes estão em `docs/testes.md`, seção Métricas.

## Conjunto

| Nome | Número | Tipo | Código |
| ---- | ------ | ---- | ------ |
| `numeric_fidelity` | M1 | determinística | `sunontent/metrics/numeric_fidelity.py` |
| `text_size` | M2 | determinística | `sunontent/metrics/text_size.py` |
| `readability` | M3 | determinística | `sunontent/metrics/readability.py` |
| `term_density` | M4 | determinística, reprova só o Iniciante | `sunontent/metrics/term_density.py` |
| `analogy_rule` | M5 | determinística | `sunontent/metrics/analogy_rule.py` |
| `llm_judge` | M6 | juiz LLM | `sunontent/metrics/llm_judge.py` |
| `plan_contract` | M7 | determinística | calculada na montagem da E5.3 |

O nível é medido pela regra de analogia (M5), pelo tamanho do artigo por nível (M2), pela legibilidade (M3), pela densidade de termos (M4, com teto no Iniciante) e pelo nível percebido pelo juiz (M6). A M7 (`plan_contract`) confere a estrutura da peça contra o plano. As métricas determinísticas não importam o cliente LLM: rodam sem pipeline, sem banco e sem rede. Só o juiz (`llm_judge.py`) chama o modelo, pelo `llm.py`.

Arquivos de apoio em `sunontent/metrics/`:

- `resultado.py`: `montar(detalhes, violacoes, faixa_esperada="0", valor=None)` devolve o `MetricaResultado` de qualquer métrica; o `valor` só é passado pelas métricas que medem uma grandeza (M3 e M4), e as outras valem o número de violações;
- `campos.py`: `campos_da_peca(payload)` devolve os textos da peça, um por campo, e `nome_da_unidade(payload)` devolve `seção`, `slide` ou `cena`;
- `contexto.py`: a recuperação de contexto do juiz, com o BM25 simples (seção M6);
- `matriz_niveis.py`: as matrizes de confusão de níveis, a do juiz e a estatística (o artigo pela faixa de IFL, o carrossel e o roteiro pelo estilo de Iniciante), e o comando que as imprime (seção M6, Matriz de confusão de níveis).

Fora da pasta, as métricas usam `sunontent/texto.py` (contagem de palavras, de caracteres, de sílabas e de frases), `sunontent/numeros.py` (leitura de número e de data), `sunontent/glossario.py` (casamento de termos) e `sunontent/levelspec.py` (limites e marcadores do nível).

## Chamadas

```python
numeric_fidelity.avaliar(peca, claims_do_plano, tabela_numeros, data_documento, glossario) -> MetricaResultado
text_size.avaliar(peca, spec) -> MetricaResultado
readability.avaliar(peca, spec, spec_iniciante) -> MetricaResultado
term_density.avaliar(peca, glossario, spec) -> MetricaResultado
analogy_rule.avaliar(peca, spec, plano_nivel, glossario) -> MetricaResultado
```

`claims_do_plano` são os claims do `PlanoNivel` da célula, lidos do FactSheet assinado, com os números na ordem de `Claim.numeros`; `tabela_numeros` é a do FactSheet; `data_documento` é a do FactSheet; `spec` é o LevelSpec do nível da célula e `spec_iniciante` é o do Iniciante (a M3 mede de que lado do piso dele a peça cai).

## Resultado, valor e faixa

Toda métrica devolve um `MetricaResultado` (`nome`, `valor`, `faixa_esperada`, `passou`, `violacoes`, `detalhes`). `passou` é verdadeiro se, e somente se, a lista de violações está vazia, e toda violação tem a `metrica` do resultado. O `detalhes` é o modelo da métrica e leva o nome dela em `metrica`, o que permite ler o `jsonb` de volta.

| Métrica | `valor` | `faixa_esperada` |
| ------- | ------- | ---------------- |
| `numeric_fidelity`, `analogy_rule`, `plan_contract` | número de violações | `"0"` |
| `text_size` | número de unidades fora do limite | `"0 unidades fora do limite (<limite do LevelSpec>)"` |
| `readability` | o IFL da peça | `"IFL de pelo menos <piso>"`, ou `"sem limite (só mede)"` no nível sem piso |
| `term_density` | a densidade, por 100 palavras | `"no máximo <teto> ocorrências por 100 palavras"`, ou `"sem limite (só mede)"` no nível sem teto |
| `llm_judge` | `true` se o juiz não reprova nada, `false` se reprova | `"true"` |

Cada `Violacao` tem `metrica`, `trecho` (o texto exato), `esperado`, `obtido`, `instrucao_corretiva` e a posição: `unidade_indice` (a seção, o slide ou a cena, a partir de 0), `campo` (`titulo`, `linha_fina`, `texto` ou `gancho_visual`; `texto` é o texto da seção, do slide ou a fala da cena), `inicio` e `fim` (o intervalo do trecho no campo, fim exclusivo). A posição é opcional: a violação do total do artigo não tem.

## Contagem de texto

`sunontent/texto.py` conta o texto final da peça, com os marcadores já trocados:

- palavra é a sequência sem espaço que tem pelo menos uma letra ou um dígito: `R$ 1,2 bi` tem 3 palavras, `IPCA-15` tem 1, o travessão solto não conta, e um marcador que ficou no texto vale 1;
- caractere é o do texto sem os espaços das pontas, contando os espaços do meio e a letra acentuada como 1.

A M3 lê o texto de outro modo, com as funções `palavras_de_leitura`, `contar_silabas` e `segmentar_frases`:

- palavra de leitura é o token com letra, sem dígito e sem a pontuação das pontas; número e marcador de número ficam de fora, porque vêm dos claims;
- sílaba é contada por regra: cada grupo de vogais vale uma, com hiato entre duas vogais fortes seguidas (`a`, `e`, `o`) e entre uma vogal e `í` ou `ú`, o `u` mudo de `que`, `qui`, `gue` e `gui` fora da conta, e o ditongo crescente (`série`, `história`) como uma sílaba; a palavra com hífen soma as partes e a sigla sem vogal vale uma;
- frase termina em `.`, `!`, `?` ou `…` seguido de espaço ou do fim do texto, e a seguinte começa em maiúscula, dígito ou abertura; não quebra em abreviação (`p.p.`, `S.A.`), em inicial de nome nem antes de letra minúscula, e `;` e `:` não quebram. Trecho sem letra nem dígito não é frase.

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

Cada unidade fora do limite é uma violação, e o `valor` é a contagem delas. A quantidade de slides e a soma das cenas até `duracao_s` não são da M2: são do M7 (`plan_contract`).

| Violação | Instrução |
| -------- | --------- |
| artigo curto | "Acrescente texto às seções: de 100 para pelo menos 350 palavras, sem mudar os números nem os claims." |
| artigo longo | "Reduza o texto das seções: de 600 para no máximo 550 palavras, sem mudar os números nem os claims." |
| slide | "Reduza o slide 1 (gancho) de 121 para no máximo 120 caracteres, sem mudar os números." |
| cena | "Reduza a fala da cena 1 de 16 para no máximo 15 palavras, que cabem em 6 s ao falar." |
| total do roteiro | "Reduza o roteiro de 180 para no máximo 150 palavras no total, cortando as falas mais longas." |

`TextSizeDetalhes`: `grandeza` (`palavras` ou `caracteres`), `limite` (o texto renderizado) e `unidades`, uma `MedidaUnidade` por unidade medida (`unidade_indice`, `papel`, `medida`, `limite_min`, `limite_max` e `dentro`). No artigo há uma só, a do total; no roteiro, a última é o total, sem `unidade_indice`.

## M3. `readability`

Índice de Facilidade de Leitura de Flesch adaptado ao português (Martins et al., 1996), sobre o texto das unidades da peça (seção, slide ou fala da cena). O título, a linha fina e o gancho visual não entram, como na M2 e na M5.

```
IFL = 248,835 - 1,015 x (palavras / frases) - 84,6 x (sílabas / palavras)
```

Palavras, sílabas e frases são as de leitura (seção Contagem de texto), somadas em todas as unidades; a frase que fica sem palavra de leitura não conta. O fim de uma unidade fecha a frase. O `valor` é o IFL, com uma casa decimal. Quanto maior, mais fácil.

### Piso e meta de frase

O LevelSpec pode ter o bloco `legibilidade`, hoje só no Iniciante:

```yaml
legibilidade:
  ifl_minimo: {artigo: 30, carrossel: 45, roteiro: 45}
  frase_max_palavras: 18
```

- `ifl_minimo` é o piso por formato: abaixo dele a métrica reprova. O nível sem o bloco só mede, com `faixa_esperada` `"sem limite (só mede)"`;
- `frase_max_palavras` é a meta de tamanho de frase. Não reprova nada: `instrucoes_redator` a escreve no prompt do redator ("Escreva frases de até 18 palavras e prefira palavras curtas e comuns"), e a instrução corretiva a cita.

### Sinal de nível do artigo

Os três níveis têm o bloco `sinal_ifl`, a faixa de IFL em que o artigo do nível cai (`min` inclusivo e `max` exclusivo):

```yaml
sinal_ifl:
  artigo: {min: 45}              # Iniciante
  artigo: {min: 27, max: 45}     # Intermediário
  artigo: {max: 27}              # Avançado
```

As faixas dos três níveis se encaixam, sem lacuna nem sobreposição, e o `carregar_todos` recusa o conjunto que não se encaixa. O sinal é **só informativo**: alimenta a matriz de confusão do artigo e nunca reprova. Na M3, o único critério que reprova é o piso de IFL do Iniciante (`ifl_minimo`); o Intermediário e o Avançado só medem, mesmo quando o IFL do artigo cai na faixa de outro nível.

### Violação

Uma violação por frase, até 3, quando o IFL fica abaixo do piso: as frases mais longas entre as que passam da meta, ou só a mais longa se nenhuma passa. O `trecho` é a frase, com a posição (unidade, `campo` `texto`, intervalo na unidade).

| Violação | Instrução |
| -------- | --------- |
| IFL abaixo do piso | "O índice de legibilidade da peça é 33,8 e o mínimo do nível é 45: reescreva a frase no slide 2 (31 palavras) em frases mais curtas de até 18 palavras e troque as palavras longas por equivalentes simples, sem mudar os números nem os claims." |

### Detalhes

`ReadabilityDetalhes`: `palavras`, `silabas`, `frases`, `ifl`, `ifl_minimo` e `frase_max_palavras` (os do nível, quando existem), `analogia_na_peca` e `nivel_estatistico` (o sinal de nível).

`analogia_na_peca` é verdadeiro quando o texto de alguma unidade tem um marcador de analogia do Iniciante (`analogia.marcadores`, buscado como na M5).

`nivel_estatistico` é o sinal de nível da peça, só informativo. No artigo, é o nível cuja faixa de `sinal_ifl` contém o IFL (`iniciante`, `intermediario` ou `avancado`), sem olhar a analogia. No carrossel e no roteiro, é o estilo de Iniciante: `iniciante` quando o IFL é igual ou maior que o piso do Iniciante no formato e `analogia_na_peca` é verdadeiro, `acima_de_iniciante` nos outros casos; o IFL sozinho confunde frase curta com texto de Iniciante (o limite de caracteres do slide encurta as frases do carrossel de qualquer nível), e a analogia é o que o Iniciante tem e os outros níveis não têm. Alimenta a matriz de confusão estatística (M6, Matriz de confusão de níveis). É `None` quando os níveis não têm `sinal_ifl` (artigo) ou quando o Iniciante não tem piso no formato ou marcadores (carrossel e roteiro).

### O que a medida mostra

No artigo, o IFL separa os três níveis: nos runs medidos (Copom e CVM) o Iniciante ficou em 52 a 55, o Intermediário em 34 a 39 e o Avançado entre -6 e 19,5, e as faixas de `sinal_ifl` (45 e 27) caem nas lacunas entre esses grupos. Como são poucas fontes, as faixas são um sinal e não reprovam. No carrossel e no roteiro as faixas de IFL se encostam, porque o limite de caracteres encurta as frases de qualquer nível; ali o IFL só separa o Iniciante dos outros dois, junto da analogia. Por isso só o Iniciante tem piso.

## M4. `term_density`

Densidade de termos do glossário no texto das unidades da peça (seção, slide ou fala da cena). Mede em todo nível e reprova só no Iniciante, acima do teto do formato.

- Conta as ocorrências dos termos com `nivel_minimo` acima de iniciante, casados pela regra da M5 (`docs/glossario.md`, Casamento no texto). No Iniciante são os mesmos termos que a M5 governa: a M5 exige a analogia na primeira ocorrência, e a M4 limita quantas ocorrências a peça tem ao todo.
- O `valor` é a densidade: ocorrências por 100 palavras (a contagem de palavras da M2), com uma casa decimal.
- O resultado traz também a densidade só dos termos de `nivel_minimo` avancado.

### Teto e piso

O LevelSpec do Iniciante tem o bloco `densidade`, o teto por formato em ocorrências por 100 palavras:

```yaml
densidade:
  termos_max_por_100: {artigo: 5.5, carrossel: 6.5, roteiro: 6.5}
```

- O teto vale para todas as ocorrências, não só as repetições. O Intermediário e o Avançado não têm o bloco (o `carregar` o recusa neles) e só medem, com `faixa_esperada` `"sem limite (só mede)"`.
- O limite em ocorrências é o maior entre `floor(teto x palavras / 100)` e o número de unidades da peça: uma ocorrência por unidade está sempre dentro do limite, para o teto nunca ficar abaixo de um termo por unidade. A peça reprova quando as ocorrências passam desse limite.
- `instrucoes_redator` escreve o teto do formato no prompt do redator, já na primeira tentativa ("Use poucos termos técnicos: no máximo 5,5 ocorrências de termos do glossário ... a cada 100 palavras").

### Violação

Uma violação por ocorrência a cortar, até 5, em quantidade igual ao excesso sobre o limite (ocorrências da peça menos o limite). A ordem das ocorrências escolhidas é: as repetições, da última para a primeira, e só depois a primeira ocorrência de cada termo, que carrega a analogia da M5. O `trecho` é a ocorrência, com a posição (unidade, `campo` `texto`, intervalo na unidade).

| Violação | Instrução |
| -------- | --------- |
| repetição de termo | "A densidade de termos da peça é 7,3 por 100 palavras e o máximo do nível é 5,5 (corte pelo menos 3 ocorrências no total): troque «Selic» no slide 3 por palavras simples: o termo já foi explicado antes. Termo: Selic." |
| primeira ocorrência | "...: evite o termo «CDI» no slide 1: escreva a ideia em palavras simples, sem o termo (e sem a analogia dele, que deixa de ser necessária). Termo: CDI." |

### Detalhes

`TermDensityDetalhes`: `palavras`, `ocorrencias`, `densidade`, `ocorrencias_avancado`, `densidade_avancado`, `termos`, uma `TermoContado` por termo encontrado (`glossario_id`, `termo`, `nivel_minimo` e `ocorrencias`), e `densidade_maxima` e `ocorrencias_maximas` (o teto do formato e o limite em ocorrências para o tamanho da peça, `None` no nível sem teto).

A densidade varia mais entre as fontes que entre os níveis (a ata do Copom tem muito mais termos que o release da B3). Por isso o teto tem folga acima do mínimo viável do Iniciante, que é o número de termos distintos da peça.

## M5. `analogy_rule`

A regra vale no Iniciante e no Intermediário, os níveis com o bloco `analogia` no LevelSpec. No Avançado, o resultado é `aplicavel = False`, sem termos e sem violações.

Para cada termo do glossário com `nivel_minimo` acima do nível (acima de iniciante no Iniciante, acima de intermediario no Intermediário) encontrado no texto das unidades:

1. o termo é achado pela regra de casamento de `docs/glossario.md`, e só no texto das unidades (seção, slide ou fala da cena); o título, a linha fina e o gancho visual não são procurados;
2. conta a primeira ocorrência do termo, na primeira unidade em que ele aparece;
3. o plano do nível precisa ter uma `Analogia` para o termo: a do glossário traz a referência `<id>#<n>`, a `nova` é ligada ao termo pela forma escrita e, no Intermediário, a explicação (a `definicao_simples`) é ligada pela forma escrita;
4. a mesma unidade precisa ter um marcador de analogia, uma das expressões de `analogia.marcadores` do LevelSpec do nível (no Intermediário, "ou seja", "isto é", "quer dizer", "em outras palavras"). A busca ignora maiúsculas e acentos, exige fronteira de palavra e não tem janela: a expressão em qualquer ponto da unidade vale para todos os termos dela.

| Violação | Instrução |
| -------- | --------- |
| termo sem `Analogia` no plano | "O termo «CDI» aparece na seção 1 sem analogia no plano: substitua-o por uma explicação em palavras simples." |
| termo com `Analogia` e sem marcador na unidade | "Explique «Selic» na seção 1, na primeira vez que o usar, com a analogia do plano (o preço do dinheiro), usando uma destas expressões: "é como", "funciona como", "imagine que", "na prática"." |

No Intermediário, as mensagens trocam "analogia" por "explicação" ("O termo «hiato do produto» aparece na seção 1 sem explicação no plano" e "Explique «hiato do produto» na seção 1, na primeira vez que o usar, com a explicação do plano (...), usando uma destas expressões: "ou seja", "isto é", "quer dizer", "em outras palavras".").

A unidade é nomeada `seção`, `slide` ou `cena` conforme o formato, contada a partir de 1 no texto da instrução. Um termo gera no máximo uma violação.

`AnalogyRuleDetalhes`: `aplicavel` e `termos`, uma `TermoAnalogia` por termo encontrado (`termo`, `glossario_id`, `unidade_indice`, `analogia_no_plano` e `marcador_na_unidade`).

## M6. `llm_judge`

Código: `sunontent/metrics/llm_judge.py` (prompt em `prompts/judge.j2`, recuperação de contexto em `sunontent/metrics/contexto.py`). Modelo `openai/gpt-oss-120b` pelo Groq (`docs/fluxos/extracao.md`, Cliente LLM), de família diferente do extractor.

O juiz roda uma vez por tentativa da célula, só depois de M1, M2, M3, M5 e M7 (`plan_contract`) passarem, e só pode reprovar: não existe caminho em que ele aprove o que uma determinística reprovou. O `juiz` chega à E6 por `config["configurable"]`, montado por `llm_judge.criar_juiz(llm, raiz)` no `__main__.py`, e é chamado como `juiz(peca, plano_nivel, factsheet, chunks, glossario, run_id=..., registrar_span=...)`.

### O que o juiz recebe

- **Claims:** os citados pelas unidades, com o tipo, o texto e o `literal_fonte`; o prompt avisa que projeção, condicionante e risco só aparecem atribuídos à fonte.
- **Trechos do documento (`contexto.montar`):** para cada unidade, o chunk da âncora de cada claim citado (o que contém o início da âncora), o anterior e o seguinte, e os 3 chunks de maior sobreposição lexical com o texto da unidade entre os que ainda não entraram, por BM25 simples (k1 1,5, b 0,75, idf `ln(1 + (N - df + 0,5) / (df + 0,5))`), sobre palavras sem maiúscula, sem acento, sem as de uma letra e sem uma lista curta de palavras vazias; só chunk com nota acima de zero entra. Cada chunk vai uma vez ao prompt, na ordem do documento, e cada unidade cita os seus pelo `chunk_id`. Sem embedding.
- **Unidades:** o texto de cada seção, slide ou cena (no roteiro, a fala e o gancho visual), com o papel do slide ou a abertura e o fechamento do roteiro, e os claims que cita. A unidade sem `claim_ids` (gancho, conclusão, abertura e fechamento) é julgada contra todos os claims da peça. O título e a linha fina do artigo vão como contexto e não são julgados.
- **Analogias:** só no Iniciante (as explicações do Intermediário vêm da `definicao_simples` e a M5 as confere), as do plano do nível cujo termo aparece no texto das unidades (ligadas ao termo do glossário como na M5), com a explicação do plano, a `definicao_tecnica` do glossário e a unidade da primeira ocorrência.
- **Nível:** o `foco` de cada nível, lido do LevelSpec, e os termos do glossário encontrados em qualquer campo da peça, com o `nivel_minimo` de cada um, como evidência para o nível percebido.

O nível pedido não vai ao prompt, para não influenciar o `nivel_percebido`.

### As perguntas e a resposta

1. Cada analogia explica o conceito corretamente, ou é enganosa ou decorativa?
2. Cada unidade é implicada pelos claims que cita? Não é quando apresenta projeção, condicionante ou risco como fato consumado ou sem atribuição, cria causa, consequência ou comparação que os claims não dizem, acrescenta fato, tendência ou recomendação, escreve por extenso um número diferente do claim (ou troca grandeza, período ou sujeito) ou contradiz um dos trechos. Explicação geral de conceito, analogia, pergunta retórica, convite à leitura e transição não reprovam.
3. Para qual nível a peça parece escrita? O juiz examina a peça por quatro critérios, nesta ordem: vocabulário (termos, siglas e métricas, e o nível de cada um pela lista do glossário), explicação (se a peça explica os conceitos ou os usa como conhecidos), pressuposto (quanto conhecimento de mercado o leitor precisa ter) e foco (comparado ao foco de cada nível). O tamanho do texto e as frases curtas não contam como sinal de nível, e a lista do glossário é evidência, não regra. Uma explicação curta junto do termo (aposto, parênteses, "ou seja" ou "isto é") conta como explicação, e a lista do glossário não vale para o termo que a peça explica. A explicação separa os níveis pelo que é explicado: a peça que usa sem explicar o jargão de especialista tende ao avançado; a que usa sem explicar o vocabulário padrão de quem investe e explica em poucas palavras só o jargão de especialista tende ao intermediário; a que explica também o vocabulário padrão, com analogia ou palavras simples, tende ao iniciante.

A resposta segue o schema `SaidaJuiz` (`sunontent/schemas.py`): `analogias` (`termo`, `justificativa` e `correta`), `unidades` (`unidade_indice`, `justificativa`, `implicada` e `trecho_problematico`), `justificativa_nivel` e `nivel_percebido`, com a justificativa antes do booleano e antes do nível.

### Completude

`conferir_completude` exige uma resposta para cada unidade (índices de 0 ao total menos 1, sem repetir e sem índice inexistente) e para cada analogia pedida (termo comparado sem maiúscula e sem acento, sem repetir e sem termo não pedido). Resposta incompleta é `FalhaTerminal`, e a E6 grava o scorecard com veredito `ERRO_INFRA`. Falha esgotada do juiz (infraestrutura, chave, crédito, bloqueio ou resposta fora do schema) também é `ERRO_INFRA`, nunca aprovação.

### Valor, violações e detalhes

`llm_judge` vale `true` quando o juiz não reprova nada e `false` quando reprova, com faixa `"true"`. Cada `false` vira uma `Violacao`, ordenada pela unidade:

| Caso | `trecho` | Posição | `instrucao_corretiva` |
| ---- | -------- | ------- | --------------------- |
| analogia incorreta | o termo como está escrito na unidade | a primeira ocorrência do termo (`campo` `texto`) | reescrever a analogia daquela unidade de modo que respeite a `definicao_tecnica` do glossário, com a justificativa do juiz |
| unidade não implicada | o `trecho_problematico`, quando ele está na unidade; senão, o trecho dado ou o texto da unidade | o intervalo do trecho no texto da unidade ou no gancho visual da cena; sem intervalo quando o trecho não está na unidade | reescrever a unidade dizendo só o que os claims citados afirmam, sem causa, consequência ou fato novo e com a atribuição nas projeções, condicionantes e riscos, com a justificativa do juiz |

`esperado` diz o que a unidade ou a analogia deveria cumprir, e `obtido` traz a justificativa do juiz. `LlmJudgeDetalhes`: `analogias` (na ordem das analogias pedidas), `unidades` (na ordem dos índices), `justificativa_nivel` e `nivel_percebido`.

### Matriz de confusão de níveis

São duas matrizes, as duas só informativas. `sunontent/metrics/matriz_niveis.py` as monta e as formata, por formato:

- **A do juiz** cruza o nível pedido e o `nivel_percebido` (`montar`, `acerto` e `formatar`). Lê a última tentativa julgada de cada célula, pela `ConsultaSupabase.niveis_percebidos`.
- **A estatística** cruza o nível pedido e o `nivel_estatistico` da M3, o sinal de nível da peça (`montar_estatistica`, `acerto_estatistico` e `formatar_estatistica`). No artigo, as colunas são os três níveis (a faixa de IFL em que o IFL cai) e conta como acerto o sinal igual ao nível pedido. No carrossel e no roteiro, as colunas são `iniciante` e `acima_de_iniciante` (IFL no piso do Iniciante e analogia na peça) e conta como acerto o Iniciante em `iniciante` e os outros dois níveis em `acima_de_iniciante`. É só informativa e não reprova nada. Lê a última tentativa de cada célula, pela `ConsultaSupabase.niveis_estatisticos`, que traz também o `ifl`; o sinal do artigo é calculado desse `ifl` pelas faixas atuais do LevelSpec (`sinal_da_linha`), e o `nivel_estatistico` gravado vale quando não há `ifl`. Como a M3 roda em toda tentativa, a matriz não depende de o juiz ter rodado.

O comando lê o Supabase (só leitura) e imprime as duas:

```
python -m sunontent.metrics.matriz_niveis [run_id ...]
```

Sem `run_id`, usa todos os runs. A calibração do juiz imprime a matriz do juiz para as suas peças (`docs/testes.md`, Juiz). O dashboard mostra as duas na seção de avaliação por célula, calculadas da última tentativa de cada célula do run aberto (`docs/fluxos/dashboard.md`).

### Calibração

`tests/fixtures/juiz/calibracao.yaml` tem 20 peças escritas sobre os claims das três referências, cobrindo os 3 níveis e os 3 formatos: 10 fiéis aos claims e 10 com um erro plantado numa unidade (analogia errada, causa inventada, projeção como fato e número por extenso trocado). `pytest -m judge` julga as 20 com o Groq de verdade e exige acerto de pelo menos 80% em cada grupo: peças fiéis aprovadas e peças com erro reprovadas.

### Groq

O juiz chama `https://api.groq.com/openai/v1/chat/completions` (API compatível com a da OpenAI) por `httpx`, sem SDK, com `Authorization: Bearer <GROQ_API_KEY>`. A `GROQ_API_KEY` é obrigatória (conferida pelo `__main__.py` antes de o run ser criado) e não tem reserva. O pedido leva:

- `response_format` com `json_schema` e `strict: true`; o schema Pydantic vai no formato do modo estrito (`llm.schema_estrito`: todas as propriedades em `required`, `additionalProperties` falso e sem `default`);
- `max_completion_tokens` 8.192, dentro do qual conta o raciocínio;
- `include_reasoning: false`, para o raciocínio não voltar na resposta;
- `reasoning_effort` (`low`, `medium` ou `high`) quando a chamada pede um nível de raciocínio (o juiz usa a chave `judge` de `config/models.yaml`).

Classificação das respostas:

| Resposta | Classe |
| -------- | ------ |
| HTTP 408, 422, 429, 498 (tier flex sem capacidade) e 5xx, timeout e erro de conexão | falha de infraestrutura, com o backoff |
| HTTP 401 | chave recusada; sem reserva, terminal |
| HTTP 400 com `code` `json_validate_failed` | resposta fora do schema: o `failed_generation` volta ao cliente, que repete com o erro de validação |
| HTTP 403, 404, 413 (pedido acima do limite de tokens por minuto da organização) e os demais 4xx | terminal, com a mensagem do Groq |
| `finish_reason` `content_filter` | conteúdo recusado, terminal |
| `finish_reason` `length` com o conteúdo vazio (o raciocínio esgotou a saída) | resposta fora do schema, repetida com o erro de validação |

Os tokens de raciocínio já vêm dentro de `completion_tokens` (com o detalhe em `completion_tokens_details.reasoning_tokens`), que é o que o span registra como saída. O Groq limita tokens e requisições por minuto e por dia por organização e por modelo; os limites da conta aparecem nos cabeçalhos `x-ratelimit-*` de cada resposta. Um pedido maior que o limite de tokens por minuto volta com 413 e não é repetido.

Nível de raciocínio: a chamada pode pedir uma chave de `raciocinio` em `config/models.yaml` (`minimal`, `low`, `medium` ou `high`). Chave ausente usa o padrão do modelo. A extração usa o padrão; a correção usa `extractor_correcao`, porque corrigir um claim com o motivo dado é uma tarefa menor que ler o documento inteiro, e o raciocínio conta como saída, o token mais caro.

## M7. `plan_contract`

As regras de estrutura que dependem do LevelSpec ou do plano. A montagem da E5.3 as calcula e monta o resultado com `resultado.montar(PlanContractDetalhes(...), violacoes)`:

| Checagem (`nome`) | Confere |
| ----------------- | ------- |
| `quantidade_de_slides` | a quantidade de slides do carrossel dentro de `slides.min` e `slides.max` |
| `soma_das_cenas` | o fim da última cena até `duracao_s` |
| `claim_ids_no_plano` | os `claim_ids` de cada unidade dentro do plano do nível |
| `cobertura_do_formato` | todo claim do formato aparece em alguma unidade |

As regras que valem para qualquer célula (um gancho no início e uma conclusão no fim, cenas contíguas a partir de 0 s, `claim_ids` não vazio onde exigido, nenhum marcador na linha fina nem no gancho visual) são validadores do schema do redator, em `sunontent/schemas.py`. A resposta que as quebra volta ao modelo sem consumir o `K`; uma violação do M7 (`plan_contract`) consome.

`PlanContractDetalhes`: `checagens`, cada uma com `nome`, `esperado`, `obtido` e `passou`.

## Limites conhecidos

- O contador de sílabas é por regra e não é exato em todos os casos: o ditongo crescente conta uma sílaba (`história`, `ordinárias`), a sigla vale uma sílaba por mais que se leia soletrada, e o ditongo e o hiato de palavras raras podem sair trocados. No IFL de um texto inteiro o erro é de poucos décimos de ponto.
- O IFL mede o texto, não o assunto: um texto com muito nome próprio e termo técnico longo pontua baixo mesmo com frases curtas.
- O número por extenso não é lido pela M1.
- A data é conferida por valor para ano, trimestre e reunião (regra leve da seção Datas).
- O parser lê todo número solto entre 1900 e 2099 como ano; uma contagem nessa faixa escrita à mão sai como data sem lastro. A pendência está em `pendentes/pendencias-futuras.md`.
