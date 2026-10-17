# Síntese de formato (E5)

Este documento detalha a E5 de uma célula: como o plano de um nível vira a `Peca` de um formato. O desenho da etapa (pacote, redator e montagem, e os objetos `Peca`, `NumeroUsado` e os schemas de saída do redator) está em `docs/arquiteturas.md`, E5 e "Peça final". O M7 (`plan_contract`), calculado na montagem, está em `docs/metricas.md`. O plano que a E5 recebe está em `docs/fluxos/plano-editorial.md`.

## Estado

Implementada, com o nó `synth`, no subgrafo da célula com a E6 e a E7 (`docs/fluxos/avaliacao.md`). O grafo principal dispara o subgrafo para as 9 células depois da E4, até 3 em paralelo (`docs/execucao.md`).

## Responsabilidade

- **Pacote (código):** corta os claims do plano do nível para o formato, troca os números do texto de cada claim por marcadores, calcula o orçamento, prende as analogias aos claims do formato e, no nível com o bloco `vocabulario`, lista os termos de especialista dos claims do formato.
- **Redator (LLM, 1 chamada por tentativa):** escreve o payload do formato com os marcadores no texto, no schema do formato.
- **Montagem (código):** troca os marcadores pelos números, registra a posição de cada número em `numeros_usados` e calcula o M7 (`plan_contract`).

## Módulos

| Arquivo | Papel |
| ------- | ----- |
| `sunontent/sintese/pacote.py` | E5.1: corte por formato, marcadores, orçamento, analogias e termos a explicar do formato. |
| `sunontent/sintese/redator.py` | E5.2: prompt de `prompts/synthesizer.j2` e chamada pelo `llm.py` com o schema do formato. |
| `sunontent/sintese/montagem.py` | E5.3: troca dos marcadores, `numeros_usados`, `plan_contract` e `restaurar` (a peça de volta com os marcadores, para o retry). |
| `sunontent/nodes/synth.py` | Nó da célula: lê o plano e o FactSheet, chama os três passos e grava a peça. |

## Pacote

**Corte por formato.** O artigo usa todos os claims do plano do nível. O carrossel e o roteiro usam até o `max_claims` do formato no LevelSpec: entram os claims do núcleo e, nas vagas que sobram, os de menor `prioridade` (1 é o mais importante), e o pacote os mantém na ordem de leitura do plano. Como todo `max_claims` é pelo menos 3 e o núcleo tem até 3 claims, o núcleo sempre cabe.

**Marcadores.** Para cada claim do plano do nível, o código varre o texto com `numeros.varrer`, ignora os achados de unidade `data` e troca o trecho de cada achado por `{{<claim_id>.<ordem>}}`. Cada achado, em ordem de aparição, casa com a primeira entrada ainda livre da lista de números do claim com o mesmo valor e unidade; a `ordem` é a posição dessa entrada, de 1 em diante. O marcador guarda o trecho do texto do claim que cobriu (`13,75%`, `R$ 1,2 bi`), e é esse trecho que volta na montagem. Achado sem entrada, entrada sem achado ou número em formato que o parser não lê é `FalhaTerminal` de dado. Os marcadores são calculados para todos os claims do plano do nível, e são esses os válidos na montagem; o prompt mostra só os do formato. As datas ficam no texto, sem marcador.

**Orçamento.** O contrato do formato em frases vem de `levelspec.instrucoes_redator`, a mesma função que dá à M2 o limite registrado. A divisão por claim usa o `peso_papel` do LevelSpec e é sempre arredondada para baixo, para que a soma não passe do limite que a M2 confere:

| Formato | Divisão |
| ------- | ------- |
| `artigo` | cada claim recebe de piso(`palavras.min` × peso ÷ soma dos pesos) a piso(`palavras.max` × peso ÷ soma dos pesos) palavras |
| `roteiro` | os segundos entre a abertura e o fechamento (`duracao_s - abertura_s - fechamento_s`) são divididos pelo peso, arredondados para baixo, e o resto vai para a última cena de claim; cada cena (abertura, de claim e fechamento) recebe piso(segundos × `palavras_por_segundo`) palavras |
| `carrossel` | sem divisão por claim: a faixa de slides e o limite de caracteres de cada papel valem para o carrossel inteiro, e o redator distribui os claims entre os slides de corpo |

Exemplo, Iniciante: no artigo, com os papéis principal, apoio, apoio, contexto e contexto (pesos 3, 2, 2, 1 e 1, soma 9), o principal fica com 116 a 183 palavras e cada contexto com 38 a 61. No roteiro, com principal, apoio e contexto, os 48 s viram 24, 16 e 8 s, com até 60, 40 e 20 palavras; a abertura e o fechamento têm 6 s e até 15 palavras cada.

**Analogias.** Cada analogia do plano do nível (só o Iniciante tem) vai presa aos claims do formato em cujo texto o termo aparece, casado pelo glossário (`docs/glossario.md`, Casamento no texto). A analogia de um termo que só aparece em claims cortados do formato sai do pacote.

**Termos a explicar.** No nível com o bloco `vocabulario` no LevelSpec, o pacote lista os termos do glossário com `nivel_minimo` acima de `explicar_acima_de` que aparecem no texto dos claims do formato, casados pelo glossário, cada um com a `definicao_simples` e os claims em que aparece. No Intermediário (`explicar_acima_de: intermediario`) são os termos de nível avançado; no Avançado (`explicar_acima_de: avancado`) a lista é sempre vazia; o Iniciante não tem o bloco. O termo que só aparece em claims cortados do formato sai do pacote, e o termo que já tem explicação no plano do nível (a do Intermediário) vai na lista de analogias do pacote, não aqui.

O pacote traz também a tese (vazia quando a limpeza da E4 a tirou, e então o prompt não tem a seção), o papel, o `atribuir_fonte` e a marca de núcleo de cada claim, o tipo e o emissor do documento e a `data_documento`.

## Prompt do redator

Um template, `prompts/synthesizer.j2`, para as 9 células, nesta ordem:

1. **Regras comuns:** não criar fatos, causa ou consequência que os claims não digam; todo número que não seja data só pelo marcador, sem conta nem arredondamento; datas como o claim as escreve, com o ano do documento permitido na data sem ano; seguir a ordem de leitura; escrever com atribuição o claim marcado "atribuir à fonte"; listar em `claim_ids` os claims de cada unidade, cobrindo todos os do formato; as regras de estrutura do formato (título e linha fina, gancho e conclusão, abertura e fechamento, `gancho_visual` sem número).
2. **O nível:** o `foco` do LevelSpec; no Iniciante e no Intermediário, as expressões de analogia ou de explicação (`instrucoes_redator(...).marcadores`) e, no Iniciante, a meta de tamanho de frase (`instrucoes_redator(...).legibilidade`, do bloco `legibilidade`); no Intermediário e no Avançado, a regra de vocabulário (`instrucoes_redator(...).vocabulario`, do bloco `vocabulario`): o Intermediário usa sem explicar o vocabulário padrão de quem investe e explica o termo de especialista, na primeira vez, com a explicação do plano e uma das expressões de `marcadores`, ou o troca pela explicação; o Avançado escreve para o leitor profissional, com o jargão técnico sem explicar.
3. **O formato:** o orçamento de `instrucoes_redator` e, no roteiro, o tempo da abertura e do fechamento.
4. **A tese do nível**, quando há.
5. **Os claims**, na ordem de leitura: id, papel, núcleo, atribuição, a parte do orçamento, o texto com marcadores e a tabela de marcadores (`{{C01.1}} = 13,75%`).
6. **As analogias**, com os claims do formato em que o termo aparece e a instrução de explicar o termo na primeira vez que for usado, mantendo o sentido e só encurtando.
7. **Os termos de especialista**, quando o pacote tem termos a explicar: cada termo com os claims do formato em que aparece e a `definicao_simples`, e a instrução de explicá-lo em poucas palavras na mesma frase, sem acrescentar fato, ou trocá-lo pela explicação.
8. **Correção**, só no retry: a peça anterior em JSON, com os marcadores restaurados (inclusive o inválido), o que está certo e não deve mudar, as violações agrupadas pelo lugar da peça (a unidade, o título, a linha fina ou a peça inteira) e, por métrica, quantas ficaram de fora da lista.

O relatório corretivo é um `RelatorioCorretivo` (`sunontent/schemas.py`): `certos` (o que passou, com o valor), `violacoes` (até 5 por métrica, na ordem do scorecard) e `restantes` (por métrica, as violações além das 5). O template só o renderiza.

## Modelo

`synthesizer` em `config/models.yaml`: `gemini-3.8-flash`, com o raciocínio padrão do modelo na tentativa 1 e `synthesizer_correcao` (`low`) no retry. A chamada passa pelo `llm.py`, com o schema do formato (`SaidaArtigo`, `SaidaCarrossel` ou `SaidaRoteiro`); a resposta fora do schema volta ao modelo pelo próprio `llm.py` e não consome tentativa da célula. O span leva o nível, a célula e a tentativa.

## Montagem

1. Em cada campo que aceita número (o título do artigo e o texto de cada unidade), cada marcador válido vira o trecho guardado no pacote, e o `NumeroUsado` registra o campo, a unidade (nula no título), o intervalo do número no texto final, o claim e a ordem. O marcador que não está entre os válidos (claim inexistente, ordem que o claim não tem ou claim fora do plano do nível) fica no texto como veio: a M1 o encontra no texto final e o aponta.
2. A linha fina e o gancho visual não aceitam marcador já no schema do redator; o número escrito fora de marcador em qualquer campo fica no texto, e a M1 o aponta na E6.
3. O M7 (`plan_contract`) é calculado sobre o payload final e montado com `metrics.resultado.montar(PlanContractDetalhes(...), violacoes)`. As checagens de cada formato:

| Checagem | Formatos | Violação |
| -------- | -------- | -------- |
| `quantidade_de_slides` | carrossel | uma, se a quantidade sai de `slides.min` a `slides.max`, com a instrução de juntar ou dividir slides de corpo |
| `soma_das_cenas` | roteiro | uma, na última cena, se ela termina depois de `duracao_s` |
| `claim_ids_no_plano` | todos | uma por unidade e claim fora do plano do nível, na unidade |
| `cobertura_do_formato` | todos | uma por claim do formato que não aparece em nenhuma unidade |

`restaurar(peca)` faz o caminho inverso, para o retry: cada número de `numeros_usados` volta a ser o seu marcador, e o resultado é a saída do redator que deu a peça.

## Nó

`nodes/synth.py` recebe um `CelulaGrafoState` (`run_id`, a `CelulaState` e, no retry, o `RelatorioCorretivo`) e as dependências de `config["configurable"]` (`repositorio`, `raiz_dados`, `raiz` e `llm`):

1. lê o LevelSpec do nível e o glossário, o plano por `carregar_plano` (sem plano, `FalhaTerminal`) e o FactSheet assinado por `carregar_factsheet`;
2. grava a célula em `GERANDO` por `atualizar_celula`;
3. monta o pacote, chama o redator com a tentativa seguinte à da célula e, no retry, a peça anterior (`celula.peca`) restaurada e o relatório do estado;
4. monta a peça e o M7 (`plan_contract`);
5. grava a peça com a célula em `AVALIANDO` e a tentativa nova, e devolve a célula com `peca`, `tentativa`, `estado` e M7 (`plan_contract`), que a E6 junta ao scorecard, os tokens das chamadas de LLM da tentativa (`tokens_tentativa`) e o início da tentativa (`inicio_tentativa`), para o scorecard.

Uma `Falha` em qualquer passo não sobe do nó: vai para `falha` no estado, com a classe e a mensagem, e o subgrafo segue direto à E7, que grava a célula em `ERRO_INFRA`. A E5 não grava estado final.

## Saída

`persistencia.gravar_peca` grava numa transação, no Supabase:

- `pecas`: a peça da tentativa, com o payload final e a união dos `claim_ids`;
- `peca_unidade_claims`: cada unidade com os seus claims, sem repetir e só os que existem no FactSheet do run (um id inventado pelo redator fica no payload e em `pecas.claim_ids` e é violação do M7 (`plan_contract`), mas não tem página para rastrear);
- `peca_unidade_numeros`: cada `NumeroUsado`, com o `campo` (`texto` ou `titulo`) e a unidade nula no título, ligado a `claim_numeros`;
- a célula, pela mesma atualização de `atualizar_celula`.

A mesma peça gravada de novo na mesma tentativa não grava nada; outra peça na mesma tentativa é `FalhaTerminal`. Depois, a cópia local: `data/runs/<run_id>/celulas/<celula_id>/peca_<tentativa>.json` e `estado.json`. O `estado.json` não leva a peça, o scorecard nem o M7 (`plan_contract`).

## Como falha

- **Dado do claim:** número do texto do claim sem entrada na lista, ou o contrário, é `FalhaTerminal` no pacote (a E3 recusa esse claim, então só acontece com dado gravado fora do pipeline).
- **Resposta fora do schema:** volta ao modelo pelo `llm.py`, sem consumir tentativa; esgotadas as tentativas de parse, a `Falha` vai para `falha` no estado, e a célula termina em `ERRO_INFRA`.
- **Provider, leitura ou gravação:** a `Falha` vai para `falha` no estado, e a célula termina em `ERRO_INFRA`.
- **Peça fora do contrato:** marcador inválido, número escrito à mão, slides demais, roteiro longo, claim fora do plano ou claim do formato ausente não interrompem a E5: a peça é gravada, e as violações ficam para a E6 (M1 e M7 (`plan_contract`)).
