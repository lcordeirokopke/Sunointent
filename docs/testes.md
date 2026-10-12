# Testes

Os testes ficam em `tests/` e rodam com `pytest` a partir da raiz do repositório. Hoje cobrem a E0 (coleta dos PDFs de Copom, CVM e B3), a E1 (leitura), a E2 (extractor e ancoragem), a E3 (validador de âncoras), o cliente LLM, o manifest do run, o grafo e o `__main__`.

## Para que servem

Garantem que a coleta e o registro do run se comportem corretamente, principalmente nos casos de erro, que são difíceis de provocar na rede real:

- página de erro no lugar do PDF: tenta de novo e, se persistir, aborta sem gravar nada;
- PDF acima de 50 MiB: aborta;
- Supabase fora do ar ou resposta perdida depois do commit: o run é fechado de forma consistente e nada é gravado em duplicidade;
- documento de referência do modo `--dev` com hash diferente: o run para com mensagem clara.

## O que é simulado e o que roda de verdade

Quase tudo é simulado. O código do projeto roda de verdade; o mundo externo (internet, banco e disco) é substituído.

| Parte | Como é simulada | Onde |
|---|---|---|
| Internet (BCB, CVM, B3) | `httpx.MockTransport` devolve respostas prontas de `tests/fixtures/http/`, inclusive 503, 429, timeout e 404 | `test_ingestao.py` |
| Supabase | `RepositorioMemoria`: classe em memória com as mesmas regras do banco usadas pela E0, que também injeta falhas por método | `conftest.py` |
| Storage do Supabase | `_StorageFalso`: só devolve o erro que o teste pede | `test_ingestao.py` |
| Espera do backoff | `monkeypatch` troca `retentativa._dormir` por uma lista; o teste confere o tamanho da lista e que as esperas crescem, sem esperar de verdade | `conftest.py` |
| Disco | `tmp_path` do pytest no lugar de `data/` | todos |
| Checkpointer do LangGraph | `InMemorySaver` | `test_main_graph.py` |
| Gemini API | `ProviderFalso`: devolve respostas prontas em ordem (JSON, `RespostaBruta` ou exceção) e guarda os prompts recebidos; `cliente_falso` monta o `ClienteLLM` com ele | `conftest.py` |

Rodam de verdade: `copom.py`, `cvm.py`, `b3.py`, `comum.py`, `retentativa.py`, `manifesto.py`, `graph.py` e `__main__.py` (parsing, validação de `%PDF-`, hash, limite de tamanho, classificação dos erros, ordem das gravações), o grafo LangGraph, a leitura e o hash dos arquivos de `config/` e `prompts/` (copiados para o `tmp_path`) e a escrita de arquivos no disco dentro do `tmp_path`.

Usam serviços reais só `test_persistencia_supabase.py`, contra o Supabase **local** do CLI, e o teste de cobertura com marker `gemini`, que chama a Gemini API.

## Proteções

- `conftest.py` aborta o pytest antes da coleta se `SUPABASE_URL` ou `SUPABASE_DB_URL` apontarem para um host diferente de `localhost`, `127.0.0.1` ou `::1`. Os testes nunca tocam o banco hospedado.
- O `.env` não é carregado pelos testes.
- Marker `supabase`: identifica os testes de integração com o Supabase local.
- Marker `gemini`: identifica os testes que chamam a Gemini API e gastam tokens. Ficam pulados, a menos que o `-m` os peça. Leem só a `GOOGLE_API_KEY` do `.env`, sem carregar o resto do arquivo.

## Limite do que provam

Como a internet é simulada, nenhum teste confirma que os sites reais continuam respondendo no formato esperado. Se o BCB, a CVM ou a B3 mudarem o formato da listagem, os testes continuam passando e a coleta real quebra. Essa checagem só acontece rodando `python -m sunontent <fonte>` de verdade.

Os documentos de `tests/fixtures/referencia/` estão congelados: Copom ata 281, CVM `cvm_fato_relevante_24708_1574050` e B3 2T26. O corpo do download simulado são os bytes desses PDFs, e as listagens de `tests/fixtures/http/` apontam para essas mesmas publicações.

## Estrutura

```
tests/
├── conftest.py                    # trava de host, marker, RepositorioMemoria, sem_espera
├── test_ingestao.py               # E0: HTTP simulado, repositório em memória
├── test_numeros.py                # parser de número brasileiro
├── test_extracao.py               # E1: leitura, normalização, chunks, metadados, números e gravação
├── test_extractor.py              # E2, E3, cliente LLM e cobertura com a Gemini API (marker gemini)
├── test_main_graph.py             # manifest, grafo (E0 a E3) e __main__
├── test_persistencia_supabase.py  # integração com o Supabase local
└── fixtures/
    ├── http/                      # respostas simuladas por fonte (listagem e página de erro)
    └── referencia/                # documentos congelados por fonte, o snapshot <fonte>.documento.json e a anotação <fonte>.anotacao.yaml
```

## E1: leitura

`test_numeros.py` fixa o parser com igualdade exata em `Decimal`: cada formato aceito (`12,5%`, `R$ 1,2 bi`, `0,50 p.p.`, `47 bps`, `1.938,6`) e cada formato recusado (`1,234.56`, `12.5%`, `1.93`, `3.1`, `0.500`), que precisa levantar `NumeroNaoPrevisto` em vez de ser interpretado.

`test_extracao.py` roda a E1 de verdade sobre os três PDFs de `tests/fixtures/referencia/`:

- união de fragmentos: o que o PDF parte (`R$31` e `5,2`) se une, e rótulo de tabela, valor e célula vizinha não;
- normalização: cabeçalho, rodapé e número de página, hifenização e caracteres invisíveis;
- chunks por seção de cada fonte, cobertura do texto inteiro e consistência com o texto das páginas;
- metadados de cada fonte e aborto quando o padrão não existe;
- números conferidos à mão no PDF (`R$315,2 milhões`, `22,0%`, `13,75%`, `R$ 390.000.000,00`) e identificadores fora da tabela;
- hash diferente, arquivo ausente, PDF sem camada de texto e arquivo que não é PDF: aborto terminal com mensagem clara;
- snapshot `<fonte>.documento.json`: a saída precisa ser exatamente igual. Depois de uma mudança intencional, `REGERAR_DOCUMENTO=1 pytest tests/test_extracao.py` regrava os arquivos, e a diferença deve ser revisada.

`test_main_graph.py` confere a E1 no grafo nos dois modos, a cópia local `documento.json` e o aborto do run quando a E1 falha. O teste de `test_persistencia_supabase.py` confere a gravação transacional e idempotente de `documento_paginas`, `chunks` e `numeros`.

## E2 e E3: extração e validação

`test_extractor.py` roda a ancoragem e o validador sobre a saída real da E1 dos três documentos de referência, e o extractor com o `ProviderFalso`.

### Anotação de cobertura

`<fonte>.anotacao.yaml` lista, à mão, os fatos que o extractor precisa transformar em claim (12 no Copom, 6 na CVM, 15 na B3). Cada fato tem um ou mais trechos alternativos, porque o mesmo fato pode estar em mais de um lugar do documento (resumo de destaques e texto detalhado, por exemplo), cada um com os números que os claims daquele trecho precisam trazer. Um fato está coberto quando, em alguma alternativa, algum claim tem âncora que cruza o trecho e os claims que cruzam o trecho, somados, trazem todos os números listados.

- `test_anotacao_bate_com_o_documento`: cada trecho anotado existe uma única vez numa página, com os números listados dentro dele, e o `doc_sha256` bate com o documento.
- `test_cobertura_acusa_fato_e_numero_ausentes`: o critério acusa fato sem claim e claim sem o número.

### Ancoragem (E2)

- espaço no lugar de quebra de linha e aspas ou travessões trocados resolvem, e o `literal_fonte` gravado é o texto exato do documento;
- paráfrase não resolve; trecho que atravessa a página é recusado;
- trecho repetido: dentro do chunk citado resolve; fora dele é ambíguo;
- número fora do trecho é recusado, com a lista dos números que estão nele; número repetido no trecho usa cada ocorrência uma vez;
- `ancorar` separa as propostas que não resolvem, com o motivo.

### Validador (E3)

- claim resolvido passa;
- âncora deslocada ou em página inexistente, `pp` no lugar de `pct`, número de outro trecho, número fora da tabela e id repetido reprovam;
- texto e números precisam dizer o mesmo: número listado que o texto não menciona, número no texto fora da lista (inclusive inventado) e número em formato não previsto reprovam.

### Extractor (E2)

- o prompt traz o documento por bloco e por página, sem a linha de versão do template, e sem a seção de correção na primeira rodada;
- bloco que atravessa página é separado por página;
- a primeira rodada renumera os ids na ordem; a correção troca só os claims pedidos e mostra o motivo no prompt.

### Cliente LLM

- custo em BRL pelo preço e pela cotação;
- resposta fora do schema reinjeta o erro de validação e conta os tokens; 3 respostas inválidas viram terminal;
- falha de infraestrutura repete com espera crescente, sem gastar o contador de parse; 4 falhas viram terminal;
- bloqueio de conteúdo e erro 4xx são terminais, sem repetir;
- nó sem modelo e modelo sem preço são terminais; o `config/models.yaml` do repositório tem preço para cada modelo;
- sem `GOOGLE_API_KEY`, erro terminal.

### Persistência

- FactSheet não assinado não é gravado;
- span repetido não soma o custo de novo e tem cópia local.

### Gemini de verdade

`test_gemini_cobre_os_fatos_anotados` (marker `gemini`) roda o `__main__` em modo `--dev` para cada fonte, com a Gemini API de verdade e o repositório em memória, e exige o FactSheet assinado e todos os fatos anotados cobertos. Imprime claims, tokens, custo e rodadas. Custa da ordem de R$ 0,15 a R$ 0,60 por documento e leva alguns minutos. Como a resposta do modelo varia, uma falha aqui pede para ler os claims gerados (`data/runs/<run_id>/factsheet.json` no `tmp_path` do teste) antes de mexer no prompt ou na anotação.

### No grafo

`test_main_graph.py` confere, com o `ProviderFalso`: o FactSheet assinado e gravado, a rodada em `validacoes_ancora`, o span e o custo no run e no manifest local; o claim recusado que volta para a E2 com o motivo no prompt e é corrigido; o claim que não converge e aborta o run depois de 3 rodadas; o extractor sem claims, que aborta; e o run que não começa sem a `GOOGLE_API_KEY`.

`test_persistencia_supabase.py` confere no Supabase local o span com o custo somado uma só vez em `runs`, a rodada em `validacoes_ancora` e o `claim_numeros` ligado à sua linha em `numeros`.

## conftest.py

Não contém testes, só infraestrutura compartilhada:

- `pytest_configure`: registra os markers `supabase` e `gemini` e aplica a trava de host.
- `pytest_collection_modifyitems`: pula os testes `gemini`, a menos que o `-m` os peça.
- `sem_espera` (autouse): troca a espera do backoff por uma lista que registra os tempos.
- `RepositorioMemoria`: substitui o Supabase. Guarda runs, documentos, objetos do bucket e coletas; aceita falhas injetadas por método e simula a perda de resposta depois do commit.
- `repositorio` e `run_id`: fixtures com um run `copom_...` já registrado.
- `referencia(fonte)`: devolve bytes e metadados do documento congelado, ou `None` se ainda não existir.
- `RepositorioMemoria` também guarda spans (sem somar duas vezes o mesmo), validações e FactSheets, e recusa número de claim que não está em `numeros`, como o Supabase.
- `ProviderFalso` e `cliente_falso`: o LLM simulado.

## test_ingestao.py

Testes de unidade da E0. Os parametrizados rodam uma vez por fonte (copom, cvm, b3).

### Caminho feliz

- `test_coleta_grava_supabase_depois_copia_local`: a coleta devolve o `DocumentoFonte` correto, grava o PDF no bucket e o registro em documentos/coletas, e só depois cria a cópia local (PDF + `.json` com os metadados).
- `test_referencia_bate_doc_sha256_e_doc_id`: o PDF congelado produz o mesmo `doc_sha256` e `doc_id` do `.json` de referência.
- `test_user_agent_explicito`: todas as requisições (listagem e PDF) enviam o `User-Agent` do projeto.

### Formato e tamanho (abortam sem gravar nada)

- `test_pagina_de_erro_persistente_aborta_sem_gravar`: se o "PDF" for sempre uma página HTML de erro, esgota as tentativas, levanta `FalhaTerminal` e não grava nada.
- `test_pagina_de_erro_temporaria_e_repetida`: se a página de erro vier só na primeira vez, a segunda tentativa funciona e o aviso aparece no stderr.
- `test_progresso_aparece_no_terminal`: o download em pedaços imprime as mensagens de progresso (localizando, baixando, MB baixados, gravando).
- `test_content_type_e_ignorado_so_os_primeiros_bytes_contam`: um PDF servido como `text/html` é aceito, porque vale só o `%PDF-` inicial.
- `test_content_length_acima_do_limite_aborta_sem_gravar`: `Content-Length` maior que o limite aborta sem gravar.
- `test_corpo_acima_do_limite_sem_content_length_aborta_sem_gravar`: corpo que ultrapassa o limite durante o download aborta sem gravar.
- `test_documento_no_limite_exato_e_aceito`: um PDF com exatamente o tamanho máximo é aceito.

### Erros da fonte

- `test_falha_retriavel_no_download_e_repetida`: timeout, erro de conexão, 503 e 429 são repetidos; com duas falhas e depois sucesso, a coleta termina e as esperas crescem (backoff exponencial).
- `test_falha_retriavel_esgotada_vira_terminal_sem_gravar`: se a falha retriável se repete em todas as tentativas, vira `FalhaTerminal` sem gravar nada.
- `test_falha_retriavel_na_listagem_tambem_e_repetida`: um 502 na listagem do Copom também é repetido.
- `test_404_no_pdf_e_terminal_sem_repetir`: 404 no PDF é terminal, não repete e não grava.
- `test_copom_sem_ata_na_listagem_e_terminal`: listagem do Copom vazia gera `FalhaTerminal` "ata mais recente não localizada".
- `test_b3_sem_release_na_listagem_e_terminal`: listagem da B3 vazia gera `FalhaTerminal` "release de resultados não localizado".
- `test_b3_envia_categoria_e_idioma_na_listagem`: a B3 recebe um POST com categoria, idioma `pt_BR` e `published: true`.
- `test_cvm_usa_ano_anterior_se_o_arquivo_do_ano_nao_existe`: se o arquivo IPE do ano corrente der 404, a CVM usa o do ano anterior.
- `test_cvm_desempata_pelo_numero_do_protocolo`: entre fatos relevantes do mesmo dia, escolhe o de maior número de protocolo e ignora outras categorias.

### Supabase: reaproveitamento, retentativa e resposta perdida

- `test_segunda_coleta_do_mesmo_documento_reaproveita`: coletar o mesmo documento em outro run não reenvia o PDF, mantém um só documento, cria a segunda coleta e não reescreve o PDF local.
- `test_objeto_ja_no_bucket_sem_linha_em_documentos_e_reaproveitado`: se o processo morreu entre o bucket e a transação, o objeto já enviado é reaproveitado.
- `test_resposta_da_transacao_perdida_rele_e_nao_repete`: se a transação grava mas a resposta se perde, o código relê a coleta do run em vez de gravar de novo.
- `test_transacao_que_nao_gravou_e_repetida`: se a transação falha antes do commit, é repetida.
- `test_falha_retriavel_no_bucket_e_repetida`: falhas retriáveis no envio ao bucket são repetidas.
- `test_gravacao_recusada_pelo_banco_aborta_sem_copia_local`: recusa terminal do banco aborta e não cria a cópia local.
- `test_run_ja_vinculado_e_terminal`: um run só aceita uma coleta; a segunda tentativa é terminal.

### Cópia local

- `test_arquivo_local_em_uso_pede_para_fechar`: se o Windows impedir a troca do arquivo por estar em uso, a mensagem pede para fechá-lo e nenhum temporário fica para trás. O Supabase já tem os registros.

### RepositorioSupabase (Storage, sem rede)

- `test_storage_envia_como_pdf_sem_upsert`: o upload usa `content-type: application/pdf` e não sobrescreve.
- `test_storage_classifica_erros`: "já existe" (409/400) é aceito como reaproveitamento; 503 é retriável; 413 e 403 são terminais.
- `test_storage_timeout_e_retriavel`: timeout do Storage é retriável.

### Nó da E0

- `test_no_coleta_coloca_documento_fonte_no_estado`: o nó `coleta` devolve só a chave `documento_fonte`, com o documento correto.

## test_main_graph.py

Cobre o manifest, o grafo e o `__main__`, com repositório e checkpointer em memória. A fixture `raiz` copia `config/` e `prompts/` e cria uma referência de Copom dentro do `tmp_path`.

### Manifest

- `test_manifest_le_versoes_e_hashes_da_config`: o manifest lê as versões dos LevelSpec e do glossário e o hash de cada arquivo de config.
- `test_hash_ignora_fim_de_linha`: trocar `\n` por `\r\n` não muda o hash.
- `test_template_com_conteudo_exige_cabecalho_de_versao`: template com conteúdo e sem `{# versao: N #}` gera erro apontando o arquivo.
- `test_template_com_cabecalho_entra_no_manifest`: template com cabeçalho entra com sua versão e seu hash.
- `test_yaml_vazio_aponta_o_arquivo`: YAML de config vazio gera erro com o nome do arquivo.

### Grafo

- `test_entrada_escolhida_pelo_modo`: modo `dev` entra por `referencia`; modo `normal` entra por `coleta`.

### `__main__` em modo normal

- `test_modo_normal_conclui_e_grava_run_e_manifest`: retorna 0, o `run_id` segue o formato `copom_<data>_<6 hex>`, o run fica `concluido` com coleta vinculada e o manifest local é gravado.
- `test_falha_da_coleta_aborta_o_run`: com a coleta falhando (tudo 404), retorna 1, o run fica `abortado` com a classe e a mensagem do erro, e nada é gravado em documentos/coletas.
- `test_falha_terminal_levantada_pelo_no_aborta_com_classe_e_mensagem`: uma `FalhaTerminal` dentro do nó aborta o run com a mesma mensagem no banco e no manifest local.
- `test_erro_inesperado_fecha_o_run_e_propaga`: uma exceção inesperada (bug) fecha o run como abortado e a exceção continua propagando.

### `__main__` em modo `--dev`

- `test_modo_dev_usa_a_referencia_sem_baixar_nem_criar_coleta`: usa o documento de referência, não baixa nada, não cria coleta e não escreve `data/sources`.
- `test_modo_dev_sem_referencia_para_com_mensagem_clara`: sem o PDF de referência, aborta citando `copom.pdf`.
- `test_modo_dev_com_hash_diferente_para_com_hashes`: com o PDF alterado, aborta mostrando o hash esperado e o hash obtido.

### Falhas antes e depois de o run existir

- `test_config_invalida_nao_cria_linha_em_runs`: config inválida (`models.yaml` vazio) para antes de criar o run e de criar `data/`.
- `test_supabase_fora_no_aborto_deixa_o_erro_so_no_manifest_local`: se o Supabase estiver fora ao registrar o aborto, o erro fica só no manifest local, sem esconder a exceção original.
- `test_run_concluido_nao_e_sobrescrito_pelo_aborto`: um run já concluído não vira abortado.
- `test_run_abortado_mantem_a_primeira_mensagem`: um segundo aborto não troca a mensagem do primeiro.
- `test_credenciais_ausentes_param_antes_de_tudo`: sem `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` e `SUPABASE_DB_URL`, a execução para com erro listando as três.
- `test_cli_recusa_fonte_desconhecida`: fonte inválida na linha de comando encerra; `cvm --dev` é lido corretamente.

## test_persistencia_supabase.py

Integração com o Supabase local do CLI (`supabase start`). Cobre o que o repositório em memória não reproduz: constraints, triggers de imutabilidade e o bucket `documentos`. Tem o marker `supabase` e é pulado se as três variáveis de ambiente não estiverem definidas ou se o Supabase local estiver fora do ar. A limpeza é feita com `supabase db reset`.

- `test_coleta_completa_e_reaproveitamento`: em dois runs seguidos, envia o documento, registra a coleta e confere que o documento é reaproveitado; um envio direto de objeto existente é aceito sem sobrescrever; um run já vinculado tem a transação recusada; e `update` em `documentos` e `delete` em `coletas` são barrados pelo trigger de imutabilidade.

## Como rodar

```
pytest                      # tudo; os testes do Supabase local são pulados se ele não estiver no ar
pytest -m "not supabase"    # só os testes simulados
pytest -m supabase          # só a integração com o Supabase local
pytest -m gemini            # só a cobertura com a Gemini API de verdade (gasta tokens)
```
