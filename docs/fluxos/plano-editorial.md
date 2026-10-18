# Plano editorial (E4)

Este documento detalha a E4: como o `FactSheet` assinado vira o `PlanoEditorial` dos 3 níveis e as 9 células em `PENDENTE`. O desenho da etapa (pré-filtro, planejador, complemento, gate e limpeza, e os objetos `PlanoEditorial`, `PlanoNivel`, `Descarte`, `ClaimPlanejado` e `Analogia`) está em `docs/arquiteturas.md`, E4 e "Plano editorial". O glossário e a regra de casamento de termos estão em `docs/glossario.md`. A E2 e a E3, que entregam o FactSheet, estão em `docs/fluxos/extracao.md`.

## Estado

Implementada. A E4 grava o plano e as 9 células em `PENDENTE`; em seguida o grafo dispara as 9 células (E5 à E7, `docs/execucao.md`).

## Responsabilidade

- **Pré-filtro (código):** por nível, separa os candidatos dos descartados por regra (`metodologico`, `origem="prefiltro"`) e marca os claims candidatos nos 3 níveis, os únicos que podem ser núcleo. Sem nenhum candidato nos 3 níveis, o run aborta antes de chamar o modelo.
- **Planejador (LLM, 1 chamada para os 3 níveis):** escolhe o núcleo, a seleção, a ordem, o papel, a prioridade, a tese, os descartes com motivo específico e as analogias `nova`. Na correção, o prompt traz as mesmas regras e claims, o plano enviado e só a lista de problemas do gate; o modelo devolve o plano inteiro de novo, com o raciocínio `adapter_correcao`.
- **Complemento e gate (código):** completa `atribuir_fonte`, os descartes `secundario`, as analogias do glossário no Iniciante e as explicações do Intermediário, confere as 9 regras do gate e devolve só os problemas ao planejador, em até 3 rodadas.
- **Limpeza (código):** depois da última rodada, corrige por código o que o gate ainda acusa, para que o plano que chega à E5 passe sempre no gate, e registra no `aviso` o que mudou.
- **Gravação:** `rodadas_plano` e `validacoes_plano` por rodada, `planos_editoriais`, `planos_nivel` e as 9 linhas de `celulas` em `PENDENTE`.

## Módulos

| Arquivo | Função |
|---|---|
| `sunontent/nodes/adapt.py` | Nó da E4: roda pré-filtro, planejador, complemento e gate em loop, a limpeza depois da última rodada, grava as rodadas, o plano e as 9 células. Grava os spans. |
| `sunontent/adaptacao/` | Pré-filtro, planejador (monta o prompt de `prompts/adapter.j2` e chama o modelo), complemento, gate e limpeza (`limpeza.py`). |
| `sunontent/levelspec.py` | Leitura e validação de `config/levels/`. Usado pela E4 e, depois, pela E5 e pelas métricas. |
| `sunontent/glossario.py` | Leitura e validação de `config/glossario.yaml`, casamento de termos e escolha da analogia do glossário. Usado pela E4 e, depois, pela M5. |
| `prompts/adapter.j2` | Template único do planejador, parametrizado pelo LevelSpec dos 3 níveis. |

## LevelSpec da E4

A E4 lê de cada `config/levels/<nivel>.yaml` os campos `foco`, `tags_priorizadas`, `tags_descartaveis` e o `max_claims` do artigo. O mesmo arquivo tem o contrato de formato (`peso_papel` e `formatos`), lido pela E5.1, pelo prompt do redator e pela M2, no Iniciante e no Intermediário, o bloco `analogia`, lido pela M5 e pelo prompt do redator, no Iniciante, o bloco `densidade`, lido pela M4 e pelo prompt do redator, e, no Intermediário e no Avançado, o bloco `vocabulario`, lido só pelo prompt do redator.

| Campo | Iniciante | Intermediário | Avançado |
|---|---|---|---|
| `foco` | impacto prático no bolso do leitor | o que o fato muda para quem já investe | análise técnica e institucional |
| `tags_descartaveis` | `[metodologico]` | `[]` | `[]` |
| `tags_priorizadas` | `[impacto_pratico]` | `[indicador_chave, contexto_macro]` | `[indicador_chave, metodologico, governanca]` |
| `max_claims` (artigo, carrossel, roteiro) | 5, 4 e 3 | 7, 5 e 5 | 9, 5 e 5 |
| palavras do artigo | 350 a 550 | 450 a 750 | 600 a 900 |

Nos 3 níveis:

- `peso_papel`: `principal` 3, `apoio` 2 e `contexto` 1, para dividir as palavras do artigo entre os claims e as palavras das cenas de claim do roteiro;
- carrossel: de 5 a 7 slides, contando o gancho e a conclusão, com no máximo 120 caracteres no gancho, 220 em cada slide de corpo e 160 na conclusão;
- roteiro: 60 s a 2,5 palavras por segundo (150 palavras), com uma cena de abertura (o gancho) de 6 s, uma cena por claim e uma de fechamento de 6 s;
- `analogia` (Iniciante e Intermediário): `marcadores`, as expressões que a M5 procura na mesma seção, slide ou cena da primeira ocorrência de um termo com analogia ou explicação no plano. No Iniciante, `["é como", "funciona como", "imagine que", "na prática"]`; no Intermediário, `["ou seja", "isto é", "quer dizer", "em outras palavras"]`;
- `densidade` (só no Iniciante): `termos_max_por_100`, o teto de ocorrências de termos do glossário acima de iniciante por 100 palavras, por formato (`artigo` 5,5, `carrossel` 6,5 e `roteiro` 6,5). A M4 reprova acima dele, com piso de uma ocorrência por unidade, e o prompt do redator recebe o teto do formato;
- `vocabulario` (só no Intermediário e no Avançado): `explicar_acima_de`, o nível acima do qual o redator explica o termo do glossário na primeira vez que o usa. No Intermediário é `intermediario` (os termos de nível avançado são explicados); no Avançado é `avancado` (nenhum termo é explicado, e o redator escreve com o jargão técnico sem explicar).

Validação do `sunontent/levelspec.py`, que recusa campo desconhecido e aborta o run com `FalhaTerminal`:

- `analogia` é obrigatória no Iniciante, opcional no Intermediário e proibida no Avançado; `marcadores` não é vazio, não repete expressão e tem expressões de pelo menos duas palavras;
- `densidade` é opcional e proibido no Intermediário e no Avançado, e o teto é positivo nos 3 formatos;
- `vocabulario` é opcional e proibido no Iniciante, e `explicar_acima_de` é um nível igual ou acima do nível do arquivo;
- `formatos.artigo.palavras.min` cresce do Iniciante ao Avançado (350, 450 e 600);
- `max_claims` é artigo >= carrossel >= roteiro;
- `carrossel.max_claims` maior que `slides.max - 2` (os slides de corpo possíveis) emite um aviso (`UserWarning`) ao carregar o arquivo e não aborta; os valores atuais (4, 5 e 5 claims, com no máximo 7 slides) não emitem o aviso.

`instrucoes_redator(spec, formato)` devolve um `InstrucoesRedator` com `orcamento` (o contrato do formato em frases, para o prompt do redator), `marcadores` (a frase com as expressões de analogia ou de explicação, vazia no nível sem o bloco `analogia`), `legibilidade` (a meta de tamanho de frase, vazia no nível sem o bloco), `densidade` (o teto de termos do formato, vazio no nível sem o bloco), `vocabulario` (a regra de vocabulário, vazia no nível sem o bloco) e `faixa_esperada` (o limite renderizado, que a M2 registra). No artigo, a faixa conta as palavras do texto das seções; o título e a linha fina ficam de fora. O prompt e a M2 leem o limite da mesma função.

Por que esses valores:

- **Iniciante:** o foco é o efeito prático. Premissa e método de cálculo (no Copom, as hipóteses de câmbio, petróleo e bandeira tarifária do cenário de referência) são o que o leitor iniciante não usa, e saem por regra. `governanca` não é descartável: no fato relevante da CVM, o evento principal é societário.
- **Intermediário:** o foco é o que o fato muda para quem já investe. Não fala em tendência nem em recomendação, que o redator não pode acrescentar e o juiz reprova. Os números centrais e o cenário macro mostram o que muda, e as premissas do cenário também interessam, por isso nada sai por regra; o planejador corta com `exige_base_tecnica` ou `fora_do_foco`.
- **Avançado:** o foco é analítico. Método e governança (hedge, alíquota efetiva, diretoria, órgãos) são material do leitor institucional.
- **`max_claims` por formato:** o plano do nível tem os claims do artigo, o formato mais longo. O carrossel e o roteiro usam menos: o núcleo e os claims de maior `prioridade` (E5.1). No roteiro, 150 palavras menos o gancho e o fechamento deixam cerca de 120 para os claims; o Iniciante fica com 3, porque as analogias ocupam parte do texto falado. O mínimo de cada formato é 3, porque o núcleo tem até 3 claims e entra nos 3 níveis e nos 3 formatos.

Os valores são de partida e sobem a `versao` de cada YAML quando mudam.

### Validação

O `levelspec.py` valida cada arquivo com Pydantic, sem aceitar campo desconhecido em nenhum nível do arquivo:

- `nivel` igual ao nome do arquivo;
- `tags_priorizadas` e `tags_descartaveis` só com tags do vocabulário `Tag` (`sunontent/schemas.py`), sem tag em comum entre as duas listas;
- `tags_descartaveis` só aceita `metodologico`, a única tag que corresponde a um motivo de descarte;
- `max_claims` de cada formato inteiro maior ou igual a 3, com artigo >= carrossel >= roteiro;
- faixas com `min` menor ou igual a `max`; carrossel com pelo menos 3 slides; no roteiro, abertura e fechamento menores que a duração; pesos de papel inteiros maiores ou iguais a 1.

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

O casador devolve, para um texto, cada ocorrência com o `id` do termo, a forma casada e as posições de início e fim no texto. A E4 usa os termos encontrados no texto dos claims (complemento, gate e a coluna de termos do prompt) e na tese (gate); a M5 usa também as posições (a primeira ocorrência, para achar a unidade da analogia). Um filtro devolve só os termos com `nivel_minimo` acima de um nível.

Um teste roda o casador sobre os claims dos 3 documentos de referência (`tests/fixtures/referencia/`) e lista os termos do glossário escritos com flexão que não está em `variantes`, para completar o glossário.

### Analogia do glossário

Para um termo, a analogia do complemento é a primeira com `status: aprovada` ou, sem nenhuma aprovada, a primeira `rascunho`, com a referência `<id>#<n>` (posição na lista, começando em 1). `revisada` é `true` só para a aprovada. Termo sem analogia no glossário vai para a lista que o planejador recebe, para criar uma analogia `nova`.

### Explicação do Intermediário

No Intermediário, o complemento atribui a cada termo com `nivel_minimo` acima de intermediario encontrado no texto dos claims usados a `definicao_simples` do glossário como `explicacao` da `Analogia` do plano, com `origem` `glossario`, sem `glossario_ref` e com `revisada` falso. O termo é ligado à explicação pela forma escrita. O planejador não escreve explicação para o Intermediário, e o gate não tem regra para ela, porque todo termo do glossário tem `definicao_simples`.

A analogia `nova` vale para o termo da lista cujo nome casa, sem maiúsculas e sem acento, com o `termo` ou uma variante. O complemento deixa de fora a `nova` de termo fora da lista, de termo que já tem analogia no glossário e de termo fora dos claims usados. Como as analogias do glossário, a `nova` começa com "É como..." e não tem número.

## Prompt do planejador

`prompts/adapter.j2` (versão no cabeçalho) traz, para cada claim, o id, o tipo, as tags, onde ele é candidato, os termos do glossário acima de iniciante do texto com o `nivel_minimo` de cada um e se o termo tem analogia no glossário (`Selic (intermediario, com analogia)`) e o texto.

Antes dos níveis, o prompt diz o que vem primeiro no tipo de documento, nos 3 níveis:

- ata do Copom: a decisão de juros, a orientação futura (como e por quanto tempo a política fica restritiva, e a reação a choques e aos efeitos secundários) e a projeção de inflação no horizonte relevante; as premissas do cenário de referência e a lista de quem votou vêm depois;
- release de resultados: os resultados centrais (receita, lucro, EBITDA), o que vai para os acionistas e os eventos corporativos de maior efeito; o detalhe de cada linha de receita e despesa vem depois;
- fato relevante: o evento principal e o que muda para o acionista (o que recebe, o que pode fazer e até quando); as etapas operacionais do cronograma vêm depois.

Depois dos níveis, o prompt diz como o Intermediário e o Avançado se diferenciam na escolha: o Intermediário fica com os fatos que mudam algo para quem já investe e deixa de fora o detalhe técnico e metodológico que não muda essa leitura; o Avançado inclui as premissas e a metodologia, o balanço de riscos, a orientação futura em detalhe e a governança.

As regras do prompt são estas; o gate confere todas, menos a preferência do Iniciante no `exige_base_tecnica`, o que vem primeiro no tipo de documento e o sentido da tese, que ficam para a revisão humana na matriz do dashboard:

- núcleo entre os candidatos nos 3 níveis, presente nos 3 níveis;
- claims só entre os candidatos do nível, até o máximo do artigo, com papel e `prioridade` de 1 a n, sem repetir (1 é o mais importante; o carrossel e o roteiro ficam com o núcleo e os de maior prioridade);
- os claims que o pré-filtro tirou de um nível não entram nem nos claims nem nos descartes desse nível;
- `redundante` só quando o outro claim usado tem todos os números e todas as datas deste;
- `exige_base_tecnica` só com termo da coluna de termos acima do nível do leitor, e nunca no Avançado; no Iniciante, o planejador prefere usar o claim de impacto prático cujo termo tem analogia (do glossário ou `nova`) a descartá-lo;
- tese como paráfrase dos claims de `tese_claim_ids`, todos usados no nível, sem causa, tendência ou recomendação que eles não digam, sem números além de datas e, no Iniciante, sem termo técnico;
- analogias `nova` só no Iniciante, começando com "É como...", sem números e sem só repetir o termo.

## Gate

As 9 regras estão em `docs/arquiteturas.md`, E4.3: `id_inexistente`, `contraditorio`, `nucleo_ausente`, `acima_do_limite`, `prioridade_invalida`, `jargao_sem_analogia`, `analogia_invalida`, `motivo_invalido` e `tese_invalida`. A mensagem de cada problema diz ao planejador o que fazer e cita só o que ele vê no prompt (ids, motivos, números e termos), nunca a origem interna de um descarte.

O `redundante` compara os números por valor e unidade (`R$ 3,1 bilhões` e `R$ 3.100 milhões` são o mesmo valor em `BRL_mi`) e as datas por dia, mês e ano, lidas com `numeros.datas` (`19/10` e `19 de outubro` são a mesma data; ano ausente de um lado vale para qualquer ano), mais os componentes de data sem dia lidos com `numeros.varrer` (ano e trimestre).

## Limpeza

Se o gate ainda acusa problema depois da 3ª rodada, `sunontent/adaptacao/limpeza.py` corrige o plano na ordem de `docs/arquiteturas.md`, E4.3 (Limpeza): ids inexistentes, contraditório, núcleo, analogia `nova` inválida, jargão sem analogia, limite pela prioridade, motivo inválido, tese e prioridades; as analogias do Iniciante e as explicações do Intermediário ficam só para os termos dos claims que restaram. Depois o gate confere o plano de novo e precisa devolver zero problemas, aceitando só a tese vazia produzida pela limpeza.

O `aviso` do plano é a lista das mudanças, cada uma com a regra e o nível: `[acima_do_limite] iniciante: C06 saiu pela prioridade (máximo de 5); [tese_invalida] iniciante: a tese saiu (com número)`. Sem problema depois das rodadas, o plano não tem `aviso`.

## Modelo

| Nó | Snapshot | Raciocínio |
|---|---|---|
| `adapter` | `gemini-3.8-flash` | padrão do modelo na rodada 1; `low` nas correções (chave `adapter_correcao` em `raciocinio` de `config/models.yaml`) |

O preço é o mesmo já registrado para o extractor em `config/models.yaml`. Nos runs do release da B3, a rodada 1 tem cerca de 5,7 a 5,9 mil tokens de entrada; na ata do Copom, cerca de 3,2 a 3,9 mil; no fato relevante da CVM, cerca de 1,9 mil. Os spans registram o custo em BRL, pela cotação de `config/models.yaml`: a E4 custou de R$ 0,07 a R$ 0,46 por run, quase todo em tokens de saída, porque o raciocínio do modelo conta como saída e varia de 3 a 22 mil tokens entre runs. Cada span do `adapter` traz a rodada do gate.

O nó troca para `gemini-4-argon` só pelo `config/models.yaml`, com o preço do snapshot, se os runs mostrarem o gate reprovando com frequência ou a revisão humana na matriz do dashboard discordando das seleções. Preços e status consultados em 2026-10-14 em https://ai.google.dev/gemini-api/docs/pricing e https://ai.google.dev/gemini-api/docs/models: `gemini-3.8-flash` a US$ 0,75 de entrada e US$ 3,75 de saída por milhão de tokens até 2026-12-31 (US$ 1,50 e US$ 7,50 a partir de 2027-01-01); `gemini-4-argon` a US$ 2,00 e US$ 10,00 até 2026-11-30 (US$ 4,00 e US$ 20,00 a partir de 2026-12-01).

## Grafo

```
... validate (sem claim para corrigir) -> adapt -> END
... descarte                           -> adapt -> END
```

- A E4 entra depois da E3 com todos os claims aprovados ou depois do descarte; o plano usa só os claims do FactSheet assinado.
- O loop do gate fica dentro do nó `adapt`, que é um nó só para os 3 níveis: cada rodada atualiza `rodada_plano` e grava a saída do planejador em `rodadas_plano` e os problemas em `validacoes_plano`.
- Depois da E4, `disparar_celulas` envia cada uma das 9 células, em `PENDENTE` em `celulas`, ao subgrafo da célula.
- Antes da rodada 1, o nó apaga as linhas de `rodadas_plano` e `validacoes_plano` do run e a cópia local delas.
- O limite de rodadas é `RODADAS_PLANO` (3), em `graph.py`, passado ao nó `adapt`.

## Saída

- Supabase: `rodadas_plano` (a saída do planejador, uma linha por rodada, inclusive a que passou), `validacoes_plano` (uma linha por problema, por rodada), `planos_editoriais`, `planos_nivel` e `celulas` (9 linhas, estado `PENDENTE`, tentativa 0). Plano e células são gravados numa transação. Se o run já tem plano gravado, a gravação não muda nada: o mesmo plano não é erro, outro plano é erro terminal.
- Cópia local em `data/runs/<run_id>/`: `validacoes_plano/<rodada>.json` (a rodada, os problemas e a saída do planejador; existe em toda rodada, com `problemas: []` na que passou), `plano.json` e `celulas/<celula_id>/estado.json`.
- Estado do grafo: `rodada_plano`, `plano` e `celulas`.
- O estado e a tentativa de uma célula mudam por `atualizar_celula` (`persistencia.py`), que grava `atualizado_em` e a cópia local; a E5 usa a função na mesma transação que grava a peça.

## Terminal e dashboard

- O terminal ganha as linhas `etapa adapt: iniciada` e `etapa adapt: concluída` (`docs/execucao.md`, Linhas de etapa no terminal). A linha final diz em quantas rodadas o plano ficou pronto e, com `aviso`, que o plano tem aviso: `concluído: <doc> (<hash>), 22 claims, 1 descartado, plano em 2 rodadas (com aviso), 9 células (<estados>), <tokens> tokens, R$ <custo>`.
- No dashboard, a E4 entra como etapa da lista do fluxo, com o nó `adapt`. A subaba da E4 e a aba Info. técnicas mostram o plano gravado (a aba Resultado mostra o plano do nível de cada célula): o núcleo, o aviso, e, por nível, a tese, os claims usados com a prioridade, as analogias e os descartes, além dos problemas do gate por rodada (`docs/fluxos/dashboard.md`, Plano editorial).

## Como falha

Os modos de falha do plano (trivialização, núcleo mal escolhido, analogia enganosa) estão em `docs/arquiteturas.md`, E4. Os próprios desta implementação:

- LevelSpec ou glossário inválido: erro terminal no início da E4, antes de chamar o modelo.
- Nenhum claim candidato nos 3 níveis: erro terminal antes de chamar o modelo.
- Gate ainda com problema depois da 3ª rodada: a limpeza corrige o plano, o run segue com `aviso`, e as células dos níveis citados no `aviso` chegam à matriz do dashboard com ele.
- Núcleo vazio, ou claim do núcleo com termo sem analogia no Iniciante, na limpeza: erro terminal, depois de gravar as rodadas. Problema que o gate ainda acusasse no plano limpo também seria erro terminal (conferência de segurança).
- Falha do modelo: a mesma regra do `llm.py` da E2 (backoff na falha de infraestrutura, nova tentativa na resposta fora do schema, troca de chave em `docs/fluxos/extracao.md`, Cliente LLM). Resposta do planejador com analogia sem termo ou sem explicação, ou com prioridade menor que 1, é resposta fora do schema.
