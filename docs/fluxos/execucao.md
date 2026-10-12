# Fluxo de execução (run)

Este documento descreve como um run começa, como o grafo é montado e executado e como ele termina, concluído ou abortado. A ingestão (E0 e o modo `--dev`) está em `docs/fluxos/ingestao.md`, a leitura (E1) em `docs/fluxos/leitura.md`, a extração e a validação (E2 e E3) em `docs/fluxos/extracao.md`, o dashboard em `docs/fluxos/dashboard.md`, e o RunManifest, o estado e o checkpoint em `docs/arquiteturas.md`.

## Responsabilidades

| Peça | Papel |
| ---- | ----- |
| `sunontent/__main__.py` | Ponto de entrada (`python -m sunontent [fonte] [--dev]`). Lê o `.env`, confere as credenciais do Supabase e do Gemini, gera o `run_id`, monta o manifest, registra o run, executa o grafo escrevendo as linhas de etapa no terminal e fecha o run como `concluido` ou `abortado`, com o custo do run. |
| `sunontent/graph.py` | Monta o grafo LangGraph: as duas entradas (E0 ou referência), a E1, a E2, a E3, o loop E2/E3, as constantes `K` e `RODADAS_EXTRACAO` e `construir_grafo(checkpointer)`. A ordem das etapas fica só aqui. |
| `sunontent/manifesto.py` | Lê `config/` e `prompts/` e devolve o `RunManifest` sem os campos do documento. |
| `sunontent/schemas.py` | `RunManifest`, `PipelineState` e os tipos `Nivel`, `Fonte` e `Modo`. |
| `sunontent/llm.py` | `conferir_credenciais` (`GOOGLE_API_KEY`) e `criar_cliente`: um `ClienteLLM` por run, que acumula os tokens e o custo do run. |
| `sunontent/persistencia.py` | Funções do run: `criar_run`, `concluir_run`, `abortar_run`, `gravar_manifest_local`, `criar_repositorio_supabase` e `checkpointer_postgres`. Única porta de gravação no Supabase e em `data/`. |
| `sunontent/nodes/` | Nós finos: `coleta` e `referencia` (entradas), `leitura` (E1), `extract` (E2) e `validate` (E3). Não tratam erro: a `Falha` sobe até o `__main__.py`. |

## Ciclo de vida do run

```mermaid
flowchart TD
    CLI["python -m sunontent [fonte]"] --> ENV["le o .env e confere SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_DB_URL e GOOGLE_API_KEY"]
    ENV -->|"ausente ou conexao recusada"| PARA["erro na tela, codigo 1, sem linha em runs"]
    ENV --> CK["abre o checkpointer: conexao propria, schema langgraph, setup()"]
    CK --> MAN["gera run_id e monta o RunManifest a partir de config/ e prompts/"]
    MAN -->|"arquivo ausente, vazio ou sem versao"| PARA
    MAN --> RUN["insere a linha em runs com status em_andamento e grava data/runs/run_id/manifest.json"]
    RUN --> G{"graph.stream: manifest.modo"}
    G -->|"normal"| E0["no coleta: E0"]
    G -->|"dev"| REF["no referencia"]
    E0 --> E1["no leitura: E1"]
    REF --> E1
    E1 --> E2["no extract: E2"]
    E2 --> E3["no validate: E3"]
    E3 -->|"claim reprovado e rodada menor que 3"| E2
    E3 --> FIM{"resultado"}
    FIM -->|"FactSheet assinado"| OK["status concluido, manifest.json com os campos do documento e o custo, codigo 0"]
    FIM -->|"Falha"| AB["abortar_run: status abortado com erro_classe e erro_mensagem, codigo 1"]
    FIM -->|"outra excecao"| AB2["abortar_run como terminal, mensagem Tipo: texto, e a excecao sobe"]
```

1. **Parâmetros.** `fonte` é opcional e vale `copom`, `cvm` ou `b3`; sem ela, as três rodam em sequência, um run por fonte. `--dev` escolhe o documento de referência no lugar do download.
2. **Credenciais.** O `.env` da raiz do repositório é carregado e as quatro variáveis de `.env.example` são conferidas: as três do Supabase, por `persistencia.criar_repositorio_supabase`, e a `GOOGLE_API_KEY`, por `llm.conferir_credenciais`. O banco recebe um `select 1` sem retentativa. Se algo falhar, a execução para com a mensagem na tela e código de saída 1, **sem criar linha em `runs`** e sem chamar `abortar_run`.
3. **Checkpointer.** `PostgresSaver` em conexão própria (`autocommit`, `dict_row`, `search_path = langgraph`). O `setup()` cria as tabelas na primeira execução.
4. **`run_id` e manifest.** O `run_id` tem o formato `<fonte>_<AAAAMMDDTHHMMSSZ>_<6 hex>`, por exemplo `copom_20261006T150000Z_9f2c1a`, e o instante é o mesmo do `timestamp` do manifest. Se a config for inválida, a execução para antes de existir linha em `runs`.
5. **Registro.** `criar_run` insere a linha em `runs` com status `em_andamento` (sem efeito se o `run_id` já existe) e o `manifest.json` local é gravado em seguida.
6. **Execução.** O grafo roda com `thread_id = run_id`. Os recursos vão em `config["configurable"]`: `repositorio`, `raiz` (raiz do repositório, de onde vêm `prompts/` e `config/`), `raiz_dados` (`data/`), `llm` (o `ClienteLLM` do run), `pasta_referencia` (`tests/fixtures/referencia/`) e, nos testes, `cliente_http`. Cada run recebe um `ClienteLLM` novo, para que o custo acumulado seja só daquele run. O `__main__.py` roda o grafo com `stream` e escreve no terminal o início e o fim de cada etapa (ver "Linhas de etapa no terminal").
7. **Fim.** Concluído ou abortado, como descrito abaixo.

O código de saída é 0 para run concluído e 1 para run abortado ou para erro antes de existir o run.

## O grafo

```
START -> (modo normal) coleta     -> leitura -> extract -> validate -> END
      -> (modo dev)    referencia -> leitura -> extract -> validate -> END
                                                  ^           |
                                                  +-----------+  claim reprovado e rodada < 3
```

- A aresta condicional de `START` lê `state.manifest.modo`: `dev` entra pelo nó `referencia`, e `normal` entra pelo nó `coleta` (E0).
- As duas entradas entregam um `DocumentoFonte` em `documento_fonte` e seguem para o nó `leitura` (E1), que entrega o `DocumentoProcessado` em `documento`.
- O nó `extract` (E2) coloca o FactSheet não assinado no estado, e o nó `validate` (E3) o confere. Sem claim reprovado, o FactSheet é assinado e o grafo termina. Com claim reprovado, `depois_da_validacao` volta ao `extract` enquanto `rodada < RODADAS_EXTRACAO` (3); na terceira rodada, levanta `FalhaTerminal` com os claims e os motivos. O loop está detalhado em `docs/fluxos/extracao.md`.
- O grafo termina na E3: a E4 em diante ainda não existe no grafo.
- `K = 2` é uma constante de `graph.py`. O valor lido no início do run entra no manifest.
- `construir_grafo(checkpointer=None)` aceita qualquer checkpointer do LangGraph. O `__main__.py` passa o `PostgresSaver`, e os testes passam o `InMemorySaver`.
- `PipelineState` tem `run_id`, `manifest`, `documento_fonte` (entradas), `documento` (E1), `factsheet` (não assinado na E2, assinado na E3) e os campos do loop E2/E3: `rodada`, `propostas`, `pendencias` e `correcoes`.
- O `thread_id` é o `run_id`. O formato `<run_id>:<celula_id>` vale a partir das células, que ainda não existem.

## Linhas de etapa no terminal

`_rodar_grafo` roda o grafo com `stream_mode=["tasks", "values"]`: cada evento de tarefa vira uma linha e o último `values` é o estado final, o mesmo que o `invoke` devolveria.

```
run cvm_20261012T122310Z_13e2d5 (dev)
etapa referencia: iniciada
etapa referencia: concluída
etapa leitura: iniciada
...
concluído: cvm_fato_relevante_24708_1574050 (53d8fb099247), 7 claims, 11074 tokens, R$ 0.1578
```

- O nome é o do nó em `graph.py` (`coleta`, `referencia`, `leitura`, `extract`, `validate`). No loop E2/E3, `extract` e `validate` aparecem uma vez por rodada.
- A etapa que falha não tem a linha `concluída`: a exceção sobe e a linha seguinte é `abortado (<classe>): <mensagem>`.
- O dashboard (`docs/fluxos/dashboard.md`) lê essas linhas para mostrar em que etapa o run está.

## Manifest

`manifesto.montar(raiz, run_id, timestamp, modo, fonte, k)` lê estes arquivos da raiz do repositório:

| Campo do manifest | Origem |
| ----------------- | ------ |
| `level_specs` | chave `versao` de `config/levels/<nivel>.yaml`. Se o arquivo traz `nivel`, ele precisa ser igual ao nome do arquivo. |
| `glossario_versao` | chave `versao` de `config/glossario.yaml`. |
| `modelos` | mapa `modelos` de `config/models.yaml` (nó para snapshot). Sem a chave, fica vazio. A chave `versao` é obrigatória. |
| `prompts` | linha `{# versao: N #}` no início de cada `prompts/*.j2`. Template vazio é ignorado e não entra em `prompts` nem em `arquivos_sha256`. Template com conteúdo e sem a linha de versão para o run. |
| `arquivos_sha256` | SHA-256 de cada arquivo lido, com `\r\n` normalizado para `\n`, chaveado pelo caminho relativo com `/`. |
| `K` | constante de `graph.py`. |

`versao` precisa ser um inteiro maior ou igual a 1. Arquivo ausente, YAML inválido, arquivo vazio ou `versao` inválida levantam `FalhaTerminal` com o caminho do arquivo, antes de existir linha em `runs`.

Os campos do documento (`doc_id`, `doc_sha256`, `url_origem`, `coletado_em`, `coleta_id`) começam vazios e são preenchidos quando o `DocumentoFonte` é entregue: em `runs`, pela E0 ou pelo `ingestao/referencia.py`, e no `manifest.json`, no fechamento do run concluído.

## Run concluído

`concluir_run` atualiza `runs` para `status = 'concluido'` com `where run_id = ... and status = 'em_andamento'` e, só depois, regrava o `manifest.json` com os campos do documento e o custo do run (`custo_total_tokens` e `custo_total_brl`, acumulados pelo `ClienteLLM`). Se o update não alterar exatamente uma linha, a função levanta `FalhaTerminal`.

No terminal, a última linha do run traz o documento, os claims do FactSheet e o custo:

```
concluído: cvm_fato_relevante_24708_1574050 (53d8fb099247), 7 claims, 11074 tokens, R$ 0.1578
```

Em `runs`, o custo é somado na mesma instrução que grava cada span (`docs/fluxos/extracao.md`, Custo).

## Run abortado

Toda `Falha` que sobe do grafo (E0, referência do `--dev`, E1, E2 e E3) é registrada por `persistencia.abortar_run(repositorio, raiz_dados, manifest, classe, mensagem)`:

1. A mensagem aparece na tela (stderr) antes de qualquer gravação.
2. Com `com_retentativa`, o `abortar_run` faz o update em `runs`: `status = 'abortado'`, `erro_classe` e `erro_mensagem`, `where run_id = ... and status = 'em_andamento'`. Os campos de documento não são tocados: podem estar vazios se o run abortou antes de a E0 entregar o documento.
3. O mesmo erro é gravado em `data/runs/<run_id>/manifest.json`, com o custo acumulado até o aborto.
4. O código de saída é 1.

As constraints de `runs` são respeitadas: `status = 'abortado'` se, e somente se, `erro_classe` não é nulo, e `erro_classe` e `erro_mensagem` são nulos juntos. Mensagem vazia vira `sem mensagem`.

| Situação | Resultado |
| -------- | --------- |
| `Falha` (retriável esgotada ou terminal) no grafo | Run `abortado` com `falha.classe` e `falha.mensagem`. |
| Exceção que não é `Falha` (bug) | Run `abortado` como `terminal`, mensagem `NomeDoTipo: texto`, e a exceção sobe com o traceback. |
| Run já `concluido` ou `abortado` | O update não altera nada: o primeiro estado final é mantido. |
| Supabase inacessível no update (retentativas esgotadas) | O erro fica só no `manifest.json`, com `status` `em_andamento`, `erro_classe` e `erro_mensagem` (a mensagem original mais a nota `status não gravado no Supabase`). A linha de `runs` segue `em_andamento`. A falha de gravação não é levantada e não esconde o erro original. |
| Falha antes de existir o run (credencial, conexão, config ou checkpointer) | Mensagem na tela e código 1. Nenhuma linha em `runs` e nenhuma chamada a `abortar_run`. |
| Falha ao gravar o fechamento de um run concluído | Mensagem na tela e código 1. A linha segue `em_andamento` e o `manifest.json` mantém o status gravado no início. |

A classificação dos erros da E0 e do `--dev` está em `docs/fluxos/ingestao.md`, seção "Classificação de erros".

## Arquivos gravados por um run

```
data/runs/<run_id>/
├── manifest.json            # RunManifest, status, erro e custo do run
├── documento.json           # E1: DocumentoProcessado
├── spans/<span_id>.json     # E2: uma chamada de LLM por arquivo
├── validacoes/<rodada>.json # E3: resultado de cada rodada
└── factsheet.json           # E3: FactSheet assinado, sem a tabela_numeros
```

O `manifest.json` contém os campos do `RunManifest` (em JSON) e, ao lado deles, `status`, `erro_classe` e `erro_mensagem`. Só o processo principal grava esse arquivo. Todos os arquivos são gravados num arquivo temporário renomeado no fim, e sempre depois de gravar no Supabase. Um run abortado tem só os arquivos das etapas que chegaram a gravar.

## Como rodar

```
pip install -r requirements.txt
cp .env.example .env        # preencher as quatro variáveis
python -m sunontent                  # modo normal: copom, cvm e b3 em sequência, um run por fonte
python -m sunontent --dev            # as três fontes com o documento de referência, sem download
python -m sunontent copom [--dev]    # só uma fonte
```

Sem a fonte, as três rodam em sequência, cada uma com o seu `run_id`. Se um run abortar, os seguintes continuam e o código de saída é 1. Rode a partir da raiz do repositório. O modo `--dev` precisa de `tests/fixtures/referencia/<fonte>.pdf` e `<fonte>.json`; sem eles, o run é abortado com a mensagem do arquivo ausente.

## Testes

```
python -m pytest
```

`tests/test_main_graph.py` cobre o manifest, a escolha da entrada do grafo, os dois modos, o loop E2/E3, as linhas de etapa no terminal e os caminhos de erro: falha da E0, bug, referência ausente, hash divergente, config inválida, Supabase fora no aborto, run concluído não sobrescrito, credenciais ausentes, claim que não converge e extractor sem claims. Os testes usam o repositório em memória e o LLM simulado (`tests/conftest.py`), o `InMemorySaver` e `tmp_path`; não acessam internet, o Gemini nem o Supabase do site.
