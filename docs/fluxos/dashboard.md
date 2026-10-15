# Dashboard do fluxo

O dashboard mostra o pipeline rodando, etapa por etapa, como no terminal, e lê os resultados de cada run. Roda na mesma máquina do pipeline e escuta só em `127.0.0.1`, porque os botões iniciam processos nessa máquina.

## Como rodar

```
pip install -r requirements.txt
python -m app                 # http://127.0.0.1:8000
python -m app --porta 8080
```

Rode a partir da raiz do repositório, com o mesmo `.env` do pipeline e o Python do `.venv` (`.venv\Scripts\python -m app` no Windows): o processo iniciado pelo dashboard é o próprio `python -m sunontent`, executado com o mesmo Python do servidor, que lê o `.env` e confere as credenciais.

Cada run chama o Gemini na E2 e na E4 e grava no Supabase do site. A ordem de grandeza do custo está em `docs/fluxos/extracao.md` (Custo); com a fonte "Todas", são três runs.

## Peças

| Arquivo | Papel |
| ------- | ----- |
| `app/__main__.py` | Sobe o servidor (uvicorn) em `127.0.0.1`. |
| `app/servidor.py` | API FastAPI e arquivos estáticos. |
| `app/execucao.py` | Inicia `python -m sunontent [fonte] [--dev]` como processo filho, lê stdout e stderr linha a linha e classifica cada linha. Uma execução por vez. |
| `sunontent/persistencia.py` | `ConsultaSupabase`: lê os runs do Supabase para o dashboard (`listar_runs` e `detalhar_run`), em conexão só de leitura. |
| `app/static/` | `index.html`, `estilo.css` e `app.js`: HTML, CSS e JS sem build e sem dependências externas. |

## Botões

- **Executar run**: `python -m sunontent [fonte]`. O `__main__.py` monta o manifest, registra o run no Supabase e chama `graph.construir_grafo()`. Cada run passa pela E0 (download da publicação mais recente), E1, E2, E3 e E4, e termina com o plano editorial e as 9 células em `PENDENTE`; a E5 em diante ainda não existe no grafo.
- **Modo dev**: o mesmo com `--dev`, entrando pelo nó `referencia` com o documento de `tests/fixtures/referencia/`, sem download. A E2 e a E4 chamam o Gemini nos dois modos.
- **Parar**: encerra o processo. O run em andamento fica com status `em_andamento` no Supabase, porque o `__main__.py` não chega a registrar o aborto.
- **Fonte**: Todas (sem fonte, os três runs em sequência), Copom, CVM ou B3.

## Leitura do Supabase

Os dados dos runs vêm do Supabase, a fonte da verdade, pela `ConsultaSupabase` de `persistencia.py`. Não há leitura de `data/runs/`.

- A consulta é criada na primeira leitura, a partir do `.env` (`SUPABASE_DB_URL`), e reaproveitada depois. Se o banco estiver fora, a leitura seguinte tenta de novo.
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
| `concluído: ...` | `concluido`: o run terminou; a página relê o run no Supabase. |
| `abortado (<classe>): <mensagem>` | `abortado`: a etapa em andamento fica como `falhou`. |
| `erro (<classe>): <mensagem>` | `erro`: falha antes de existir o run (credencial, conexão, config). |
| qualquer outra | `log`: vai para o terminal e para a etapa em andamento (progresso da E0, novas tentativas). |

Quando o processo termina, o servidor acrescenta o evento `fim` com o código de saída. Os eventos ficam em memória até a próxima execução: recarregar a página reconstrói o estado, mas reiniciar o servidor apaga o log. O resultado do run continua no Supabase.

## Abas

- **Fluxo**: os cartões E0 a E7 com estado e duração, o contador de rodadas do loop E2/E3 e as arestas do grafo. As subabas mostram o terminal completo ou uma etapa: descrição, estado, duração, os dados do run gravados por ela e as linhas do terminal daquela etapa. O cartão da E3 cobre os nós `validate` e `quarentena`: soma as durações, junta as linhas e mostra o estado do último que rodou. Cada etapa com agente mostra só as chamadas de LLM do seu nó: a E2, as do `extractor`; a E4, as do `adapter`. A subaba da E4 mostra as rodadas do plano, se ele tem aviso e o plano gravado (ver Plano editorial abaixo).
- **Resultado**: um run lido do Supabase: resumo (no run abortado, o erro com a classe e a etapa em que o run abortou), documento processado, claims do FactSheet com o trecho da fonte (os aceitos na revisão da quarentena marcados como `humano`), validações por rodada, a quarentena com o motivo e a decisão de cada claim, o plano editorial da E4 e os problemas do gate por rodada, chamadas de LLM e as versões do manifest. A quarentena é só leitura: a revisão é feita pelas funções de `sunontent/extracao/quarentena.py` (`docs/fluxos/extracao.md`, Quarentena).
- **Histórico**: os runs da tabela `runs`, do mais recente ao mais antigo, com os claims e os claims em quarentena ainda sem decisão; um clique abre o resultado.

### Plano editorial

A subaba da E4 e a aba Resultado mostram o plano lido de `planos_editoriais`, `planos_nivel` e `validacoes_plano`, só leitura:

- **Plano:** o núcleo, com o texto de cada claim, as rodadas do gate e o aviso, quando houver: a lista do que a limpeza do código mudou depois da última rodada.
- **Por nível:** a tese, com os claims que ela parafraseia, ou a nota de que a limpeza tirou a tese; os claims usados em ordem de leitura, com o papel, a prioridade (1 é o mais importante; o carrossel e o roteiro usam os de maior prioridade), o texto do claim e a marca de atribuição à fonte, e o núcleo destacado; no Iniciante, as analogias, com o termo, a explicação, a origem (`glossario`, com a referência `<id>#<n>`, ou `nova`) e se está revisada; e os descartes, recolhidos, com o motivo, a origem e o claim relacionado em `redundante`.
- **Gate do plano por rodada:** os problemas de cada rodada, com a regra, o nível e a mensagem devolvida ao planejador; a rodada sem problema aparece como aprovada no gate.

O texto dos claims vem do FactSheet do run. O plano não tem texto de peça: artigo, carrossel e roteiro são da E5.

Uma etapa é mostrada como implementada quando o seu nó existe no grafo devolvido por `/api/grafo`, que chama `graph.construir_grafo()`. As etapas E5 a E7 aparecem como não implementadas até os nós `synth`, `evaluate` e `router` existirem.

A URL guarda a aba: `#fluxo/E3`, `#resultado/<run_id>`, `#historico`.

## API

| Rota | O que faz |
| ---- | --------- |
| `GET /api/grafo` | Nós e arestas de `construir_grafo()`, `RODADAS_EXTRACAO`, `RODADAS_PLANO` e `K`. |
| `POST /api/execucao` | Corpo `{"fonte": null \| "copom" \| "cvm" \| "b3", "dev": bool}`. 409 se já houver execução em andamento, 422 para fonte desconhecida. |
| `GET /api/execucao` | A execução atual ou a última, com todos os eventos. |
| `GET /api/execucao/eventos?desde=N` | SSE com os eventos a partir de `N`; termina depois do `fim`. |
| `POST /api/execucao/parar` | Encerra o processo. |
| `GET /api/runs` | Resumo dos runs (`ConsultaSupabase.listar_runs`). |
| `GET /api/runs/<run_id>` | Run (com `etapa_erro`), documento resumido, FactSheet, validações, quarentena, spans (com a `rodada` do gate no `adapter`), plano editorial e rodadas do gate do plano (`ConsultaSupabase.detalhar_run`). 404 se o run não existe. |

As duas rotas de runs respondem 503 com a mensagem quando o banco recusa a conexão ou a consulta.
