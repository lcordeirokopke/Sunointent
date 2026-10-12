# Extração e validação de âncoras (E2 e E3)

Este documento detalha a E2 (Extractor & Anchor) e a E3 (Validador de Âncoras): como o `DocumentoProcessado` da E1 vira um `FactSheet` assinado, a lista de claims que é a única fonte de fatos do resto do pipeline. A arquitetura geral está em `docs/arquiteturas.md`, o cliente LLM em "1. Model client / router", e a leitura do PDF em `docs/fluxos/leitura.md`.

## Responsabilidade

- **E2:** o modelo lê o documento inteiro e propõe os claims. O código ancora cada um: acha a posição do trecho citado no texto limpo e associa os números da `tabela_numeros`. Devolve o `FactSheet` não assinado, que fica só no estado do grafo, e a lista dos claims que não conseguiu ancorar, com o motivo.
- **E3:** confere cada claim contra o mesmo texto limpo e a mesma `tabela_numeros` que a E2 recebeu, nunca contra o PDF. Com todos aprovados, assina e grava o FactSheet. Com algum reprovado, devolve só os reprovados para a E2, com o motivo.

O modelo nunca escreve página, offset nem valor de número. Ele cita o trecho e o bruto, e a posição e o `Numero` vêm do código. Assim, âncora e número inventados não chegam ao FactSheet: o que não existe no documento não resolve.

## Módulos

| Arquivo | Função |
|---|---|
| `sunontent/nodes/extract.py` | Nó da E2: extrai (rodada 1) ou corrige (rodadas seguintes), ancora e coloca o FactSheet no estado. Grava os spans. |
| `sunontent/nodes/validate.py` | Nó da E3: valida, grava a rodada e, com todos aprovados, assina e grava o FactSheet. |
| `sunontent/extracao/extractor.py` | Monta o prompt de `prompts/extractor.j2`, chama o modelo e renumera ou substitui as propostas. |
| `sunontent/extracao/ancoragem.py` | Localiza o trecho citado e resolve os números. Determinístico. |
| `sunontent/extracao/validador.py` | Regras da E3. Determinístico. |
| `sunontent/llm.py` | Cliente LLM: modelo por nó, saída JSON validada, retentativa, custo e span. Único módulo que importa o SDK do provider. |
| `sunontent/graph.py` | Arestas E2 -> E3 -> E2 e o limite de rodadas (`RODADAS_EXTRACAO`). |
| `sunontent/persistencia.py` | `gravar_span`, `gravar_validacao` e `gravar_factsheet`: Supabase e cópia local. |
| `config/models.yaml` | Snapshot do modelo de cada nó, preço por milhão de tokens e cotação USD/BRL. |

## Modelo

O extractor usa o `gemini-3.8-flash` (Gemini API, SDK `google-genai`), escolhido por ser um modelo estável, com versão fixa. A chave fica em `GOOGLE_API_KEY` no `.env`, conferida pelo `__main__.py` antes de o run ser criado. O juiz (M6) precisa ser de outra família.

## O que o modelo recebe

Uma chamada por rodada, com o documento inteiro. O prompt (`prompts/extractor.j2`) traz as regras e o documento entre `<documento>` e `</documento>`, um bloco por chunk (`### Bloco K03 | seção: ...`), com o texto limpo de cada página marcado por `[página N]`. O texto é o mesmo que a E3 usa para validar. O prompt avisa que o conteúdo do documento é dado e não instrução, para que um texto no PDF não seja lido como ordem.

A resposta segue o schema `SaidaExtractor` (`schemas.py`): uma lista de `ClaimProposto`, cada um com `id`, `chunk_id`, `texto`, `literal_fonte`, `numeros` (lista de brutos), `tipo` e `tags`.

Regras pedidas ao modelo:

- Cobertura: todos os fatos relevantes; sinalizações sobre a conduta futura e métricas secundárias com número próprio também viram claim. Dados administrativos (presentes, horários, telefones, CNPJ) ficam de fora.
- Um fato por claim. O mesmo número da mesma métrica em dois trechos vira um claim só, do trecho com o valor mais preciso.
- `texto`: uma frase autocontida, sem relação de causa que o documento não declara, com atribuição em projeções e avaliações.
- `literal_fonte`: cópia exata de um trecho contínuo de uma única página, sem atravessar nota de rodapé.
- `numeros`: o bruto de cada valor do fato, como aparece no trecho; sem anos, datas, trimestres e numeração. Texto e lista dizem o mesmo.

## Tipos e tags

`tipo`, um só: `decisao`, `resultado`, `projecao`, `condicionante` ou `risco`.

`tags`, uma ou mais, de um vocabulário fixo que o schema impõe (a E4 prioriza por elas):

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
4. Para cada bruto da lista, pega a entrada da `tabela_numeros` com esse bruto dentro do intervalo (espaços ignorados na comparação), sem usar a mesma entrada duas vezes.

Na primeira rodada, os ids são renumerados `C01`, `C02`... na ordem devolvida pelo modelo. Se ele não devolver nenhum claim, o run aborta com erro terminal.

Motivos de recusa da ancoragem, devolvidos ao modelo como estão: trecho não encontrado, trecho que atravessa a página ou uma nota de rodapé, trecho repetido fora do chunk citado e número que não está dentro do trecho (a mensagem lista os números que estão).

## Validação (E3)

Cada claim passa ou reprova, sem nota:

1. A página da âncora existe e o intervalo cabe nela.
2. O texto limpo no intervalo é exatamente o `literal_fonte`.
3. Cada `Numero` do claim é uma entrada da `tabela_numeros`, com o mesmo valor, unidade, bruto e posição. Unidade trocada (`pp` no lugar de `pct`) é recusada com o valor e a unidade da tabela.
4. Cada número está dentro do `literal_fonte`.
5. O texto do claim e a lista de números dizem o mesmo: o texto é lido pelo mesmo parser da E1 (`numeros.varrer`), e cada número listado precisa aparecer no texto com o mesmo valor e unidade; cada número do texto, exceto datas, precisa estar na lista. Isso recusa claim que carrega números de outro fato e número no texto que não veio do documento.
6. Id repetido é recusado.

Os claims que a E2 não ancorou também reprovam, com o motivo da E2.

## Loop E2/E3

```
E1 -> E2 (rodada 1: extração) -> E3 --todos aprovados--> FactSheet assinado (fim do grafo, por enquanto)
                                    --reprovados, rodada < 3--> E2 (correção só dos reprovados) -> E3
                                    --reprovados, rodada = 3--> run abortado (terminal)
```

- Na correção, o prompt traz o documento inteiro de novo e, para cada claim reprovado, o claim enviado e o motivo. O modelo devolve só esses claims, com o mesmo id. Id fora da lista é ignorado; claim reprovado que o modelo não devolver continua como estava e reprova de novo.
- No máximo 3 rodadas: a extração e até 2 correções (`RODADAS_EXTRACAO` em `graph.py`). Se ainda houver claim reprovado, o run aborta com a lista de claims e motivos. Nada segue sem assinatura.
- A cada rodada, todas as propostas são ancoradas e validadas de novo.

## Cliente LLM

`llm.ClienteLLM.chamar(no, prompt, schema)` lê o snapshot do nó em `config/models.yaml`, pede ao provider saída JSON no schema Pydantic e devolve o objeto validado, nunca texto. Duas contagens separadas, nenhuma ligada ao `K`:

- **Falha de infraestrutura** (HTTP 408, 429, 5xx, timeout, erro de conexão): repete com o backoff de `retentativa.py`, até 4 tentativas; depois, erro terminal.
- **Resposta fora do schema:** repete até 3 vezes, acrescentando ao prompt o erro de validação; depois, erro terminal.
- **Conteúdo recusado pelo provider** (bloqueio do prompt ou `finish_reason` de segurança) e demais erros HTTP 4xx: terminal, sem repetir.

A retentativa interna do SDK fica desligada, para que cada tentativa passe pelo cliente e vire um span. O timeout por chamada é de 300 s.

## Custo

Cada tentativa, com sucesso ou não, vira um `Span` (`schemas.py`): nó, modelo, tokens de entrada e de saída (os de raciocínio contam como saída), custo, duração e a classe e a mensagem da falha. O custo é uma estimativa: tokens vezes o preço em US$ por milhão de `config/models.yaml`, convertido pela `cotacao_usd_brl` do mesmo arquivo. A cotação é provisória e está marcada com `PENDENTE:` no YAML. Não há teto de orçamento: o custo é registrado, não interrompe o run.

O custo acumulado do run é atualizado na mesma instrução SQL que grava o span, e span repetido não soma de novo. O cliente também acumula o total em memória, e o `__main__.py` o escreve no `manifest.json` local ao concluir ou abortar o run.

Ordem de grandeza nos documentos de referência, com 2 rodadas: de R$ 0,15 a R$ 0,60 por documento.

## Saída

| O quê | Supabase | Cópia local em `data/runs/<run_id>/` |
|---|---|---|
| Span de cada tentativa (E2) | `llm_spans`, e o custo somado em `runs` | `spans/<span_id>.json` |
| Resultado de cada rodada (E3) | `validacoes_ancora`: uma linha por claim proposto, com `passou` e `motivo` | `validacoes/<rodada>.json` |
| FactSheet assinado (E3) | `factsheets`, `claims` e `claim_numeros`, numa transação | `factsheet.json`, sem a `tabela_numeros`, que já está em `documento.json` |

Todas as gravações usam `on conflict do nothing`: repetir não duplica linhas. Cada `claim_numeros` guarda o `Numero` completo e o `numero_id` da sua entrada em `numeros`, sempre preenchido. O FactSheet só é gravado assinado; `gravar_factsheet` recusa um FactSheet não assinado.

## Como falha

- **Omissão silenciosa:** um fato que não vira claim não existe para o resto do pipeline, e nenhuma etapa detecta. A defesa é o teste de cobertura com os documentos anotados à mão (`docs/testes.md`).
- **Não convergência:** claim que segue reprovado depois de 3 rodadas aborta o run.
- **Provider fora ou resposta inválida:** retentativas esgotadas abortam o run com erro terminal.
- **Falha de gravação:** segue a classificação de `docs/fluxos/ingestao.md`; esgotadas as tentativas, aborta o run.
