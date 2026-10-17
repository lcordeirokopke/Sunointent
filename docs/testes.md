# Testes

Os testes ficam em `tests/` e rodam com `pytest` a partir da raiz do repositório. Hoje cobrem a E0 (coleta dos PDFs de Copom, CVM e B3), a E1 (leitura), a E2 (extractor e ancoragem), a E3 (validador de âncoras), a E4 (LevelSpec, glossário e plano editorial), a E5 de uma célula (pacote, redator, montagem e gravação da peça), a E6 e a E7 de uma célula (scorecard, Router e o refinement loop no subgrafo da célula), as métricas determinísticas, o juiz (M6) e a sua calibração, o cliente LLM, o manifest do run, o grafo, o `__main__`, o dashboard (`app/`) e as consultas de leitura do dashboard.

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
| Supabase | `RepositorioMemoria`: classe em memória com as mesmas regras do banco usadas pelo pipeline (E0 a E7), que também injeta falhas por método | `conftest.py` |
| Leitura do Supabase pelo dashboard | `ConsultaFalsa`: devolve runs e peças prontos ou levanta a falha pedida | `test_app.py` |
| Processo do pipeline iniciado pelo dashboard | script Python que escreve as mesmas linhas do `__main__.py` | `test_app.py` |
| Storage do Supabase | `_StorageFalso`: só devolve o erro que o teste pede | `test_ingestao.py` |
| Espera do backoff | `monkeypatch` troca `retentativa._dormir` por uma lista; o teste confere o tamanho da lista e que as esperas crescem, sem esperar de verdade | `conftest.py` |
| Disco | `tmp_path` do pytest no lugar de `data/` | todos |
| Groq | `httpx.MockTransport` devolve as respostas do Groq, inclusive os erros HTTP e o JSON fora do schema; o juiz nos testes de `llm_judge` usa o `ProviderFalso` | `test_juiz.py` |
| Gemini API | `ProviderFalso`: devolve respostas prontas em ordem (JSON, `RespostaBruta` ou exceção) e guarda os prompts recebidos; `cliente_falso` monta o `ClienteLLM` com ele, para o extractor e o adapter; `resposta_plano` monta a resposta do planejador | `conftest.py` |

Rodam de verdade: `copom.py`, `cvm.py`, `b3.py`, `comum.py`, `retentativa.py`, `manifesto.py`, `graph.py` e `__main__.py` (parsing, validação de `%PDF-`, hash, limite de tamanho, classificação dos erros, ordem das gravações), o grafo LangGraph, o servidor do dashboard (`app/servidor.py` e `app/execucao.py`, com um processo filho de verdade), a leitura e o hash dos arquivos de `config/` e `prompts/` (copiados para o `tmp_path`) e a escrita de arquivos no disco dentro do `tmp_path`.

Usam serviços reais só `test_persistencia_supabase.py`, contra o Supabase **local** do CLI, o teste de cobertura com marker `gemini`, que chama a Gemini API, e a calibração do juiz com marker `judge`, que chama o Groq.

## Proteções

- `conftest.py` aborta o pytest antes da coleta se `SUPABASE_URL` ou `SUPABASE_DB_URL` apontarem para um host diferente de `localhost`, `127.0.0.1` ou `::1`. Os testes nunca tocam o banco hospedado.
- O `.env` não é carregado pelos testes.
- Marker `supabase`: identifica os testes de integração com o Supabase local.
- Marker `gemini`: identifica os testes que chamam a Gemini API e gastam tokens. Ficam pulados, a menos que o `-m` os peça. Leem só a `GOOGLE_API_KEY_PRINC` e a `GOOGLE_API_KEY_SEC` do `.env`, sem carregar o resto do arquivo, e pulam se a `GOOGLE_API_KEY_PRINC` estiver ausente.
- Marker `judge`: identifica a calibração do juiz, que chama o Groq e gasta tokens. Fica pulada, a menos que o `-m` a peça. Lê só a `GROQ_API_KEY` do `.env` e pula se ela estiver ausente.

## Limite do que provam

Como a internet é simulada, nenhum teste confirma que os sites reais continuam respondendo no formato esperado. Se o BCB, a CVM ou a B3 mudarem o formato da listagem, os testes continuam passando e a coleta real quebra. Essa checagem só acontece rodando `python -m sunontent <fonte>` de verdade.

Os documentos de `tests/fixtures/referencia/` estão congelados: Copom ata 281, CVM `cvm_fato_relevante_16993_1575717` (fato relevante conjunto Sabesp e EMAE) e B3 2T26. O corpo do download simulado são os bytes desses PDFs, e as listagens de `tests/fixtures/http/` apontam para essas mesmas publicações.

## Estrutura

```
tests/
├── conftest.py                    # trava de host, marker, RepositorioMemoria, sem_espera
├── test_ingestao.py               # E0: HTTP simulado, repositório em memória
├── test_numeros.py                # parser de número brasileiro
├── test_extracao.py               # E1: leitura, normalização, chunks, metadados, números e gravação
├── test_extractor.py              # E2, E3, cliente LLM e cobertura com a Gemini API (marker gemini)
├── test_levelspec.py              # LevelSpec: leitura e validação de config/levels/, blocos analogia e vocabulario, avisos e instrucoes_redator
├── test_glossario.py              # glossário: validação, casamento de termos, analogia e flexões
├── test_adaptacao.py              # E4: pré-filtro, complemento, gate, planejador e gravação
├── test_texto.py                  # contagem de palavras e de caracteres, sílabas e frases do texto da peça
├── test_metrics.py                # M1 a M5, sem LLM e sem banco
├── test_schemas.py                # schemas do redator, da peça, do scorecard e da célula
├── test_sintese.py                # E5 de uma célula: pacote, redator (LLM simulado), montagem, M7 (plan_contract), gravação e nó
├── test_router.py                 # E6 e E7 de uma célula: scorecard, Router, relatório corretivo e o refinement loop
├── test_juiz.py                   # juiz (M6): Groq, contexto, prompt, completude, violações, matriz de níveis e calibração (marker judge)
├── test_main_graph.py             # manifest, grafo (E0 a E4 e o fan-out com as células simuladas) e __main__
├── test_fanout.py                 # fan-out das 9 células: Send, max_concurrency, isolamento de falhas, fechamento, terminal e trava do custo
├── test_app.py                    # dashboard: linhas do terminal, API, slide em SVG e claims descartados na E3, com processo e consulta simulados
├── test_persistencia_supabase.py  # integração com o Supabase local
└── fixtures/
    ├── http/                      # respostas simuladas por fonte (listagem e página de erro)
    ├── juiz/                      # calibracao.yaml: as peças de calibração do juiz, metade com erro plantado
    └── referencia/                # documentos congelados por fonte, o snapshot <fonte>.documento.json, a anotação <fonte>.anotacao.yaml e os claims <fonte>.claims.json
```

## E1: leitura

`test_numeros.py` fixa o parser com igualdade exata em `Decimal`: cada formato aceito (`12,5%`, `R$ 1,2 bi`, `0,50 p.p.`, `47 bps`, `1.938,6`) e cada formato recusado (`1,234.56`, `12.5%`, `1.93`, `3.1`, `0.500`), que precisa levantar `NumeroNaoPrevisto` em vez de ser interpretado. Também fixa o ordinal de período (`1º tri` é `data`, `3º lugar` é `contagem`) e o leitor de datas (`numeros.datas`): cada formato com dia vira dia, mês e ano; mês sem dia, trimestre, lei e data inválida ficam de fora; e em "19/10 e 19 de outubro" a data por extenso não pega o mês de `19/10`. `numeros.datas_com_posicao` devolve as mesmas datas com o início e o fim da expressão, e `NumeroNaoPrevisto.posicao` aponta o início do trecho que `varrer` não leu.

`test_extracao.py` roda a E1 de verdade sobre os três PDFs de `tests/fixtures/referencia/`:

- união de fragmentos: o que o PDF parte (`R$31` e `5,2`) se une, e rótulo de tabela, valor e célula vizinha não;
- normalização: cabeçalho, rodapé e número de página, hifenização e caracteres invisíveis;
- versão em inglês: bloco final descartado (as páginas 4 a 6 do fato relevante da CVM), e inglês no meio do documento, documento todo em inglês e termo isolado em inglês mantidos;
- chunks por seção de cada fonte, cobertura do texto inteiro e consistência com o texto das páginas;
- unidade das células de tabela: cabeçalho e rótulo da linha, escala, fim no novo cabeçalho de colunas, parêntese que não é unidade, `exceto LPA` e linha de quantidade, e as células conferidas nos documentos de referência;
- metadados de cada fonte e aborto quando a data não existe; emissor da CVM com uma e com duas companhias, título em inglês, a primeira linha com letras e o aviso quando o cabeçalho não é reconhecido, e aborto só na página sem nenhuma letra;
- números conferidos à mão no PDF (`R$315,2 milhões`, `22,0%`, `13,75%`) e identificadores fora da tabela, como os CNPJs do fato relevante;
- hash diferente, arquivo ausente, PDF sem camada de texto e arquivo que não é PDF: aborto terminal com mensagem clara;
- snapshot `<fonte>.documento.json`: a saída precisa ser exatamente igual. Depois de uma mudança intencional, `REGERAR_DOCUMENTO=1 pytest tests/test_extracao.py` regrava os arquivos, e a diferença deve ser revisada.

`test_main_graph.py` confere a E1 no grafo nos dois modos, a cópia local `documento.json` e o aborto do run quando a E1 falha. O teste de `test_persistencia_supabase.py` confere a gravação transacional e idempotente de `documentos_processados`, `documento_paginas`, `chunks` e `numeros`, e que o banco recusa o documento processado de outro documento.

`test_extracao.py` também confere a gravação no repositório em memória, que segue a mesma regra do banco: a E1 só grava o documento vinculado ao run (`test_documento_que_nao_e_o_do_run_e_recusado`).

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
- número listado com a unidade da tabela (`3,2%` para a célula `3,2` do Copom) vale pela célula, e o claim passa na E3; com outra unidade (`3,2 p.p.`) é recusado;
- `ancorar` separa as propostas que não resolvem, com o motivo.

### Validador (E3)

- claim resolvido passa;
- âncora deslocada ou em página inexistente, `pp` no lugar de `pct`, número de outro trecho, número fora da tabela e id repetido reprovam;
- literal só de números (célula da tabela do Copom sem o rótulo) reprova, e o mesmo trecho com o rótulo da linha passa;
- data do texto fora do literal reprova: o ano acrescentado (`19 de outubro de 2026` contra `19/10`) e o dia ou mês trocado; a mesma data escrita de outra forma passa;
- texto e números precisam dizer o mesmo: número listado que o texto não menciona, número no texto fora da lista (inclusive inventado) e número em formato não previsto reprovam;
- data listada em `numeros` (o `2T25` do trecho da B3) reprova, e o mesmo claim sem ela passa.

### Extractor (E2)

- o prompt traz o documento por bloco e por página, sem a linha de versão do template, e sem a seção de correção na primeira rodada;
- bloco que atravessa página é separado por página;
- a primeira rodada renumera os ids na ordem; a correção troca só os claims pedidos e mostra o motivo no prompt;
- a correção mostra só os chunks em volta do claim recusado (o citado, o encontrado e os vizinhos), ou o documento inteiro se o chunk citado não existe, e pede o raciocínio `extractor_correcao`; a extração usa o raciocínio padrão do modelo.

### Cliente LLM

- custo em BRL pelo preço e pela cotação;
- resposta fora do schema reinjeta o erro de validação e conta os tokens; 3 respostas inválidas viram terminal;
- claim sem tag é resposta fora do schema: o extractor responde de novo, e o `Claim` também recusa `tags` vazia;
- a regra 7 do prompt descreve todos os tipos do `TipoClaim`, inclusive `evento_corporativo`;
- falha de infraestrutura repete com espera crescente, sem gastar o contador de parse; 4 falhas viram terminal;
- bloqueio de conteúdo e erro 4xx são terminais, sem repetir;
- nó sem modelo, modelo sem preço e nível de raciocínio inválido são terminais; o `config/models.yaml` do repositório tem preço para cada modelo (inclusive o do juiz, US$ 0,15 de entrada e US$ 0,60 de saída por milhão de tokens) e o raciocínio da correção do extractor, do planejador e do redator e o do juiz;
- sem `GOOGLE_API_KEY_PRINC`, erro terminal, mesmo com a `GOOGLE_API_KEY_SEC`; sem `GROQ_API_KEY`, erro terminal; o cliente monta as chaves na ordem PRINC e SEC, a SEC só entra se estiver preenchida, e o juiz vai só ao Groq, sem reserva;
- falha de infraestrutura ou chave recusada na PRINC passa para a SEC na mesma tentativa, sem esperar, e o span da falha leva o nome da chave;
- cada chamada começa pela PRINC, mesmo depois de uma passagem para a SEC;
- as duas chaves com falha de infraestrutura contam uma tentativa, com espera, e a seguinte recomeça pela PRINC; 4 tentativas viram terminal;
- as duas chaves recusadas são terminais, sem esperar;
- bloqueio de conteúdo e os demais erros 4xx na PRINC são terminais, sem chamar a SEC;
- o provider Gemini classifica 429 como retriável, 401, 403 e 400 de chave inválida como chave recusada, e os demais 400 como terminais.

### Persistência

- FactSheet não assinado não é gravado;
- span repetido não soma o custo de novo e tem cópia local.

### Gemini de verdade

`test_gemini_cobre_os_fatos_anotados` (marker `gemini`) roda o `__main__` em modo `--dev` para cada fonte, com a Gemini API de verdade e o repositório em memória, e exige o FactSheet assinado e todos os fatos anotados cobertos. Imprime claims, tokens, custo e rodadas, e os tokens, o custo e a duração de cada chamada (com `-s`). O run vai até a E4, com o planejador de verdade; as 9 células ficam simuladas, sem chamar o redator. Custa da ordem de R$ 0,15 a R$ 0,76 por documento na extração, mais R$ 0,07 a R$ 0,46 pelo plano, e leva de 15 a 20 minutos nas 3 fontes. Como a resposta do modelo varia, uma falha aqui pede para ler os claims gerados (`data/runs/<run_id>/factsheet.json` no `tmp_path` do teste) antes de mexer no prompt ou na anotação.

### No grafo

`test_main_graph.py` confere, com o `ProviderFalso`: o FactSheet assinado e gravado, a rodada em `validacoes_ancora`, o span e o custo no run e no manifest local; o claim recusado que volta para a E2 com o motivo no prompt e é corrigido; o claim que não converge e, depois de 3 rodadas, é descartado, com o motivo na linha da rodada 3 em `validacoes_ancora`, o run concluído, o FactSheet assinado só com o claim válido, a linha final com `1 descartado` e as linhas da etapa `descarte`; o run sem nenhum claim válido, que aborta na etapa `descarte` sem gravar FactSheet, com a quantidade e o motivo dos descartados na mensagem; o extractor sem claims, que aborta; e o run que não começa sem a `GOOGLE_API_KEY_PRINC`, mesmo com a `GOOGLE_API_KEY_SEC` preenchida.

`test_persistencia_supabase.py` confere no Supabase local o span com o custo somado uma só vez em `runs`, a rodada em `validacoes_ancora` e o `claim_numeros` ligado à sua linha em `numeros`.

### Claims descartados

`test_persistencia_supabase.py::test_descartados_sao_os_reprovados_na_ultima_rodada` confere no Supabase local a contagem de descartados da `ConsultaSupabase`, em `listar_runs` e em `detalhar_run`: só os reprovados na rodada `RODADAS_EXTRACAO` contam, e o run parado antes dela não tem descartados.

## E4: plano editorial

### LevelSpec

`test_levelspec.py` lê os 3 arquivos de `config/levels/` e confere os valores de `docs/fluxos/plano-editorial.md`, inclusive o contrato de formato (`max_claims` por formato, palavras do artigo, slides e caracteres do carrossel, duração, palavras por segundo, abertura e fechamento do roteiro, peso do papel e versão). Cada regra de validação aborta com o caminho do arquivo: `nivel` diferente do nome do arquivo, tag fora do vocabulário, tag nas duas listas, tag descartável que não é `metodologico`, `max_claims` de cada formato abaixo de 3 ou não inteiro (`"5"`, `5.0`, `true`), `max_claims` no topo do arquivo, formato menor com mais claims que um maior, faixa invertida, carrossel com menos de 3 slides, caractere de um papel ausente, abertura e fechamento que ocupam o roteiro todo, palavras por segundo zero, peso de papel zero, campo desconhecido (no topo e num formato), campo ausente e arquivo vazio ou ausente.

Os testes do bloco `analogia` e das instruções do redator conferem:

- os marcadores do Iniciante, e que Intermediário e Avançado não têm o bloco;
- `analogia` ausente no Iniciante e presente no Intermediário ou no Avançado: erro terminal; marcadores vazios, de uma palavra só, repetidos (sem maiúsculas e com espaços a mais) e campo desconhecido no bloco: erro terminal;
- o aviso `carrossel.max_claims` maior que `slides.max - 2` (6 claims e 5 slides de corpo possíveis, num arquivo reescrito) e nenhum aviso nos três níveis atuais (4, 5 e 5, os dois últimos exatamente no limite);
- o bloco `legibilidade`: só o Iniciante o tem (piso 30, 45 e 45 e meta de 18 palavras), piso sem um dos formatos e meta abaixo de 5 palavras: erro terminal, e o nível sem o bloco carrega;
- o bloco `sinal_ifl`: as faixas do artigo dos três níveis (45, 27 a 45 e abaixo de 27), `contem` com `min` inclusivo e `max` exclusivo, lacuna entre as faixas ou nível sem o bloco: erro terminal, e `min` maior ou igual a `max`: erro terminal;
- as palavras mínimas do artigo: ordem decrescente aborta (`carregar_todos`), valores iguais passam;
- `instrucoes_redator`: o orçamento e o limite do artigo, do carrossel e do roteiro (com `2,5` de vírgula decimal), os marcadores, a meta de tamanho de frase e o teto de densidade só no Iniciante;
- o bloco `densidade`: o teto por formato só no Iniciante (5,5, 6,5 e 6,5), o bloco no Intermediário e no Avançado, formato faltando e teto não positivo: erro terminal; a frase do teto por formato no Iniciante, vazia nos outros níveis;
- o bloco `vocabulario`: `explicar_acima_de` `intermediario` no Intermediário e `avancado` no Avançado, ausente no Iniciante; o bloco no Iniciante, `explicar_acima_de` abaixo do nível, nível fora do vocabulário e campo desconhecido no bloco: erro terminal; o nível sem o bloco carrega; a frase de vocabulário do Intermediário (explicar o termo de especialista) e a do Avançado (jargão sem explicar), vazia no Iniciante.

### Glossário

`test_glossario.py`, com um glossário pequeno montado no teste e com o do repositório:

- o glossário do repositório é válido;
- valor fora da lista em `categoria`, `nivel_minimo` ou `status`, campo ausente ou desconhecido, `limite` vazio, variante que o YAML leu como booleano, id repetido, forma repetida (sem maiúsculas e sem acentos, na mesma entrada ou em duas) e dígito em definição ou analogia: erro terminal;
- casamento: maiúsculas e acentos ignorados, posições no texto original, fronteira de palavra, sigla só com a caixa exata (inclusive terminada em sinal, como `EMBI+`), flexão fora de `variantes` não casa, `ambiguo` só pelas formas de mais de uma palavra, a forma mais longa vence, quebra de linha entre as palavras da forma e marcador `{{...}}` fora;
- filtro por `nivel_minimo` acima de um nível e busca do termo por uma forma;
- analogia: a primeira aprovada ou, sem aprovada, o primeiro rascunho, com `<id>#<n>` e `revisada`; termo sem analogia devolve nada;
- `test_flexoes_fora_de_variantes_nos_claims_das_referencias`: roda o casador sobre `<fonte>.claims.json` (claims de um run real de cada documento de referência) e lista, com `-s`, os termos escritos com flexão que não está em `variantes`. Não falha: a lista é para revisão humana do glossário. A heurística troca as 2 últimas letras de cada palavra da forma e pode trazer falso positivo.

### Plano editorial

`test_adaptacao.py`, com claims, LevelSpec e glossário montados no teste:

- pré-filtro: sai só o claim com todas as tags descartáveis; com uma tag descartável e outra não, continua candidato; candidatos nos 3 níveis;
- complemento: `atribuir_fonte` pelo tipo; a prioridade e a tese como o planejador mandou; descartes do pré-filtro, do planejador e `secundario` para os candidatos restantes; analogia do glossário e `nova`, casada pelo nome sem caixa e sem acento; `nova` fora da lista, de termo com analogia no glossário ou fora dos claims usados fica de fora; lista de termos sem analogia; sigla em minúscula não casa e termo `ambiguo` só casa pela forma de mais de uma palavra, no complemento e no gate;
- gate: o plano correto passa, e cada regra reprova: `id_inexistente` (núcleo, claims, descartes, relacionado e tese), `contraditorio` (usado e descartado, descartado pelo pré-filtro e pelo planejador, usado depois do pré-filtro, repetido nos claims, com as mensagens que citam só o que o planejador vê), `nucleo_ausente` (fora de um nível e não candidato nos 3), `acima_do_limite`, `prioridade_invalida` (repetida ou com buraco), `jargao_sem_analogia` (só no Iniciante e só para termo acima de iniciante), `analogia_invalida` (`nova` com dígito, com marcador ou que só repete o termo, inclusive por forma composta e variante; a que cita o termo com mais conteúdo passa), `motivo_invalido` (`redundante` sem relacionado, com relacionado fora dos claims ou sem todos os números e datas do descartado, comparados por valor e unidade e por dia, mês e ano, com número repetido contando duas vezes; `exige_base_tecnica` sem termo acima do nível, e sempre no Avançado) e `tese_invalida` (id não usado, tese vazia ou sem ids, número que não é data, termo técnico no Iniciante; data, termo de iniciante e o 15 de IPCA-15 passam; a tese vazia sem ids da limpeza só é aceita com `aceitar_tese_vazia`);
- limpeza depois da última rodada: plano sem problema não muda; ids inexistentes (o `redundante` sem relacionado existente vira `secundario`, e o `aviso` cita o id que saiu da tese), contraditório, núcleo ausente incluído no fim como `apoio` e núcleo não candidato retirado, núcleo vazio aborta, analogia inválida retirada com o claim saindo do Iniciante como `exige_base_tecnica`, claim do núcleo com jargão sem analogia aborta, corte pela prioridade mantendo o núcleo, analogia do termo que saiu pelo corte retirada, redundante com relacionado cortado vira `secundario`, motivo inválido vira `secundario`, tese com número ou termo técnico fica vazia, prioridades renumeradas, e o `aviso` lista o que mudou;
- schema do planejador: núcleo de 1 a 3, exatamente 3 níveis em ordem, analogia só no Iniciante, motivo `secundario` recusado, analogia sem termo ou sem explicação recusada e prioridade menor que 1 recusada;
- planejador, com o `ProviderFalso`: o prompt traz o LevelSpec dos 3 níveis, os claims com onde cada um é candidato e os termos técnicos de cada um com o nível, os termos sem analogia, o que vem primeiro no tipo de documento (uma linha por tipo, `test_prompt_diz_o_que_vem_primeiro_no_tipo_de_documento`), como o Intermediário e o Avançado se diferenciam na escolha e as regras da prioridade, do `redundante`, do `exige_base_tecnica` (com a preferência do Iniciante pelo claim com analogia), da tese e da analogia `nova`, sem a linha de versão e sem a seção de correção; a rodada 1 usa o raciocínio padrão e o span traz a rodada; a correção traz só os problemas e o plano enviado, pede `adapter_correcao` e o span traz a rodada da correção; resposta fora do schema é repetida com o erro, e 3 viram terminal;
- gravação, com o repositório em memória: rodadas do gate sem duplicar e com a cópia local (lista vazia na rodada que passou, com a saída do planejador em toda rodada), limpeza das rodadas e das saídas, plano e 9 células em `PENDENTE` com a cópia local, o mesmo plano gravado de novo sem erro e outro plano recusado, o plano lido como uma cópia reconstruída, `atualizar_celula` com a cópia local e recusada para célula que não existe, `carregar_factsheet` com os claims em ordem de id (C100 depois de C99), plano recusado sem FactSheet e leitura do plano que regrava a cópia local que falta.

### No grafo

`test_main_graph.py`, com o `ProviderFalso`:

- o run conclui com o plano, as 9 células ainda em `PENDENTE` no banco (o subgrafo simulado não grava), as analogias do glossário no Iniciante, o span do `adapter` sem nível e com a rodada 1, a saída do planejador em `rodadas_plano` e na cópia local, e a linha `plano em 1 rodada`;
- o problema do gate volta ao planejador só com a lista de problemas, e o plano fica pronto na rodada 2, com as 2 saídas e os 2 spans com a rodada;
- problema que continua depois da 3ª rodada: a limpeza corrige o plano, o `aviso` lista o que mudou, e a linha final diz `(com aviso)`;
- núcleo vazio depois da limpeza: aborta com a etapa `adapt`, com as 3 rodadas gravadas e sem plano;
- nenhum claim candidato nos 3 níveis: aborta antes de chamar o planejador;
- LevelSpec ou glossário inválido abortam antes de chamar o planejador; 3 respostas fora do schema abortam o run;
- as rodadas antigas do run são apagadas antes da rodada 1.

`test_app.py::test_e4_entra_no_fluxo_do_dashboard` confere o nó `adapt` e as arestas `validate -> adapt`, `descarte -> adapt` e `adapt -> END` em `/api/grafo`, `RODADAS_PLANO` em `rodadas_plano`, e a etapa E4 com o nó `adapt` no `app.js`. `test_persistencia_supabase.py::test_plano_editorial_rodadas_e_celulas` faz a gravação no Supabase local.

## Contrato da peça

`test_texto.py` confere `contar_palavras`, `contar_caracteres`, `palavras_de_leitura`, `contar_silabas` e `segmentar_frases` (`sunontent/texto.py`). A leitura confere: a contagem de sílabas de cerca de 30 palavras financeiras e comuns (hiato, ditongo, `u` mudo, acento, hífen, sigla), as palavras de leitura sem número, marcador nem pontuação, e as frases (quebra em `.`, `!` e `?`, sem quebra em decimal, abreviação e minúscula, `:` e `;` que não quebram, texto sem ponto final, texto sem letra e intervalos que apontam para o texto). A contagem de palavras e de caracteres confere: texto vazio e só de espaços, `R$ 1,2 bi` com 3 palavras e 9 caracteres, `IPCA-15` com 1 palavra, o travessão e a pontuação soltos que não contam, o marcador `{{C27.1}}` que ficou no texto com 1 palavra (a mesma contagem do trecho do claim que o substitui), quebras de linha e tabulação como separador e a letra acentuada decomposta com 1 caractere.

`test_schemas.py` confere os schemas de `sunontent/schemas.py`:

- **Saída do redator:** `SaidaArtigo` (seção sem claim, artigo sem seções, marcador na `linha_fina`), `SaidaCarrossel` (gancho no início, conclusão no fim, só corpo no meio, slide de corpo sem claim, menos de 3 slides) e `SaidaRoteiro` (primeira cena em 0 s, lacuna, sobreposição, cena sem duração, cena de claim sem claim, marcador no `gancho_visual`, menos de 3 cenas); a abertura, o fechamento, o gancho e a conclusão aceitam `claim_ids` vazio.
- **`NumeroUsado` e `Peca`:** `campo` e `unidade_indice` coerentes (o título não tem unidade), payload de outro formato, `celula_id` que não confere, `claim_ids` diferente da união das unidades, número em unidade inexistente, tentativa 0 e a ida e volta por JSON que mantém o tipo do payload.
- **`Violacao`:** posição opcional, `inicio` e `fim` sempre juntos e em ordem, campo e métrica fora do vocabulário.
- **Detalhes, `MetricaResultado` e `Scorecard`:** as sete métricas e o modelo de `detalhes` de cada uma (lido de volta do JSON pelo campo `metrica`), detalhes de outra métrica, `passou` coerente com as violações, violação de outra métrica, valor numérico e booleano, e o veredito da tentativa (`APROVADA` só com tudo dentro da faixa, `REPROVADA` só com métrica fora, `ERRO_INFRA` sem regra sobre as métricas), métrica repetida e tentativa 0.
- **`CelulaState`:** `motivo_reprovacao` obrigatório em `REPROVADA` e proibido nos outros estados, `erro_classe` e `erro_mensagem` obrigatórios em `ERRO_INFRA` e proibidos nos outros, e peça ou scorecard de outra célula.

## conftest.py

Não contém testes, só infraestrutura compartilhada:

- `pytest_configure`: registra os markers `supabase`, `gemini` e `judge` e aplica a trava de host.
- `pytest_collection_modifyitems`: pula os testes `gemini` e `judge`, a menos que o `-m` os peça.
- `sem_espera` (autouse): troca a espera do backoff por uma lista que registra os tempos.
- `RepositorioMemoria`: substitui o Supabase. Guarda runs, documentos, objetos do bucket e coletas; aceita falhas injetadas por método e simula a perda de resposta depois do commit.
- `repositorio` e `run_id`: fixtures com um run `copom_...` já registrado.
- `referencia(fonte)`: devolve bytes e metadados do documento congelado, ou `None` se ainda não existir.
- `RepositorioMemoria` também guarda spans (sem somar duas vezes o mesmo), validações e FactSheets, e recusa número de claim que não está em `numeros`, como o Supabase.
- `RepositorioMemoria` guarda também as rodadas do gate com a saída do planejador, o plano e as células, recusa o plano sem FactSheet do run, recusa outro plano para o run que já tem plano e recusa `rodada` em span que não é do `adapter`. O FactSheet e o plano ficam como JSON (`model_dump(mode="json")`) e voltam por `model_validate`, como pelo Postgres; `factsheet_gravado` e `plano_gravado` são os atalhos dos testes para ler o que foi gravado.
- `RepositorioMemoria` guarda também as peças de cada tentativa, as unidades com os claims e os números usados: recusa a peça de célula inexistente, outra peça na mesma tentativa, número usado que não aponta para um número de claim do run e posição repetida; grava em `peca_unidade_claims` só os claims do run; a mesma peça de novo não grava nada.
- `SubgrafoFalso` e a fixture `celulas_aprovadas`: o subgrafo de uma célula no lugar do real, com os nós `synth`, `evaluate` e `router`, que termina cada célula no estado pedido (padrão `APROVADA`) sem chamar LLM e mede o pico de células ao mesmo tempo. `test_main_graph.py` o usa em todos os testes, para que o run passe pelas 9 células sem redator.
- `ProviderFalso` e `cliente_falso`: o LLM simulado, com modelo para o `extractor`, o `adapter` e o `synthesizer`. `resposta_plano(*claim_ids)`: resposta do planejador que usa os claims dados nos 3 níveis, com as prioridades em ordem.

## Métricas

`test_metrics.py` (regras em `docs/metricas.md`) roda `sunontent/metrics/` sobre peças montadas no teste, com o LevelSpec e o glossário de teste, sem LLM e sem banco. Os claims do plano são `C01` a `C04` e a `tabela_numeros` tem um número (`9,9%`) que está no documento e em nenhum claim.

- **M1, os desfechos:** número de marcador passa; número escrito à mão com o valor de um claim do plano, com a instrução do marcador (o do claim que a peça usa tem preferência, e a `ordem` é a do número no claim); número do documento fora do plano; número ausente do documento; mesmo valor com outra unidade; marcador inválido que ficou no texto (sozinho e ao lado de um válido); número em formato que o parser não lê (`US$ 5`), que não impede a varredura do resto; várias violações no mesmo texto saem todas.
- **M1, os campos:** marcador vale no título e não vale na linha fina nem no gancho visual (o número é violação com a instrução de remover); a fala da cena; marcador de uma unidade que não cobre o número de outra.
- **M1, o glossário:** `IPCA-15` é mascarado; sem a máscara o `15 de outubro` seria lido como data sem lastro.
- **M1, as datas:** data com dia, por extenso e com o ano do documento passam; data que nenhum claim traz, ano diferente do ano do documento, ano e trimestre sem lastro são violação; o ano do documento vale sem claim; data não conta como número varrido.
- **M2:** artigo dentro da faixa, nos dois limites, curto e longo, com título e linha fina fora da contagem e a faixa de cada nível; carrossel com o limite de cada papel e a letra acentuada contada uma vez; roteiro com o limite de cada cena e do total, inclusive o total acima do limite com as cenas dentro; a faixa registrada traz o limite renderizado.
- **M5:** termo com analogia no plano e marcador na mesma unidade; sem marcador; sem analogia no plano; marcador só na unidade seguinte; só a primeira ocorrência conta; marcador sem diferença de maiúscula e acento e não dentro de outra palavra; termo de nível iniciante; termo só no título; analogia `nova` ligada pela forma escrita; Intermediário e Avançado fora da regra; a unidade nomeada como seção, slide ou cena.
- **M3:** o IFL calculado à mão num texto de 6 palavras, 9 sílabas e 2 frases (118,9); número e marcador fora da leitura; título e linha fina fora da medida; Iniciante abaixo do piso reprova com a frase mais longa, a posição e a instrução com a meta; até 3 frases acima da meta; Intermediário e Avançado só medem, seja qual for a faixa de IFL do artigo; `nivel_estatistico` do carrossel pelo piso do Iniciante e pela analogia (IFL alto sem analogia é `acima_de_iniciante`); `nivel_estatistico` do artigo pela faixa de `sinal_ifl` (Iniciante, Intermediário e Avançado nos cortes 45 e 27, sem olhar a analogia) e `None` sem as faixas; o piso do carrossel e do roteiro; o fim da unidade que fecha a frase.
- **M4:** a densidade por 100 palavras só com termos acima de iniciante, a densidade dos termos avançados, a contagem por termo, o teto por formato do Iniciante (dentro, acima, com o piso de uma ocorrência por unidade e o limite de 5 violações), as repetições escolhidas da última para a primeira, o Intermediário e o Avançado que só medem e o título e a linha fina fora da contagem.
- **Valor, faixa e detalhes:** o valor é o número de violações (o IFL na M3 e a densidade na M4); o da `llm_judge` é `true` ou `false`; o M7 (`plan_contract`) monta o resultado pelo mesmo `resultado.montar`.

## E5: síntese de formato

`test_sintese.py` (fluxo em `docs/fluxos/sintese.md`) usa o LevelSpec e o glossário de `config/`, seis claims montados no teste e um plano em que o `C02` do Iniciante tem prioridade 4 (sai do roteiro e fica no carrossel):

- **Marcadores:** o número vira `{{C01.1}}` e a data fica no texto; o trecho guardado é o do texto do claim (`R$ 1,2 bi`); o mesmo valor duas vezes casa com a primeira entrada livre (`{{C05.1}}` e `{{C05.2}}`); número sem entrada, entrada sem número e mesma cifra com outra unidade são `FalhaTerminal`; os marcadores válidos são os de todos os claims do plano do nível.
- **Corte e orçamento:** o artigo usa todo o plano; o carrossel e o roteiro, o núcleo e as maiores prioridades na ordem de leitura; a faixa do artigo e os segundos e palavras do roteiro divididos pelo peso, arredondados para baixo, com o resto dos segundos na última cena de claim; o carrossel sem divisão por claim.
- **Pacote:** analogias presas aos claims do formato (a do IPCA sai do roteiro com o `C02`), tese, núcleo, atribuição e `data_documento`; claim do plano fora do FactSheet é `FalhaTerminal`.
- **Termos a explicar:** no Intermediário, o termo de nível avançado dos claims do formato com a `definicao_simples` e os claims, sem os termos de nível intermediário; nenhum no Iniciante nem no Avançado; o termo de um claim cortado do formato sai do pacote.
- **Prompt:** marcadores com o trecho, orçamento por claim, tese, analogias e expressões no Iniciante, nada de analogia nos outros níveis, o teto de densidade só no Iniciante, a regra de vocabulário e os termos de especialista no Intermediário, o jargão sem explicar e sem lista de termos no Avançado, nada de vocabulário no Iniciante, abertura e fechamento no roteiro e, no retry, a peça anterior com os marcadores, o que está certo, as violações agrupadas pelo lugar da peça e as que ficaram de fora.
- **Montagem:** a posição de cada número no título e nas seções confere com o trecho; o marcador inexistente, o de ordem que o claim não tem e o de claim fora do plano ficam no texto e a M1 conta os três como `marcador_invalido`; `restaurar` devolve a saída do redator; o M7 (`plan_contract`) do carrossel com slides demais, claim fora do plano e claim ausente (instruções no masculino de "slide"), do carrossel curto e do roteiro que passa de 60 s, com as checagens de cada formato.
- **Nó:** a célula termina em `AVALIANDO` com a tentativa 1, depois de uma atualização para `GERANDO`; a peça, as unidades (sem o id inventado `C99`, que fica na peça e no M7 (`plan_contract`)), o número do título e a cópia local são gravados; o span leva o nível, a célula e a tentativa; o retry manda a peça anterior com o marcador inválido e o relatório, com raciocínio `low`, e grava a tentativa 2; a resposta fora do schema volta ao modelo sem consumir tentativa; sem plano, a `Falha` volta no estado (`falha`), sem a célula.
- **Gravação:** a mesma peça de novo não grava nada, e outra peça na mesma tentativa é `FalhaTerminal`.

`test_persistencia_supabase.py::test_peca_unidades_numeros_e_celula_numa_transacao` faz a gravação no Supabase local: a peça, a unidade só com o claim do run, os números do título (unidade nula) e do texto com o `campo`, a célula em `AVALIANDO`, a repetição sem efeito e a outra peça recusada. O mesmo teste grava o scorecard da tentativa (o mesmo de novo sem efeito, outro recusado, sem peça recusado), lê de volta igual ao gravado, com o valor booleano da `llm_judge`, e grava a célula em `REPROVADA` e em `ERRO_INFRA`; o check de `celulas` recusa `APROVADA` com o erro preenchido.

## E6 e E7: avaliação e refinement loop

`test_router.py` (fluxo em `docs/fluxos/avaliacao.md`) roda o subgrafo da célula (`graph.construir_grafo_celula`) com o `ProviderFalso`, o repositório em memória e o plano e o FactSheet de `test_sintese.py`, na célula `intermediario__carrossel`:

- **Correção de verdade:** a tentativa 1 tem um número escrito à mão (M1) e um slide de corpo acima de 220 caracteres (M2); o Router devolve a célula à E5 com o relatório; o prompt da tentativa 2 traz a peça anterior, o que passou (`analogy_rule` e M7 (`plan_contract`), com o valor), as violações por slide e as instruções; a tentativa 2 passa, e a célula termina `APROVADA` na tentativa 2, com os dois scorecards (`REPROVADA` e `APROVADA`), os tokens de cada tentativa e as cópias locais.
- **Não convergência:** duas tentativas reprovadas pelas mesmas métricas, sem melhora, terminam em `REPROVADA` com `nao_convergencia` na tentativa 2; aprovada na tentativa 1, o redator não é chamado de novo.
- **Falhas:** a `Falha` do redator vira `ERRO_INFRA` com a classe e a mensagem, tentativa 0 e nada gravado; a falha na gravação do scorecard vira `ERRO_INFRA` com a peça gravada e sem scorecard; a falha no retry deixa a tentativa da última peça; sem plano, `ERRO_INFRA`; uma exceção que não é `Falha` sobe.
- **Juiz no config:** recebe os chunks do documento processado, só roda quando as 6 determinísticas passam, entra no fim do scorecard e pode reprovar, com a instrução no retry; a `Falha` do juiz grava o scorecard com veredito `ERRO_INFRA` e as 6 determinísticas.
- **Router:** o relatório corretivo com até 5 violações por métrica e o resto em `restantes`; a não convergência (mesma contagem e contagem maior não convergem; contagem menor e métrica que passou convergem); a regra 5 não age com `K = 2` (nem lê a tentativa anterior), encerra com `nao_convergencia` com `K = 3` e, quando a tentativa melhorou, devolve o relatório sem gravar estado; com `K = 3`, a tentativa 3 que ainda reprova termina em `k_esgotado`; scorecard de outra tentativa é bug (`ValueError`).

## Juiz (M6)

`test_juiz.py` (regras em `docs/metricas.md`, M6) usa os claims e os chunks das três referências (`<fonte>.claims.json` e `<fonte>.documento.json`, com o texto do chunk tirado das páginas) e o glossário de `config/`:

- **Provider por nó:** o `judge` usa a sua cadeia e os outros nós a padrão; chave do Groq recusada é terminal, sem reserva.
- **Groq (HTTP simulado):** o pedido leva o modelo, o prompt, o `json_schema` com `strict`, o `max_completion_tokens` (8.192), o `include_reasoning` falso e o `reasoning_effort` (e não leva o `reasoning_effort` quando a chamada não pede); os tokens de saída são os `completion_tokens`, com o raciocínio dentro; 408, 422, 429, 498, 500, 502 e 503 são retriáveis, 401 é chave recusada, 400, 403, 404 e 413 são terminais; o 400 `json_validate_failed` devolve o `failed_generation` para o cliente repetir; `content_filter` é bloqueio; timeout e conexão são retriáveis; a resposta vazia por `length` volta como fora do schema e os tokens são cobrados. `schema_estrito` põe todas as propriedades em `required`, fecha os objetos e tira os `default`.
- **Contexto:** as palavras sem acento, sem maiúscula e sem as vazias; o BM25 ordena pela sobreposição; o chunk da âncora é o que contém o início dela; cada unidade recebe a âncora e os vizinhos e 3 chunks lexicais fora deles; a unidade sem claim recebe todos os da peça; cada chunk vai uma vez, na ordem do documento; o documento de um chunk só (CVM) não tem vizinho nem lexical.
- **Juiz:** só as analogias do plano usadas na peça vão ao juiz, com a definição técnica; o prompt traz os claims com o `literal_fonte`, os trechos, as unidades e a definição, o foco de cada nível e os termos do glossário da peça com o `nivel_minimo`, sem o nível pedido, com raciocínio `low`; a `justificativa_nivel` vai para os detalhes; tudo implicado aprova com o `nivel_percebido` e o span da célula; cada `false` vira violação, com o intervalo do trecho (no texto ou no gancho visual da cena) e, sem o trecho na unidade, sem intervalo; unidade faltando, repetida ou inexistente e analogia faltando ou não pedida são `FalhaTerminal`; `criar_juiz` tem a assinatura que a E6 chama.
- **Matriz de níveis:** `montar`, `acerto` e `formatar`, e o comando com a consulta simulada.
- **Peças de calibração:** todas são `Peca` válidas, com claims da referência e a unidade do erro dentro da peça, toda analogia dada é usada na peça, metade tem erro, os quatro tipos de erro aparecem e as peças cobrem os 9 pares de nível e formato e as três fontes.

`test_calibracao_do_juiz` (marker `judge`) julga as 20 peças de `tests/fixtures/juiz/calibracao.yaml` com o Groq de verdade, 2 em paralelo, e exige acerto de pelo menos 80% em cada grupo: peças fiéis aprovadas e peças com erro reprovadas. Uma falha do juiz (o `ERRO_INFRA` do pipeline) conta como erro da peça. Imprime, com `-s`, o resultado de cada peça (as unidades apontadas e a plantada), as justificativas, os tokens, o custo e a matriz de confusão de níveis. Custa da ordem de R$ 0,20 nas 20 peças.

## Fan-out das 9 células

`test_fanout.py` (fluxo em `docs/execucao.md`) usa o `SubgrafoFalso` no grafo principal, com o repositório em memória; o loop de uma célula está em `test_router.py`.

- **`Send` e `max_concurrency`:** num grafo mínimo, 9 ramos com `max_concurrency` 3 nunca passam de 3 ao mesmo tempo, e sem o limite passam; uma `Falha` tratada no ramo vira estado da célula e os outros 8 ramos terminam.
- **Grafo principal:** `MAX_CONCORRENCIA` vale 3 e o config de execução leva o `max_concurrency`; as 9 células rodam com no máximo 3 em paralelo; o manifest (no Supabase simulado e no `manifest.json`) registra `max_concorrencia`.
- **Terminal:** cada célula escreve as 6 linhas de início e fim de `synth`, `evaluate` e `router` na tentativa 1 e a linha do estado final; não há linhas `etapa` dos nós das células; a linha `concluído` traz a contagem por estado (`9 células (7 APROVADA, 2 REPROVADA)`).
- **Falhas:** célula em `ERRO_INFRA` não para as outras 8, o run fecha como `concluido` e o código de saída é 1; célula `REPROVADA` não muda o código; uma exceção que não é `Falha` dentro de uma célula aborta o run, com o nó em `etapa_erro`.
- **Fechamento:** conta os estados finais; célula sem estado final é `ValueError`.
- **`ClienteLLM`:** 8 threads com 25 chamadas cada somam exatamente os tokens e o custo.

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

Cobre o manifest, o grafo e o `__main__`, com repositório em memória. A fixture `raiz` copia `config/` e `prompts/` e cria uma referência de Copom dentro do `tmp_path`.

### Manifest

- `test_manifest_le_versoes_e_hashes_da_config`: o manifest lê as versões dos LevelSpec, do glossário e dos prompts, iguais às dos próprios arquivos, e o hash de cada arquivo de config.
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
- `test_run_abortado_mantem_a_primeira_mensagem`: um segundo aborto não troca a mensagem nem a etapa do primeiro; a etapa do erro (`referencia`) fica no banco e no manifest local.
- `test_varredura_aborta_so_o_run_antigo_em_andamento`: o run `em_andamento` com mais de `IDADE_ORFAO` vira `abortado` (terminal, mensagem de processo encerrado) no banco e no manifest local; o run recente segue `em_andamento`.
- `test_credenciais_ausentes_param_antes_de_tudo`: sem `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` e `SUPABASE_DB_URL`, a execução para com erro listando as três.
- `test_cli_recusa_fonte_desconhecida`: fonte inválida na linha de comando encerra; `cvm --dev` é lido corretamente.

### Linhas de etapa no terminal

- `test_run_escreve_inicio_e_fim_de_cada_etapa`: um run `--dev` com uma correção escreve `iniciada` e `concluída` para `referencia`, `leitura`, duas rodadas de `extract` e `validate` e `adapt`, nessa ordem.
- `test_etapa_que_falha_fica_sem_a_linha_de_fim`: com a coleta falhando, só aparece `etapa coleta: iniciada`.

## test_app.py

Cobre o dashboard sem rodar o pipeline e sem banco: o processo é trocado por um script Python que escreve as mesmas linhas do `__main__.py` e a `ConsultaSupabase` por uma `ConsultaFalsa` que devolve runs e peças prontos ou levanta a falha pedida. As consultas de verdade são testadas em `test_persistencia_supabase.py`.

- `test_classifica_as_linhas_do_main`: `run`, `etapa`, `concluído`, `abortado` e `erro` viram eventos com os campos extraídos; o resto é `log`.
- `test_comando_do_pipeline`: o comando é `python -m sunontent [fonte] [--dev]`; fonte desconhecida é recusada.
- `test_grafo_vem_do_graph_py`: `/api/grafo` devolve os nós e as arestas de `construir_grafo()`, inclusive `validate -> extract`.
- `test_execucao_transmite_as_linhas_classificadas`: a execução recusa uma segunda enquanto roda, separa stdout e stderr, termina com o evento `fim` e o SSE devolve todos os eventos em ordem.
- `test_fonte_desconhecida_e_recusada`, `test_parar_sem_execucao_e_recusado` e `test_run_inexistente_responde_404`: respostas 422, 409 e 404.
- `test_runs_vem_da_consulta`: `/api/runs` e `/api/runs/<run_id>` devolvem o que a consulta devolve, com `descartados`, e o servidor passa `graph.RODADAS_EXTRACAO` às duas leituras.
- `test_supabase_fora_do_ar_responde_503_com_a_mensagem`: uma `Falha` do banco vira 503 com a mensagem, nas duas rotas.
- `test_quebra_de_linha_nao_muda_o_texto_e_cabe_na_largura`, `test_palavra_maior_que_a_caixa_fica_sozinha_na_linha` e `test_espacos_repetidos_e_quebras_de_linha_valem_um_espaco`: em cada papel (gancho, corpo e conclusão), juntar as linhas com um espaço devolve exatamente o `Slide.texto`, nenhuma linha de mais de uma palavra passa da largura da caixa (medida com o `.ttf` pelo Pillow), a palavra maior que a caixa fica sozinha e a sequência de espaços vale um espaço.
- `test_svg_tem_o_texto_em_tspans_e_os_metadados`, `test_svg_escapa_o_texto_sem_mudar_as_palavras`, `test_texto_que_nao_cabe_na_altura_da_caixa_sai_com_aviso` e `test_nome_do_arquivo_do_slide`: o SVG tem 1080 x 1350, o texto em `<tspan>` dentro de `<text>`, `celula_id`, tentativa, posição, papel e `claim_ids` no `<metadata>`, `&` e `<` escapados sem mudar as palavras, e o aviso de estouro de altura.
- `test_rota_devolve_o_svg_do_slide_da_ultima_tentativa`, `test_rota_baixa_com_o_nome_do_arquivo_e_aceita_a_tentativa`, `test_rota_responde_404_para_o_que_nao_existe` e `test_rota_do_slide_responde_503_com_o_banco_fora_do_ar`: a rota do slide usa a última tentativa ou a pedida, `baixar=true` devolve o mesmo arquivo com o nome `<celula_id>_t<tentativa>_slide<n>.svg` como anexo, slide fora do carrossel, célula sem peça ou de outro formato é 404, e falha do banco é 503.
- `test_pagina_carrega_a_fonte_e_as_abas_da_sidebar`: a página tem as cinco abas da sidebar na ordem (Início, Resultado, Fluxo, Info. técnicas, Histórico), serve os três scripts e a Inter e a licença.
- `test_dashboard_mostra_os_descartados_sem_revisao`: a página tem só as cinco abas e nenhum script de revisão; o Início, o resumo do run e o Histórico mostram os claims descartados na E3 (o Histórico com 0 no lugar do vazio e "-" nos dados ausentes do run abortado); a E3 do Fluxo cobre os nós `validate` e `descarte`; as únicas rotas que não são de leitura iniciam e param o pipeline.

## test_persistencia_supabase.py

Integração com o Supabase local do CLI (`supabase start`, que roda o Postgres, o Storage e a API do Supabase em containers do Docker). Cobre o que o repositório em memória não reproduz: constraints, chaves estrangeiras, triggers de imutabilidade e o bucket `documentos`. Os testes da E1 em diante usam `run_com_referencia`: um run `--dev` da CVM com o documento de referência vinculado, como a E1 o encontra. Tem o marker `supabase` e é pulado se as três variáveis de ambiente não estiverem definidas ou se o Supabase local estiver fora do ar. A limpeza é feita com `supabase db reset`.

- `test_coleta_completa_e_reaproveitamento`: em dois runs seguidos, envia o documento, registra a coleta e confere que o documento é reaproveitado; um envio direto de objeto existente é aceito sem sobrescrever; um run já vinculado tem a transação recusada; e `update` em `documentos` e `delete` em `coletas` são barrados pelo trigger de imutabilidade.
- `test_documento_processado_grava_paginas_chunks_e_numeros` e `test_span_validacao_e_factsheet`: gravação da E1 e da E3, descritas nas seções da E1 e da E2 e E3.
- `test_plano_editorial_rodadas_e_celulas`: o plano é recusado sem o FactSheet do run; as rodadas do gate (uma linha por problema, nível nulo para o plano inteiro, e a saída do planejador em `rodadas_plano`, inclusive na rodada que passou), a limpeza das rodadas, o plano e as 9 células em `PENDENTE` gravados sem duplicar, o mesmo plano gravado de novo sem erro e outro plano recusado, a leitura do plano, `atualizar_celula` com `atualizado_em` e recusada para célula que não existe, o FactSheet lido de volta igual ao gravado (C01, C99 e C100 nessa ordem) e o plano e as rodadas do gate na `ConsultaSupabase` do dashboard.
- `test_span_do_adapter_aceita_nivel_nulo`: o span do `adapter` grava sem nível (a chamada única do planejador da E4), com nível e com a rodada do gate; o span do `extractor` com nível ou com rodada é recusado pelo banco; a `ConsultaSupabase` devolve a rodada do span.
- `test_run_abortado_com_a_etapa_do_erro`: o aborto grava `etapa_erro`, o banco recusa `etapa_erro` em run que não está abortado, e a `ConsultaSupabase` devolve a etapa.
- `test_documento_que_nao_e_o_do_run_e_recusado`: a FK composta de `documentos_processados` recusa a saída da E1 de um documento que não é o vinculado ao run.
- `test_consulta_le_o_run_completo_do_dashboard`: grava um run `--dev` completo (documento de referência, saída da E1, dois spans, duas rodadas de validação, FactSheet assinado) e confere o que a `ConsultaSupabase` devolve ao dashboard: o resumo com claims e tokens, o documento resumido, o claim com números e âncora, as rodadas em ordem e os spans em ordem de gravação. Roda as consultas contra o schema das migrations: uma coluna renomeada ou removida quebra aqui.
- `test_peca_unidades_numeros_e_celula_numa_transacao`: além da gravação da peça e do scorecard, confere a leitura do dashboard (`detalhar_run` com as 9 células, e na do artigo a peça, os números usados em ordem e o scorecard com as violações e a posição; `peca` com a última tentativa e com uma tentativa que não existe).
- `test_descartados_sao_os_reprovados_na_ultima_rodada`: descrito na seção da E2 e E3.
- `test_consulta_nao_grava`: a conexão da consulta recusa um `delete` (transação só de leitura).

## Como rodar

```
pytest                      # tudo; os testes do Supabase local são pulados se ele não estiver no ar
pytest -m "not supabase"    # só os testes simulados
pytest -m supabase          # só a integração com o Supabase local
pytest -m gemini            # só a cobertura com a Gemini API de verdade (gasta tokens)
pytest -m judge -s          # só a calibração do juiz com o Groq de verdade (gasta tokens)
```
