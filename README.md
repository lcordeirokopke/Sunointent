# Sunontent

Pipeline de IA que recebe documentos financeiros públicos densos (ata do Copom, fato relevante da CVM, release de resultados da B3) e gera adaptações para **3 níveis de audiência × 3 formatos de mídia**, com um avaliador híbrido (métricas determinísticas e um juiz LLM) que confere se a peça é fiel ao documento e se o nível de sofisticação foi respeitado. Quando uma métrica falha, um loop de correção reescreve a peça com um relatório corretivo.

- **Audiências:** Iniciante (zero jargão sem analogia), Intermediário (vocabulário padrão de mercado, jargão de especialista explicado) e Avançado/Institucional (jargão pleno, foco analítico).
- **Formatos:** artigo analítico, carrossel (gancho, corpo e conclusão) e roteiro de vídeo curto (até 60 s, com marcação de tempo e gancho visual).
- **Saída por documento:** 9 células (3 × 3), cada uma com a peça, o scorecard e o estado final.

## Sumário

1. [Arquitetura em uma página](#arquitetura-em-uma-página)
2. [Metodologia de avaliação](#metodologia-de-avaliação)
3. [Matriz de confusão de níveis](#matriz-de-confusão-de-níveis)
4. [Resultados medidos](#resultados-medidos)
5. [Trade-offs de custo e latência](#trade-offs-de-custo-e-latência)
6. [Reprodutibilidade](#reprodutibilidade)
7. [Mapa da documentação](#mapa-da-documentação)

## Arquitetura em uma página

```
E0 coleta (ou --dev)
        │
        ▼
E1 leitura do PDF
        │
        ▼
E2 extractor ──> E3 validador de âncoras
     ▲                      │
     └──── até 3 rodadas ───┘
                            │
             descarte dos claims reprovados
                            │
             E4 plano editorial (3 níveis)
                            │
   fan-out: 1 subgrafo por célula, no máximo 3 em paralelo
                            │
E5 redator ──> E6 avaliação (M1 a M7) ──> E7 Router
    ▲                                          │
    └──── retry com relatório, até K = 3 ──────┘
                                               │
                                fechamento: conta as 9 células
```

- **Orquestrador:** LangGraph. **Modelos:** `gemini-3.8-flash` no extractor, no planejador e no redator; `openai/gpt-oss-120b` (Groq) no juiz, de família diferente do gerador, para o juiz não avaliar a si mesmo.
- **Fatos antes de texto:** o extractor produz *claims* ancorados num trecho literal do PDF, e o validador (código, sem LLM) confere âncora, números e datas. O redator só escreve a partir dos claims aprovados, e todo número da peça entra por um marcador do claim (`{{C27.1}}`) que o código troca pelo trecho original (`5,2%`).
- **Banco:** Supabase (Postgres e Storage) é a fonte da verdade; `data/sources/` e `data/runs/` são cópias locais.

Detalhes em `docs/arquiteturas.md` e `docs/execucao.md`.

## Metodologia de avaliação

A avaliação é o núcleo do projeto. Ela responde duas perguntas: **a peça é fiel ao documento?** e **o nível de sofisticação pedido foi respeitado?** As cinco primeiras métricas e a M7 são determinísticas (rodam sem rede, sem banco e sem LLM); só a M6 chama um modelo.

| Métrica | Nome | Tipo | O que confere | Reprova quando |
| ------- | ---- | ---- | ------------- | -------------- |
| M1 | `numeric_fidelity` | determinística | Todo número da peça que não é data vem de um marcador de claim; datas, anos, trimestres e reuniões batem com os claims do plano e o ano do documento | número sem marcador (inclusive alucinado), marcador inválido, data sem lastro |
| M2 | `text_size` | determinística | Palavras do artigo, caracteres de cada slide e palavras de cada cena dentro do contrato de formato do LevelSpec | unidade fora do limite |
| M3 | `readability` | determinística | Índice de Facilidade de Leitura de Flesch adaptado ao português (Martins et al., 1996) | IFL abaixo do piso do Iniciante (os outros níveis só medem) |
| M4 | `term_density` | determinística | Ocorrências de termos do glossário acima de iniciante por 100 palavras | densidade acima do teto do Iniciante (os outros níveis só medem) |
| M5 | `analogy_rule` | determinística | Todo termo acima do nível tem analogia (Iniciante) ou explicação (Intermediário) no plano e um marcador na mesma unidade da primeira ocorrência | termo sem analogia no plano, ou sem marcador na unidade |
| M6 | `llm_judge` | juiz LLM | Cada analogia explica corretamente; cada unidade é implicada pelos claims que cita; nível percebido | analogia incorreta, unidade não implicada |
| M7 | `plan_contract` | determinística | Quantidade de slides, soma das cenas até `duracao_s`, `claim_ids` dentro do plano, cobertura do formato | qualquer checagem falha |

### Princípios

- **Determinístico primeiro, juiz por último.** O juiz só roda depois que M1, M2, M3, M4, M5 e M7 passam, e só pode **reprovar**: não existe caminho em que ele aprove o que uma métrica determinística reprovou.
- **Falso positivo é o pior defeito.** A M1 confere ano, trimestre e reunião por valor (regra leve) para não reprovar peça correta, ao custo de deixar passar um trimestre trocado quando os dois números aparecem nos claims.
- **O nível é medido por cinco sinais independentes:** regra de analogia (M5), tamanho por nível (M2), legibilidade (M3), densidade de termos (M4) e nível percebido pelo juiz (M6).
- **O juiz não sabe o nível pedido.** O prompt do `nivel_percebido` não recebe o nível da célula, para não ser influenciado. Ele examina quatro critérios, nesta ordem: vocabulário, explicação, pressuposto de conhecimento e foco.
- **Contexto do juiz por BM25, sem embeddings.** Para cada unidade, vão ao juiz o chunk da âncora de cada claim citado, o anterior, o seguinte e os 3 chunks de maior sobreposição lexical.
- **Toda violação é acionável.** Cada `Violacao` traz o trecho exato, a posição (unidade, campo, intervalo), o esperado, o obtido e uma `instrucao_corretiva` que alimenta o retry.
- **Falha de infraestrutura nunca vira aprovação.** Falha esgotada do juiz grava o scorecard com veredito `ERRO_INFRA`.

### Calibração do juiz

`tests/fixtures/juiz/calibracao.yaml` tem 20 peças escritas sobre os claims das três referências, cobrindo os 3 níveis e os 3 formatos: 10 fiéis aos claims e 10 com um erro plantado (analogia errada, causa inventada, projeção como fato, número por extenso trocado). `pytest -m judge` julga as 20 com o Groq de verdade e exige acerto de pelo menos 80% em cada grupo (fiéis aprovadas, com erro reprovadas). Custa da ordem de R$ 0,20.

### Refinement loop (E7)

O Router é o único nó que grava estado final de célula. Em cada tentativa ele aplica as regras na ordem e:

1. **Aprova** quando todas as métricas ficam dentro da faixa.
2. **Devolve à E5** com um relatório corretivo (até 5 violações por métrica, o resto em `restantes`, e o que já passou), se a tentativa não esgotou o `K`.
3. **Encerra por `nao_convergencia`** a partir da tentativa 2 quando a tentativa não melhora a anterior (as mesmas métricas reprovadas, ou mais, e nenhuma contagem de violações por métrica menor).
4. **Encerra por `k_esgotado`** na 3ª tentativa que ainda reprova (`K = 3`).
5. **Termina em `ERRO_INFRA`** (com a classe e a mensagem da falha) quando o redator ou o avaliador falha por infraestrutura.

Regra por métrica, fórmulas, instruções corretivas e limites conhecidos: `docs/metricas.md`. Fluxo do Router: `docs/fluxos/avaliacao.md`.

## Matriz de confusão de níveis

São duas matrizes, as duas **só informativas** (nenhuma reprova célula). Ambas leem a última tentativa de cada célula.

1. **Matriz do juiz:** linhas = nível pedido, colunas = `nivel_percebido` pelo juiz (M6), por formato. Só entram células em que o juiz rodou (as determinísticas passaram).
2. **Matriz estatística:** linhas = nível pedido, colunas = sinal de nível da M3.
   - **Artigo:** as colunas são os três níveis, pela faixa de IFL em que o artigo cai (`sinal_ifl` do LevelSpec: Iniciante a partir de 45, Intermediário de 27 a 45, Avançado abaixo de 27).
   - **Carrossel e roteiro:** as colunas são `iniciante` e `acima_de_iniciante`. O limite de caracteres encurta as frases de qualquer nível, então o IFL sozinho confunde frase curta com texto de Iniciante; por isso o sinal exige IFL no piso do Iniciante **e** um marcador de analogia na peça.

Para imprimir as duas a partir do Supabase (só leitura):

```
python -m sunontent.metrics.matriz_niveis [run_id ...]
```

Sem `run_id`, usa todos os runs. O dashboard mostra as mesmas matrizes na página Resultado.

## Resultados medidos

Os números abaixo agregam as cópias locais em `data/runs/` dos runs concluídos cujo `manifest.json` registra as mesmas versões e os mesmos hashes de `config/` e `prompts/` que o repositório tem hoje (LevelSpec, glossário, `models.yaml` e os quatro prompts). São 6 runs, nos modos `--dev` e normal: Copom (3), B3 (2) e CVM (1), com 54 células. A CVM tem um único run, então a agregação pesa mais Copom e B3. São poucos runs: ler como ordem de grandeza, não como estatística robusta.

### Estados finais das 54 células

| Fonte | Runs | Aprovadas | Reprovadas |
| ----- | ---: | --------: | ---------: |
| Copom | 3 | 25 | 2 |
| B3 | 2 | 17 | 1 |
| CVM | 1 | 9 | 0 |
| **Total** | **6** | **51** | **3** |

- Reprovadas: 2 por `k_esgotado` e 1 por `nao_convergencia`. Nenhuma célula terminou em `ERRO_INFRA`.
- **Aprovação:** 51 de 54 células (94%). Das 51 aprovadas, 34 passaram na tentativa 1, 13 na tentativa 2 e 4 na tentativa 3: sem o loop, só 34 das 54 células (63%) teriam saído aprovadas.

### Matriz do juiz (nível pedido × nível percebido)

Soma dos runs, das células em que o juiz rodou (53 de 54, porque o juiz só roda depois que as determinísticas passam).

| Artigo | percebido Iniciante | percebido Intermediário | percebido Avançado |
| ------ | :---: | :---: | :---: |
| **pedido Iniciante** | 3 | 3 | 0 |
| **pedido Intermediário** | 0 | 4 | 2 |
| **pedido Avançado** | 0 | 0 | 6 |

| Carrossel | percebido Iniciante | percebido Intermediário | percebido Avançado |
| --------- | :---: | :---: | :---: |
| **pedido Iniciante** | 4 | 2 | 0 |
| **pedido Intermediário** | 1 | 3 | 2 |
| **pedido Avançado** | 0 | 1 | 5 |

| Roteiro | percebido Iniciante | percebido Intermediário | percebido Avançado |
| ------- | :---: | :---: | :---: |
| **pedido Iniciante** | 5 | 1 | 0 |
| **pedido Intermediário** | 1 | 2 | 3 |
| **pedido Avançado** | 0 | 1 | 4 |

Acerto do juiz: 36 de 53 (68%): 13 de 18 no artigo, 12 de 18 no carrossel e 11 de 17 no roteiro. Leitura:

- O Avançado é bem reconhecido (15 de 17).
- Os erros são todos entre níveis vizinhos: nenhum Iniciante foi lido como Avançado nem o inverso.
- Há duas direções de confusão. O Iniciante é lido como Intermediário (6 de 18), e o Intermediário é lido como Avançado (7 de 18), sobretudo no roteiro. O Intermediário é o nível mais difícil de fixar, e a peça de Intermediário tende a usar jargão de especialista com explicação curta, que o juiz lê como Avançado.
- O juiz não vê o nível pedido, então essa é uma medida independente do prompt do redator.

### Matriz estatística (nível pedido × sinal da M3)

**Artigo** (18 células):

| | sinal Iniciante | sinal Intermediário | sinal Avançado |
| - | :---: | :---: | :---: |
| **pedido Iniciante** | 6 | 0 | 0 |
| **pedido Intermediário** | 0 | 5 | 1 |
| **pedido Avançado** | 0 | 0 | 6 |

IFL medido: Iniciante 47,6 a 62,6; Intermediário 25,3 a 38,2; Avançado 3,1 a 24,8. Acerto de 17 de 18: o único erro é o Intermediário da CVM (25,3), abaixo do corte de 27. Como o IFL mede o texto e não o assunto, um documento com muito nome próprio e termo longo desloca a escala.

**Carrossel** (18 células): 18 de 18 corretas. **Roteiro** (18 células): 17 de 18, com um Iniciante lido como `acima_de_iniciante`. O Intermediário e o Avançado ficam em `acima_de_iniciante` nas 12 células de cada formato. O sinal só separa o Iniciante dos demais, por construção.

O sinal estatístico (52 de 54, 96%) é mais estável que o do juiz (68%), mas mede só legibilidade e analogia, não o pressuposto de conhecimento. Os dois são complementares, e nenhum reprova a célula.

## Trade-offs de custo e latência

Custos em BRL, com a cotação provisória de R$ 5,40 por US$ (`config/models.yaml`). Preço por milhão de tokens: `gemini-3.8-flash` US$ 0,75 de entrada e US$ 3,75 de saída (promocional até 2026-12-31); juiz `openai/gpt-oss-120b` US$ 0,15 e US$ 0,60. Tokens de raciocínio contam como saída. As tabelas usam os mesmos 6 runs.

### Custo por run e por etapa

| Etapa | Média | Mínimo | Máximo |
| ----- | ----: | -----: | -----: |
| Extractor (E2) | R$ 0,35 | R$ 0,17 | R$ 0,52 |
| Planejador (E4) | R$ 0,15 | R$ 0,09 | R$ 0,22 |
| Redator (E5) | R$ 1,00 | R$ 0,68 | R$ 1,39 |
| Juiz (M6) | R$ 0,14 | R$ 0,09 | R$ 0,18 |
| **Total do run** | **R$ 1,64** | **R$ 1,28** | **R$ 2,28** |

Por fonte, o custo médio do run é R$ 1,55 no Copom (3 runs), R$ 1,96 na B3 (2) e R$ 1,31 na CVM (1). Os tokens por run vão de 136 mil a 309 mil. Um run completo (9 células) custa em média R$ 1,64, ou cerca de R$ 0,18 por peça.

### Onde está o trade-off

- **O redator domina o custo (cerca de 61% da média do run).** Cada retry reescreve a peça inteira, e o raciocínio conta como saída, o token mais caro. Por isso a correção do redator usa raciocínio `low` (`synthesizer_correcao`), assim como a correção do extractor e do planejador: corrigir com um relatório é tarefa menor que gerar do zero.
- **O loop troca custo por fidelidade.** Das 51 células aprovadas, 17 precisaram de retry (13 na tentativa 2 e 4 na tentativa 3). Cada tentativa extra repete a chamada do redator (e, se as determinísticas passam, a do juiz). A parada por não convergência evita gastar a 3ª tentativa quando a 2ª não melhorou.
- **O juiz é barato, mas o contexto é caro.** Custa cerca de 9% do run, mas a entrada dele é grande (claims, trechos do documento por BM25, unidades): 42 mil a 137 mil tokens de entrada por run contra 12 mil a 26 mil do redator. A recuperação por BM25 simples, sem embeddings, mantém esse contexto pequeno e sem custo de indexação.
- **Juiz só depois das determinísticas.** Ele roda uma vez por tentativa e só quando as 6 verificações passam, então a tentativa que já falhou por número, tamanho ou analogia não paga a chamada.
- **Concorrência limitada a 3 células** (`MAX_CONCORRENCIA`) para ficar dentro dos limites de requisições por minuto das APIs, ao custo de latência.
- **Chave de reserva do Gemini:** a `GOOGLE_API_KEY_SEC` só é chamada quando a principal falha por infraestrutura ou chave recusada, trocando disponibilidade por um pouco de latência na falha.

### Latência

Soma das durações das chamadas de LLM por run (o redator roda em paralelo entre as células, então o relógio real é menor que a soma dele):

| Etapa | Mediana | Mínimo | Máximo |
| ----- | ------: | -----: | -----: |
| Extractor | 94 s | 38 s | 140 s |
| Planejador | 42 s | 25 s | 50 s |
| Redator | 263 s | 206 s | 412 s |
| Juiz | 58 s | 43 s | 83 s |

- A extração e o plano são sequenciais e somam cerca de 1 a 3 minutos. Depois deles, as 9 células rodam 3 a 3.
- O tempo de relógio de um run não é registrado no manifest; ele depende da fila de concorrência e das esperas de backoff. Pelos spans do Supabase (do início da primeira chamada ao fim da última), os 6 runs levaram de 310 a 452 s (5 a 7,5 minutos), dos quais 94 a 197 s até o fim do plano. Nos testes de cobertura com a Gemini API de verdade, a extração e o plano das 3 fontes levaram de 15 a 20 minutos no total.
- Os spans incluem as chamadas que falharam por infraestrutura (94 nos 6 runs, como cota 429 da Gemini): não consomem tokens, mas somam latência de retentativa. Com cota disponível, a latência cai.

## Reprodutibilidade

### Pré-requisitos

- Python 3.12 (versão em que o projeto foi desenvolvido) e pip.
- Node.js (para o `npx supabase`) e Docker em execução (o Supabase local sobe em contêineres).
- Para rodar o pipeline com LLM de verdade: uma chave da Gemini API (Google AI Studio) e uma chave do Groq. Os testes de rotina **não** precisam de nenhuma chave.

### 1. Instalar

```
git clone <url-do-repositório>
cd <pasta-do-repositório>
python -m venv .venv
.venv\Scripts\activate          # Windows; no Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

O código roda sempre a partir da raiz do repositório.

### 2. Rodar a suíte de testes (sem chaves, sem rede)

```
pytest
```

Quase tudo é simulado (HTTP, Supabase em memória, LLM simulado, disco em `tmp_path`), e uma trava no `conftest.py` aborta o pytest se `SUPABASE_URL` ou `SUPABASE_DB_URL` apontarem para um host que não seja `localhost`, `127.0.0.1` ou `::1`. Os testes de integração com o Supabase local têm o marker `supabase`; os que gastam tokens têm os markers `gemini` e `judge` e ficam pulados, a menos que o `-m` os peça:

```
pytest -m supabase     # integração com o Supabase local (passo 3)
pytest -m gemini -s    # cobertura da extração com a Gemini API de verdade (gasta tokens)
pytest -m judge -s     # calibração do juiz com o Groq de verdade (cerca de R$ 0,20)
```

O que cada teste cobre: `docs/testes.md`.

### 3. Subir o Supabase local

```
npx supabase start
npx supabase db reset
```

`db reset` aplica as migrations de `supabase/migrations/` e o `seed.sql`, e apaga os dados da cópia local. As portas padrão são 54321 (API) e 54322 (Postgres).

### 4. Configurar o `.env`

Copie `.env.example` para `.env` (o arquivo fica fora do git) e preencha. Para o Supabase local:

```
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_SERVICE_ROLE_KEY=<service_role key impressa pelo supabase start>
SUPABASE_DB_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
GOOGLE_API_KEY_PRINC=<chave da Gemini API>
GOOGLE_API_KEY_SEC=             # opcional: reserva
GROQ_API_KEY=<chave do Groq>
```

`npx supabase status` reimprime as chaves locais. A `GOOGLE_API_KEY_PRINC` e a `GROQ_API_KEY` são obrigatórias para um run; sem elas o `__main__` recusa o início antes de criar o run.

### 5. Rodar o pipeline

Modo `--dev`, que usa os documentos congelados de `tests/fixtures/referencia/` (Copom ata 281, fato relevante conjunto Sabesp e EMAE da CVM, release 2T26 da B3) em vez de baixar da internet. É o caminho reprodutível, porque o `doc_sha256` do PDF é conferido contra o `.json` de referência:

```
python -m sunontent copom --dev
python -m sunontent cvm --dev
python -m sunontent b3 --dev
```

Sem a fonte, `python -m sunontent --dev` roda as três, e a falha de uma não impede as seguintes. Sem `--dev`, a E0 baixa a publicação mais recente de cada fonte (ata do Copom, fato relevante da CVM, release da B3).

Cada run grava no Supabase e em `data/runs/<run_id>/`: `manifest.json`, o FactSheet, o plano, e por célula a peça (`peca_N.json`), o scorecard (`scorecard_N.json`) e o `estado.json`. O código de saída é 1 se alguma célula terminar em `ERRO_INFRA`; célula `REPROVADA` não muda o código.

### 6. Reproduzir as matrizes de confusão e o custo

```
python -m sunontent.metrics.matriz_niveis                 # todos os runs
python -m sunontent.metrics.matriz_niveis <run_id> ...    # runs específicos
```

O custo e os tokens de cada chamada ficam em `data/runs/<run_id>/spans/` e o total em `manifest.json` (`custo_total_tokens`, `custo_total_brl`).

### 7. Dashboard de comparação e auditoria

```
python -m app
```

Sobe em `http://127.0.0.1:8000` (somente local; use `--porta N` para outra porta). Permite iniciar o pipeline, acompanhar as etapas ao vivo, abrir os runs, ver a matriz 3×3 de células com scorecard, violações por posição, rastreabilidade claim a trecho do PDF, plano do nível, as matrizes de confusão e o carrossel em SVG (baixável em SVG ou PNG). O dashboard só lê o Supabase e nunca grava no banco. Detalhes em `docs/fluxos/dashboard.md`.

### Como congelar um documento para o modo `--dev`

O modo `--dev` lê `tests/fixtures/referencia/<fonte>.pdf` e `<fonte>.json` (os campos `doc_id`, `doc_sha256`, `url_origem` e `coletado_em`) e para se o SHA-256 do PDF não bater com o `.json`. Para trocar o documento congelado de uma fonte (aqui, `copom`):

```
python -m sunontent copom                          # 1. baixa a publicação mais recente para data/sources/copom/
cp data/sources/copom/<doc_sha256>.pdf tests/fixtures/referencia/copom.pdf
cp data/sources/copom/<doc_sha256>.<coletado_em>.json tests/fixtures/referencia/copom.json   # 2. copia o par, sem reabrir o PDF
python -m sunontent copom --dev                    # 3. confere o hash
```

Depois, atualize à mão `copom.anotacao.yaml` (fatos que a extração deve cobrir) e a listagem simulada em `tests/fixtures/http/copom/`, regrave o snapshot com `REGERAR_DOCUMENTO=1 pytest tests/test_extracao.py`, regrave `copom.claims.json` copiando o `factsheet.json` de um run `--dev` do novo documento, rode `pytest` e versione o resultado. O passo a passo completo está em `docs/fluxos/ingestao.md`, Como congelar um documento.

### O que fixa a reprodutibilidade

- **Manifest por run:** `manifest.json` registra o modelo de cada nó, a versão de cada prompt, a versão de cada LevelSpec e do glossário, `K`, `max_concorrencia` e o SHA-256 de cada arquivo de `config/` e `prompts/`. Dois runs com o mesmo manifest usaram exatamente as mesmas regras.
- **Snapshot de modelo fixo por nó** em `config/models.yaml`, com o preço usado no custo.
- **Documentos congelados** com hash conferido no modo `--dev`, mais a anotação manual dos fatos que a E2 deve cobrir (`<fonte>.anotacao.yaml`).
- **LevelSpec, glossário, prompts e `models.yaml` versionados no git**; o banco guarda só as versões usadas em cada run.
- **Dependências com versão exata:** o `requirements.txt` fixa cada pacote direto com `==`, para garantir a máxima reprodutibilidade do ambiente.
- **Métricas M1 a M5 e M7 puras:** mesma peça, mesmo resultado, sem rede.

### Limites da reprodução

- A saída do LLM varia entre execuções. Dois runs `--dev` do mesmo documento geram claims, planos e peças ligeiramente diferentes (os custos e estados das 54 células medidas acima mostram essa variação). O que é reprodutível é o **critério de avaliação**, não o texto gerado; por isso o relatório usa a matriz de confusão e as contagens de estado, e não a comparação de textos.
- A cotação (R$ 5,40 por US$) e o preço promocional do Gemini são valores de consulta, e o custo em BRL muda quando eles mudam.
- Os testes não confirmam que os sites reais do BCB, da CVM e da B3 mantêm o formato esperado; isso só se verifica rodando `python -m sunontent <fonte>` sem `--dev`.

## Mapa da documentação

| Arquivo | Conteúdo |
| ------- | -------- |
| `docs/arquiteturas.md` | Arquitetura, etapas E0 a E7 e o refinement loop |
| `docs/execucao.md` | Ciclo de vida do run, manifest, concluído e abortado |
| `docs/metricas.md` | M1 a M7: regra, valor, faixa, detalhes e instrução corretiva, incluindo o juiz no Groq |
| `docs/glossario.md` | `config/glossario.yaml`: termos, níveis e analogias |
| `docs/testes.md` | O que cada teste cobre e como rodar |
| `docs/fluxos/ingestao.md` | E0 e modo `--dev` |
| `docs/fluxos/leitura.md` | E1: PDF, chunks, metadados e tabela de números |
| `docs/fluxos/extracao.md` | E2 e E3: extractor, ancoragem, validação e cliente LLM |
| `docs/fluxos/plano-editorial.md` | E4: LevelSpec, glossário e plano |
| `docs/fluxos/sintese.md` | E5: pacote, redator e montagem |
| `docs/fluxos/avaliacao.md` | E6 e E7: scorecard, Router e relatório corretivo |
| `docs/fluxos/dashboard.md` | Dashboard: páginas, API e slide em SVG |
