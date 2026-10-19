# Dashboard do fluxo

O dashboard mostra o pipeline rodando, etapa por etapa, como no terminal, e lê os resultados de cada run. Roda na mesma máquina do pipeline e escuta só em `127.0.0.1`, porque os botões iniciam processos nessa máquina.

## Como rodar

```
pip install -r requirements.txt
python -m app                 # http://127.0.0.1:8000
python -m app --porta 8080
```

Rode a partir da raiz do repositório, com o mesmo `.env` do pipeline e o Python do `.venv` (`.venv\Scripts\python -m app` no Windows): o processo iniciado pelo dashboard é o próprio `python -m sunontent`, executado com o mesmo Python do servidor, que lê o `.env` e confere as credenciais.

Cada run chama o Gemini na E2, na E4 e, por célula, na E5 (redator), chama o juiz (Groq) na E6 quando as métricas determinísticas passam, e grava no Supabase do site. A ordem de grandeza do custo está em `docs/fluxos/extracao.md` (Custo); com a fonte "Todas", são três runs.

## Peças

| Arquivo | Papel |
| ------- | ----- |
| `app/__main__.py` | Sobe o servidor (uvicorn) em `127.0.0.1`. |
| `app/servidor.py` | API FastAPI e arquivos estáticos. |
| `app/execucao.py` | Inicia `python -m sunontent [fonte] [--dev]` como processo filho, lê stdout e stderr linha a linha e classifica cada linha. Uma execução por vez. |
| `app/slide_svg.py` | O slide do carrossel em SVG: o template (caixas por papel), a quebra de linha medida pela fonte (Pillow) e a montagem do SVG. |
| `sunontent/persistencia.py` | `ConsultaSupabase`: lê os runs do Supabase para o dashboard (`listar_runs`, `detalhar_run`, com as células, `peca`, `balanco` e `biblioteca`), em conexão só de leitura. O dashboard não grava nada no banco. |
| `app/static/` | `index.html` (sidebar e as cinco páginas), `estilo.css` (tema claro, cor de marca `#E11927`, fonte Inter) e os scripts `app.js` (resumo do último run no Início, fluxo, histórico e a navegação), `inicio.js` (balanço geral e peças salvas do Início), `resultado.js` (aba Resultado, com as peças por nível e a exportação em SVG e PNG), e `tecnicas.js` (aba Info. técnicas): HTML, CSS e JS sem build e sem dependências externas; os ícones são SVG inline. |
| `app/static/fontes/` | `Inter-Regular.ttf`, `Inter-Bold.ttf` e `Inter-OFL.txt`: a fonte do slide (Inter, licença SIL OFL 1.1), carregada por `@font-face` na página e lida pelo Pillow no servidor. |

## Botões

Ficam na barra fixa do topo, visível em todas as páginas.

- **Executar run**: `python -m sunontent [fonte]`. O `__main__.py` monta o manifest, registra o run no Supabase e chama `graph.construir_grafo()`. Cada run passa pela E0 (download da publicação mais recente), E1, E2, E3 e E4, depois pelas 9 células em paralelo (E5, E6 e E7, até 3 ao mesmo tempo) e pelo fechamento, e termina com o plano editorial e as 9 células no estado final.
- **Modo dev**: o mesmo com `--dev`, entrando pelo nó `referencia` com o documento de `tests/fixtures/referencia/`, sem download. A E2, a E4 e a E5 chamam o Gemini nos dois modos.
- **Parar**: encerra o processo. O `__main__.py` não chega a registrar o aborto: o run fica `em_andamento` no Supabase até a varredura de um run iniciado mais de 1 hora depois (`docs/execucao.md`, Run abortado).
- **Fonte**: Todas (sem fonte, os três runs em sequência), Copom, CVM ou B3.

## Leitura do Supabase

Os dados dos runs vêm do Supabase, a fonte da verdade, pela `ConsultaSupabase` de `persistencia.py`. Não há leitura de `data/runs/`.

- A consulta é criada na primeira leitura, a partir do `.env` (as três variáveis do Supabase são conferidas e a conexão usa `SUPABASE_DB_URL`), e reaproveitada depois. Se o banco estiver fora, a leitura seguinte tenta de novo.
- Toda transação abre com `BEGIN READ ONLY` (`read_only` da conexão do psycopg). A opção não vai em `options` da URL porque o pooler do Supabase descarta os parâmetros de inicialização da sessão.
- Durante o run, a página relê o run a cada etapa concluída: cada etapa grava no banco o que produziu, então a aba da E3 mostra a rodada 1 enquanto a 2 roda.
- O documento processado vem de `documentos_processados` e das contagens de páginas, chunks e números: a aba da E1 mostra o emissor e a data assim que a E1 termina, mesmo que o run aborte depois.
- Enquanto o banco do site for recriado com `npx supabase db reset --linked`, cada reset apaga o histórico do dashboard.

## Como o dashboard sabe a etapa

O `__main__.py` escreve `etapa <no>: iniciada` e `etapa <no>: concluída` para cada nó (ver `docs/execucao.md`, "Linhas de etapa no terminal"). `app/execucao.py` classifica cada linha:

| Linha | Evento |
| ----- | ------ |
| `run <run_id> (<modo>)` | `run`: começa um run; sem fonte, uma execução tem três. |
| `etapa <no>: iniciada` / `concluída` | `etapa`: muda o estado da etapa e soma a duração. |
| `celula <celula_id> <no> <tentativa>: iniciada` / `concluída` | `celula`: um nó do subgrafo de uma célula (`synth`, `evaluate` ou `router`) começou ou terminou, com a tentativa. |
| `celula <celula_id>: <ESTADO>` | `celula`: a célula terminou em `APROVADA`, `REPROVADA` ou `ERRO_INFRA`; a página relê o run no Supabase. |
| `concluído: ...` | `concluido`: o run terminou; a página relê o run no Supabase. |
| `abortado (<classe>): <mensagem>` | `abortado`: a etapa em andamento fica como `falhou`. |
| `erro (<classe>): <mensagem>` | `erro`: falha fora do grafo: global, antes do primeiro run (credencial, conexão), ou de uma fonte (config, criação ou fechamento do run), com a mensagem começando pela fonte. |
| qualquer outra | `log`: vai para o terminal e para a etapa em andamento (progresso da E0, novas tentativas). |

Quando o processo termina, o servidor acrescenta o evento `fim` com o código de saída. Os eventos ficam em memória até a próxima execução: recarregar a página reconstrói o estado, mas reiniciar o servidor apaga o log. O resultado do run continua no Supabase.

## Abas

Uma sidebar à esquerda leva às cinco páginas, nesta ordem (no celular ela vira uma barra no topo). O estado geral da execução (ocioso, rodando, concluída...) fica no pé da sidebar. Uma barra fixa no topo de todas as páginas tem só o seletor de fonte (Todas, Copom, CVM ou B3) e os botões Executar run, Modo dev e Parar (no celular a barra rola com a página).

- **Início**: o resumo do último run (fonte, documento, claims, claims descartados na E3, células aprovadas, tokens e custo), com atalhos para o Resultado, as Info. técnicas e o Fluxo. Enquanto o banco não tem nenhum run, a página mostra no lugar o aviso de como iniciar o pipeline; o mesmo vale para a dica do terminal do Fluxo. Ao iniciar o pipeline, a página abre o Fluxo. Depois do último run, o Início mostra o **balanço geral** de todos os runs (runs por status e por fonte, tokens e custo total e médio, células por estado, tentativas por célula e a taxa de aprovação de cada métrica na última tentativa) e as **peças salvas**: uma lista de cartões resumidos (título, formato, nível, fonte, data, run e estado) da última tentativa de cada célula, com filtro de formato, de nível e de estado (aprovadas por padrão). Cada cartão expande o conteúdo da peça: o artigo em texto, o carrossel em SVG e o roteiro por cena. A lista mostra 20 cartões por vez, com o botão Mostrar mais.
- **Resultado**: um run lido do Supabase, escolhido no seletor (abaixo, Resultado).
- **Fluxo**: os cartões E0 a E7 com estado e duração, o contador de rodadas do loop E2/E3, a miniatura das 9 células nos cartões da E5 à E7 e as arestas do grafo. As subabas mostram o terminal completo ou uma etapa: descrição, estado, duração, os dados do run gravados por ela e as linhas do terminal daquela etapa. O cartão da E3 cobre os nós `validate` e `descarte`: soma as durações, junta as linhas e mostra o estado do último que rodou. Cada etapa com agente mostra só as chamadas de LLM do seu nó: a E2, as do `extractor`; a E4, as do `adapter`. A subaba da E4 mostra as rodadas do plano, se ele tem aviso e o plano gravado (ver Plano editorial abaixo). As subabas da E5, da E6 e da E7 mostram a grade das células (abaixo).
- **Info. técnicas**: tudo o que um run gravou, de um run por vez (abaixo, Informações técnicas).
- **Histórico**: os runs da tabela `runs`, do mais recente ao mais antigo, com os claims e os claims descartados na E3 (0 quando não há); no run abortado, claims e descartados ausentes valem 0 e os demais dados ausentes aparecem como "-"; um clique abre o resultado.

### Telas estreitas

Até 900 px de largura, a sidebar vira uma barra no topo, com o menu rolando de lado. Até 720 px, os textos e os cartões ficam menores, os cartões das etapas do Fluxo passam a duas colunas (sem rolagem lateral), as cenas do roteiro empilham o tempo sobre a fala e a matriz 3x3 do Resultado vira uma lista: um bloco por nível, com uma linha por formato e o nome do formato em cada célula. Tabelas largas rolam dentro do próprio cartão, e identificadores longos (como o `run_id`) quebram em vez de estourar a largura.

### Plano editorial

A subaba da E4 e a aba Info. técnicas mostram o plano lido de `planos_editoriais`, `planos_nivel` e `validacoes_plano`, só leitura:

- **Plano:** o núcleo, com o texto de cada claim, as rodadas do gate e o aviso, quando houver: a lista do que a limpeza do código mudou depois da última rodada.
- **Por nível:** a tese, com os claims que ela parafraseia, ou a nota de que a limpeza tirou a tese; os claims usados em ordem de leitura, com o papel, a prioridade (1 é o mais importante; o carrossel e o roteiro usam os de maior prioridade), o texto do claim e a marca de atribuição à fonte, e o núcleo destacado; no Iniciante, as analogias, com o termo, a explicação, a origem (`glossario`, com a referência `<id>#<n>`, ou `nova`) e se está revisada; e os descartes, recolhidos, com o motivo, a origem e o claim relacionado em `redundante`.
- **Gate do plano por rodada:** os problemas de cada rodada, com a regra, o nível e a mensagem devolvida ao planejador; a rodada sem problema aparece como aprovada no gate.

O texto dos claims vem do FactSheet do run. O plano não tem texto de peça: artigo, carrossel e roteiro são da E5.

### Células

As etapas E5, E6 e E7 rodam uma vez por célula, e as 9 células andam em paralelo (`MAX_CONCORRENCIA`, 3). O estado de cada etapa sai das 9 células, lido das linhas `celula`:

- **Cartão da etapa:** pendente até a primeira célula entrar no nó; rodando enquanto alguma célula está no nó ou alguma ainda não terminou; concluída quando as 9 células têm estado final. Se o processo termina antes disso, a etapa fica interrompida (ou falhou, se o run abortou). A duração vai do início da primeira célula no nó ao fim da última. Dentro do cartão, uma miniatura 3x3 pinta cada célula pelo estado (rodando, `APROVADA`, `REPROVADA`, `ERRO_INFRA`).
- **Grade da subaba:** 3 linhas (Iniciante, Intermediário, Avançado) por 3 colunas (Artigo, Carrossel, Roteiro). Cada quadro mostra o que a célula fez naquele nó: a tentativa em curso, quantas vezes concluiu o nó (`×2` depois de um retry) ou, na E7, o estado final; nas outras etapas, a célula que terminou mostra o estado final embaixo.
- **Terminal da etapa:** as linhas `celula ... <no> ...` do nó daquela etapa, de todas as células.

Uma etapa é mostrada como implementada quando o seu nó existe no grafo devolvido por `/api/grafo`. A rota abre o subgrafo da célula (`get_graph(xray=True)`), então `synth`, `evaluate` e `router` aparecem como nós do grafo, ligados à `adapt` e ao `fechamento`.

A URL guarda a aba: `#inicio`, `#fluxo/E3`, `#resultado/<run_id>/<celula_id>`, `#tecnicas/<run_id>`, `#historico`. Resultado e Info. técnicas usam o mesmo run selecionado.

### Resultado

A aba lê o run por `detalhar_run` e mostra, de cima para baixo:

- **Resumo do run:** identificação, modo, status, claims, claims descartados na E3 (quando há), tokens e custo; no run abortado, o erro com a classe e a etapa em que o run abortou.
- **Peças geradas:** um seletor de nível (Iniciante, Intermediário, Avançado) e, para o nível escolhido, as três peças da última tentativa de cada célula: o artigo (título, linha fina e seções), o carrossel com os slides desenhados na página e o roteiro (cenas com tempo, fala e gancho visual). Cada peça mostra o estado da célula. Os slides podem ser salvos em SVG ou em PNG, um a um (botões no slide) ou todos de uma vez (botões do carrossel).
- **Avaliação por célula:** a matriz 3x3 (níveis nas linhas, formatos nas colunas) com o estado de cada célula, o veredito da última tentativa e um selo por métrica (M1 a M7, com a M7 sendo o contrato do plano; a M6 só tem selo se o juiz rodou), e, abaixo da matriz, as matrizes de confusão de níveis do run (o nível pedido contra o nível percebido pelo juiz, M6, contra o nível que o IFL do artigo indica pelas faixas de `sinal_ifl`, M3, e contra o estilo de Iniciante do carrossel e do roteiro, IFL no piso e analogia na peça, M3), calculadas da última tentativa de cada célula e todas só informativas. Uma nota junto das matrizes diz que o IFL é um sinal de nível que não reprova nada e que, na M3, só o piso de IFL do Iniciante reprova; o quadro recolhível das métricas diz o mesmo. As faixas de IFL do artigo vêm do LevelSpec, pelo campo `sinal_ifl_artigo` da resposta de `GET /api/runs/{run_id}`, e o artigo de qualquer run é classificado pelo `ifl` gravado no scorecard. A seção traz um quadro recolhível com o que cada métrica mede e quais reprovam, e a nota do juiz (o que o faz reprovar ou aprovar e que o nível percebido não reprova) aparece junto das matrizes de confusão e no scorecard das tentativas em que o juiz rodou; o scorecard tem uma coluna com o efeito de cada métrica (reprova, só mede ou reprova sob condição). Um clique na célula abre:
  - **Estado:** `APROVADA`, `REPROVADA` (com o `motivo_reprovacao`) ou `ERRO_INFRA` (com a classe e a mensagem da falha), e um botão por tentativa gravada.
  - **Scorecard da tentativa:** valor, faixa esperada e resultado de cada métrica, e a lista das violações com o trecho, o esperado, o obtido, a instrução corretiva e a posição (unidade, campo e intervalo).
  - **Peça com marcações:** o texto final por unidade (título, linha fina e seções do artigo; slides; cenas com a fala e o gancho visual). Cada violação com posição é destacada no trecho exato, e cada número vindo de um marcador é sublinhado, com o claim e a página do PDF no balão.
  - **Rastreabilidade:** em cada seção, slide ou cena, os claims que ela usa, com a página da âncora no PDF; quando o run tem a URL de origem, o claim abre o PDF naquela página. O texto do claim vem do FactSheet do run.
  - **Plano do nível:** o plano editorial do nível da célula (tese, claims em ordem de leitura, analogias e descartes).
- **Claims e documento:** os claims do FactSheet com o trecho da fonte e o documento processado.

### Informações técnicas

A aba mostra, para o run escolhido: o resumo do run; os custos e as chamadas de LLM (resumo com chamadas, falhas, tokens e custo, a soma por nó e modelo e a lista de todas as chamadas, com nível, célula, tentativa, rodada, duração e falha); o manifest (modelos por nó, versões dos prompts e dos LevelSpecs, versão do glossário, `K` e células em paralelo); os arquivos de `config/` e `prompts/` com o SHA-256 inteiro; o documento fonte (com o `doc_sha256`) e o documento processado; as validações por rodada da E3; o plano editorial e as rodadas do gate do plano.

### Slide em SVG

Na célula de um carrossel, cada slide é desenhado como um `<svg>` dentro da página, e o botão "SVG" salva o mesmo arquivo. O botão "PNG" gera no navegador uma imagem 1080 x 1350 desse mesmo SVG, com a Inter embutida só na hora de desenhar (o navegador não carrega fonte externa de um SVG usado como imagem); "Todos em SVG" e "Todos em PNG" salvam os slides em sequência. O servidor gera o SVG a cada pedido, a partir da peça lida do Supabase: o pipeline não gera arquivo, nada vai para o Storage e não há tabela para isso.

- **Formato:** 1080 x 1350 (retrato 4:5), com uma caixa de texto por papel (`CAIXAS` em `app/slide_svg.py`: posição, largura, altura, tamanho da fonte, entrelinha, peso e cores):

  | Papel | Caixa (x, y, largura x altura) | Fonte |
  | ----- | ------------------------------ | ----- |
  | `gancho` | 96, 330, 888 x 690 | Inter Bold, 76 px, fundo escuro (#1A1A1A) |
  | `corpo` | 96, 240, 888 x 870 | Inter Regular, 52 px, fundo claro (#F5F6F8) |
  | `conclusao` | 96, 330, 888 x 690 | Inter Bold, 64 px, fundo escuro (#1A1A1A) |

- **Moldura:** em todos os papéis, uma faixa vermelha curva (#E11927) no topo (`<path id="faixa">`), uma pílula por slide abaixo dela (a do slide atual cheia, as outras a 30%) e um rodapé (`<g id="rodape">`) com a linha fina, a posição "n / total" à esquerda e a marca "( SUNO CONTENT )" à direita. A moldura fica fora das caixas de texto e não leva texto da peça.

- **Texto editável:** o texto fica em `<text id="texto">`, uma linha por `<tspan>` com `x` e `y` próprios, e a posição do slide ("n / total") em `<text id="posicao">`. A fonte não vai dentro do arquivo: quem edita no Inkscape ou no Illustrator precisa ter a Inter instalada (os `.ttf` estão em `app/static/fontes/`). SVG não quebra linha sozinho, então quem edita uma frase depois reajusta as linhas à mão.
- **Quebra de linha:** `slide_svg.quebrar_linhas` junta palavra por palavra e mede cada candidata com o mesmo `.ttf` que a página carrega (`ImageFont.getlength`, Pillow); a linha que passaria da largura da caixa vai para a seguinte. O código só troca o espaço entre duas palavras por uma quebra: juntar as linhas com um espaço devolve o `Slide.texto`, palavra por palavra (espaços repetidos e quebras de linha do texto valem um espaço). Uma palavra maior que a caixa fica sozinha na linha e passa da largura. Como o texto é o `Slide.texto` final, com os números já no lugar dos marcadores, a regra de todo número vir de um marcador continua valendo.
- **Aviso de estouro:** se as linhas não cabem na altura da caixa do papel, a prévia mostra o aviso (cabeçalho `X-Estouro-Altura: true` e `estouro_altura="true"` no `<metadata>`): é sinal de que o limite de caracteres do LevelSpec e a caixa do template não batem.
- **`<metadata>`:** um elemento `sunontent:slide` com `celula_id`, `tentativa`, `posicao`, `total`, `papel`, `claim_ids` (separados por espaço) e `estouro_altura`.
- **Nome do arquivo baixado:** `<celula_id>_t<tentativa>_slide<n>.svg`, por exemplo `iniciante__carrossel_t2_slide3.svg`.

Para conferir um arquivo baixado, abra o SVG no Inkscape, clique no texto e confirme que cada linha é um trecho editável e que o desenho é o da tela.

### Claims descartados na E3

Os claims descartados de um run são os que reprovaram na última rodada do loop E2/E3 (`RODADAS_EXTRACAO`), lidos de `validacoes_ancora` pela `ConsultaSupabase`, que recebe o limite do `graph.py` pelo servidor. Um run que parou antes da última rodada não tem descartados. O Início, o resumo do run e o Histórico mostram a contagem, e a subaba da E3 mostra o motivo de cada claim em cada rodada.

## API

| Rota | O que faz |
| ---- | --------- |
| `GET /api/grafo` | Nós e arestas de `construir_grafo()` com o subgrafo da célula aberto (`get_graph(xray=True)`), sem os nós de início e fim do subgrafo; `rodadas_extracao`, `rodadas_plano`, `k` e `max_concorrencia`. |
| `POST /api/execucao` | Corpo `{"fonte": null \| "copom" \| "cvm" \| "b3", "dev": bool}`. 201 ao iniciar, 409 se já houver execução em andamento, 422 para fonte desconhecida. |
| `GET /api/execucao` | A execução atual ou a última, com todos os eventos. |
| `GET /api/execucao/eventos?desde=N` | SSE com os eventos a partir de `N`; termina depois do `fim`. |
| `POST /api/execucao/parar` | Encerra o processo. |
| `GET /api/runs` | Resumo dos runs, com `descartados` (`ConsultaSupabase.listar_runs`). |
| `GET /api/balanco` | Runs por status e fonte, células por estado e a taxa de aprovação por métrica, de todos os runs (`ConsultaSupabase.balanco`). |
| `GET /api/biblioteca` | Uma linha por célula com peça gravada, na última tentativa e sem o conteúdo (`ConsultaSupabase.biblioteca`). |
| `GET /api/runs/<run_id>/pecas/<celula_id>` | A peça da célula, na última tentativa (`ConsultaSupabase.peca`). 404 se não existe. |
| `GET /api/runs/<run_id>` | Run (com `etapa_erro` e `max_concorrencia`), documento resumido, FactSheet, validações, `descartados`, spans (com a `rodada` do gate no `adapter`), plano editorial, rodadas do gate do plano e as células com as peças, os números usados e os scorecards por tentativa (`ConsultaSupabase.detalhar_run`). 404 se o run não existe. |
| `GET /api/runs/<run_id>/carrossel/<celula_id>/<n>.svg` | O SVG do slide `n` (a partir de 1) do carrossel da célula, na última tentativa ou na de `?tentativa=<t>`; `?baixar=true` devolve o mesmo arquivo como anexo com o nome do download. 404 para peça inexistente, célula que não é carrossel ou slide fora do carrossel. |
As rotas de runs e do slide respondem 503 com a mensagem quando o banco recusa a conexão ou a consulta.
