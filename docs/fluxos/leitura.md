# Leitura (E1)

Este documento detalha a etapa E1: como o PDF entregue pela E0 vira texto limpo posicionado, chunks e a tabela de números. A arquitetura geral está em `docs/arquiteturas.md`.

## Responsabilidade

Recebe o `DocumentoFonte`, confere o SHA-256 dos bytes lidos contra `doc_sha256` e devolve um `DocumentoProcessado`. É determinística: zero LLM, zero token. Qualquer falha aborta o run com erro terminal, porque o texto limpo é a referência de todos os offsets do pipeline.

## Módulos

| Arquivo | Função |
|---|---|
| `sunontent/nodes/leitura.py` | Nó do grafo: chama `extracao/leitura.py`, grava a saída e a coloca no estado. |
| `sunontent/extracao/leitura.py` | Orquestra: PDF, normalização, chunks, metadados e tabela de números. |
| `sunontent/extracao/pdf.py` | Confere o hash, abre o PDF (pymupdf) e devolve as linhas de cada página com posição. |
| `sunontent/extracao/normalizacao.py` | Cabeçalho e rodapé repetidos, número de página, hifenização, espaço e descarte da versão em inglês do fim do documento. Gera o texto limpo. |
| `sunontent/extracao/chunking.py` | Chunks por seção lógica. |
| `sunontent/extracao/tabelas.py` | Unidade das células de tabela, pelo cabeçalho ou pelo rótulo da linha. |
| `sunontent/extracao/metadados.py` | Emissor e data do documento. |
| `sunontent/numeros.py` | Parser de número brasileiro e varredura do texto. Também é usado pelas métricas. |
| `sunontent/persistencia.py` | `gravar_documento_processado`: Supabase e cópia local. |

## Leitura do PDF

O pymupdf decide onde há espaço pela distância entre caracteres, o que gera `202 6` e `R $1,3` em PDFs exportados com texto justificado. A E1 lê com `TEXT_INHIBIT_SPACES`: só existe espaço onde o PDF tem um caractere de espaço.

Esses PDFs também partem números em fragmentos sem espaço entre eles (`R$31` e `5,2` são `R$315,2`) e, às vezes, quebram a linha no meio do número (`R$1` e `.701,2`). A E1 une os fragmentos que ficam na mesma linha base, no mesmo bloco, sem espaço na fronteira, e só quando os dois lados formam um mesmo token:

- dígito com dígito, vírgula, ponto, `%` ou hífen;
- vírgula ou ponto seguido de dígito, e `$` seguido de dígito;
- letra com letra minúscula (palavra partida) ou com pontuação;
- `(` seguido de letra ou dígito.

Rótulo de tabela seguido de valor (`Receita total` e `3.081,4`), `)` seguido de dígito e letra maiúscula colada não se unem. A distância horizontal não entra na regra, porque nesses PDFs a posição do texto não acompanha o desenho. A quebra de linha no meio de número só é desfeita quando a linha termina em dígito e a seguinte começa com `.` ou `,` seguido de dígito, ou quando a linha termina em dígito e vírgula e a seguinte começa com dígito.

Erros terminais: arquivo ausente, SHA-256 diferente (a mensagem traz o arquivo, o hash esperado e o encontrado, e pede para apagar o arquivo local), PDF que não abre, PDF com senha e página sem camada de texto. O OCR ainda não existe, e a coluna `ocr` de `documento_paginas` fica sempre `false`.

## Normalização

- Ligaduras viram letras, espaço sem quebra vira espaço, caracteres de largura zero e hífen opcional saem, e o espaço é colapsado.
- Cabeçalho e rodapé: linhas na margem da página (12% da altura, em cima e embaixo) que se repetem em pelo menos metade das páginas, em documentos de 3 páginas ou mais. Em linha com letras, os dígitos são mascarados na comparação (`Relatório 3`); linha só de número é comparada pelo valor. Número de página isolado (`3`, `Página 3 de 9`) sai da margem em qualquer documento, mas só se for o número da página, descontado o deslocamento da numeração (a capa pode não ser contada). Um número na margem que não é o da página, como uma célula de tabela no pé da página, é mantido.
- Palavra hifenizada: linha que termina em letra e hífen, seguida de linha que começa em minúscula, é juntada sem o hífen.
- As linhas do PDF são mantidas: o texto limpo de cada página é o das linhas separadas por `\n`.
- Versão em inglês: depois da normalização, o bloco final de páginas em inglês é descartado (o fato relevante da CVM pode repetir o documento traduzido no fim). A página é inglês quando tem ao menos 20 palavras curtas típicas do inglês (`the`, `of`, `and`, `to`...) e mais que o dobro das típicas do português (`de`, `que`, `para`, `com`...). Só o bloco final sai, e página vazia não o interrompe: as páginas que ficam continuam numeradas como no PDF. Página em inglês no meio do documento fica, e documento todo em inglês fica inteiro. O terminal mostra `E1: páginas 4 a 6 descartadas: versão em inglês`; nada é gravado sobre as páginas descartadas.

A página é 1-indexada, e o offset é de caractere no texto limpo da página, começando em 0, com fim exclusivo.

## Chunks

Por seção lógica, cobrindo o texto inteiro sem sobreposição. O texto antes da primeira fronteira vira o primeiro chunk, com `secao` vazia. `chunk_id` é `K01`, `K02`, e assim por diante.

| Documento | Fronteira | `secao` |
|---|---|---|
| Ata do Copom | Parágrafo numerado, só na sequência 1, 2, 3... do documento | Título `A)`, `B)`, `C)` ou `D)` vigente. O título e o primeiro parágrafo da seção formam um chunk só. |
| Release da B3 | Linha em maiúsculas, sem dígitos, com 8 caracteres ou mais | O título. Títulos seguidos formam um só. |
| Fato relevante da CVM | Nenhuma | Vazia. O documento todo é um chunk. |

Subseções da B3 em caixa mista ficam dentro do chunk da seção maior. O `Chunk.texto` é o texto limpo do intervalo: página inicial a partir de `offset_inicio`, páginas do meio inteiras e página final até `offset_fim`, unidas por `\n`.

## Metadados

Sem LLM, por regra do texto limpo.

| Fonte | Emissor | Data do documento |
|---|---|---|
| Copom | Texto fixo do Comitê de Política Monetária do Banco Central | Último dia da reunião (`15 e 16 de setembro de 2026` é 2026-09-16) |
| CVM | Nome de cada companhia no cabeçalho da primeira página, antes do título (`FATO RELEVANTE` ou `MATERIAL FACT`): as linhas do nome até a linha de CNPJ, NIRE ou `Companhia Aberta`, ignorando linhas sem letra; no fato relevante conjunto, os nomes unidos por `; `. Sem título ou sem nome antes dele, a primeira linha com letras, com o aviso `E1: emissor não identificado no cabeçalho; usando a primeira linha: '...'`; página sem nenhuma letra aborta | Linha de assinatura (`São Paulo, 08 de outubro de 2026.`) |
| B3 | Texto fixo da B3 S.A. | Data-base do balanço (`CONSOLIDADO EM 30/06/2026`) |

Se o padrão não for encontrado, o run aborta com erro terminal.

## Tabela de números

`numeros.varrer` percorre o texto limpo de cada página. O `bruto` é o trecho exato, com a unidade, e a âncora aponta para ele.

- Ponto separa milhar e vírgula separa decimal. Valor em `Decimal`, nunca `float`.
- Formato fora disso (`1,234.56`, `12.5%`, `1.93`, `3.1`, `0.500`, `1,2,3`) levanta erro e aborta o run, indicando a página e o trecho. O parser não adivinha.
- `pct`: `%` ou `por cento`. `pp`: `p.p.`, `pp` ou `pontos percentuais`; `bps` e `pontos-base` também viram `pp` (1 bp é 0,01 p.p.).
- `BRL`: `R$` sem escala. `BRL_mi`: `R$` com `mil`, `milhão`, `bilhão` ou `trilhão`, normalizado para milhões. `R$5,15/US$` (cotação) é um `BRL`. Outra quantia em dólar ou euro aborta.
- `x`: `2,5x` ou `3 vezes`. `contagem`: número puro; com escala sem `R$` (`11,1 milhões`) o valor é em unidades.
- `data`: cada componente numérico é um número, com o inteiro escrito como valor. `15 e 16 de setembro de 2026` dá 15, 16 e 2026; `2T26` dá 2 e 26; `30/06/2026` dá 30, 6 e 2026; `281ª reunião` e `1º tri` (ou `1º trimestre`) dão 281 e 1; ano de 1900 a 2099 sem separador dá o ano.
- Sinal: `-` ou `−` colado ao número e fora de intervalo (`2026-2030` não tem sinal). Parênteses de valor negativo em tabela ficam fora do `bruto`, e o valor fica positivo.
- Ignorados: CNPJ, CPF, CEP, NIRE, telefone, hora (`10h06`, `10:00h`), endereço web, numeração de parágrafo no início da linha (`12. `) e número colado a letra (`Copom1`, `B3`, `5G`).

Célula de tabela (`sunontent/extracao/tabelas.py`): numa tabela, a unidade fica no cabeçalho (`(Em R$ milhões)`) ou no rótulo da linha (`RPC média (R$)`, `ADV (milhares de contratos)`), e a célula é só o número, que o parser lê como `contagem`. A E1 dá à célula a unidade declarada acima dela, na mesma página:

- célula: linha que é só um número sem unidade própria, com ou sem parênteses de negativo;
- declaração: linha com um parêntese de unidade reconhecida: `(R$)`, `(R$ mil)`, `(R$ milhares)`, `(R$ milhões)`, `(R$ bilhões)`, `(%)`, `(% a.a.)`, `(bps)`, `(milhares ...)` ou `(milhões ...)`. Vale para as células seguintes até a próxima declaração. Parêntese que não é unidade (`(Trillia)`, `(+/-)`) é ignorado;
- o cabeçalho de colunas de uma nova tabela (`2T26`, `2026 2027`, `1º tri 2028`), depois de já ter havido célula com a unidade declarada, encerra a declaração;
- `exceto LPA` deixa como estão as células da linha do LPA (`LPA` ou `Lucro por ação`), e linha de quantidade (`Número de...`, `Nº de...`, `Quantidade de...`) só usa a unidade que ela mesma declara;
- a conversão segue as regras acima: `3.081,4` sob `(Em R$ milhões)` é `BRL_mi` 3081,4; `3.081.392` sob `(Em R$ milhares)` é `BRL_mi` 3081,392; `4,1` sob `(%)` é `pct` 4,1; `5.909` sob `(milhares de contratos)` é `contagem` 5909000. O `bruto` e a âncora não mudam.

Todos os números do documento entram na tabela, não só os que virarão claims.

## Saída

- `documentos_processados` (uma linha por run: `doc_id`, `doc_sha256`, `tipo_documento`, `emissor` e `data_documento`), `documento_paginas`, `chunks` e `numeros`, numa transação, com `on conflict do nothing`: repetir a gravação não duplica linhas. A cópia local é `data/runs/<run_id>/documento.json`, gravada depois do Supabase.
- `documentos_processados` aponta para `runs` por `(run_id, doc_id, doc_sha256)`: o banco recusa a saída da E1 de um documento que não é o vinculado ao run pela E0 ou pela referência do `--dev`. As páginas do run apontam para `documentos_processados`.
- O `DocumentoProcessado` valida a si mesmo: páginas contíguas a partir de 1, o `bruto` de cada número igual ao texto na âncora, e o texto de cada chunk igual ao intervalo das páginas.
