-- E1 (DocumentoProcessado), E2 (FactSheet, Claim, Numero) e E3 (validação de âncoras).
-- Ancora (docs/arquiteturas.md, Número e âncora) é gravada em colunas: pagina, offset_inicio, offset_fim,
-- com offsets de caractere no texto limpo da página. chunks guarda um intervalo: offset_inicio
-- relativo a pagina_inicio e offset_fim relativo a pagina_fim. A página é 1-indexada, como no PDF aberto por um humano.

-- Metadados do DocumentoProcessado, um por run (docs/arquiteturas.md, E1. Leitura). Gravado pela E1 na
-- mesma transação das páginas, dos chunks e dos números. Emissor e data vêm de extracao/metadados.py e
-- pertencem ao run, não ao PDF: mudam se o código da E1 mudar. A FK composta garante que a E1 gravou o
-- documento vinculado ao run pela E0 ou pela referência do --dev.
create table documentos_processados (
  run_id         text primary key,
  doc_id         text not null,
  doc_sha256     char(64) not null,
  tipo_documento tipo_documento not null,
  emissor        text not null,
  data_documento date not null,
  foreign key (run_id, doc_id, doc_sha256) references runs (run_id, doc_id, doc_sha256)
);

-- Texto limpo por página. Necessário para reverificar âncoras (docs/arquiteturas.md, E3. Validador de Âncoras).
create table documento_paginas (
  run_id      text not null references documentos_processados (run_id),
  pagina      int not null check (pagina >= 1),
  texto_limpo text not null,
  ocr         boolean not null default false,  -- página lida por OCR (docs/arquiteturas.md, E1. Leitura); sempre false enquanto o OCR não existe
  primary key (run_id, pagina)
);

-- Chunks por seção lógica (docs/arquiteturas.md, E1. Leitura), usados pela M6 para recuperar contexto.
create table chunks (
  run_id           text not null,
  chunk_id         text not null,
  pagina_inicio    int not null,
  offset_inicio    int not null,
  pagina_fim       int not null,
  offset_fim       int not null,
  secao            text,
  texto            text not null,
  primary key (run_id, chunk_id),
  foreign key (run_id, pagina_inicio) references documento_paginas (run_id, pagina),
  foreign key (run_id, pagina_fim) references documento_paginas (run_id, pagina),
  check (pagina_fim > pagina_inicio or (pagina_fim = pagina_inicio and offset_fim >= offset_inicio))
);

-- FactSheet (docs/arquiteturas.md, FactSheet). Um por run, gravado já assinado pela E3 ou, quando
-- sobram claims reprovados depois da última rodada, pelo nó quarentena, só com os claims válidos.
create table factsheets (
  run_id         text primary key references runs (run_id),
  doc_id         text not null,
  tipo_documento tipo_documento not null,
  emissor        text not null,
  data_documento date not null,
  assinado       boolean not null default false
);

-- FactSheet.tabela_numeros: TODOS os números do fonte, com âncora. Gravada pela E1 (origem pipeline);
-- a revisão da quarentena acrescenta o número que a E1 não leu, confirmado por um humano (origem humano).
-- O mesmo bruto pode aparecer em posições diferentes; a unicidade é por posição.
create table numeros (
  id            bigint generated always as identity primary key,
  run_id        text not null,
  bruto         text not null,
  valor         numeric not null,
  unidade       unidade_numero not null,
  pagina        int not null,
  offset_inicio int not null,
  offset_fim    int not null,
  origem        origem_dado not null default 'pipeline',
  foreign key (run_id, pagina) references documento_paginas (run_id, pagina),
  unique (run_id, pagina, offset_inicio, offset_fim),
  check (offset_fim >= offset_inicio)
);

create index numeros_run_bruto_idx on numeros (run_id, bruto);

-- Claim (docs/arquiteturas.md, Claim). claim_id ("C07") é estável só dentro do documento.
-- origem humano: claim da quarentena aceito na revisão, gravado depois do FactSheet assinado.
create table claims (
  run_id        text not null references factsheets (run_id),
  claim_id      text not null,
  texto         text not null,
  tipo          tipo_claim not null,
  tags          text[] not null default '{}',
  literal_fonte text not null,
  pagina        int not null,
  offset_inicio int not null,
  offset_fim    int not null,
  origem        origem_dado not null default 'pipeline',
  primary key (run_id, claim_id),
  check (offset_fim >= offset_inicio)
);

-- Claim.numeros: o Numero completo de cada número do claim e o vínculo com a sua entrada em numeros.
-- A E2 resolve cada número pelo bruto dentro do literal_fonte (docs/fluxos/extracao.md), então todo
-- número de claim é uma entrada da tabela_numeros, na mesma posição.
create table claim_numeros (
  run_id        text not null,
  claim_id      text not null,
  ordem         int not null,
  bruto         text not null,
  valor         numeric not null,
  unidade       unidade_numero not null,
  pagina        int not null,
  offset_inicio int not null,
  offset_fim    int not null,
  numero_id     bigint not null references numeros (id),
  primary key (run_id, claim_id, ordem),
  foreign key (run_id, claim_id) references claims (run_id, claim_id)
);

-- Rodadas do loop E2/E3 (docs/fluxos/extracao.md): uma linha por claim proposto em cada rodada, com o
-- motivo da recusa. A rodada 1 é a extração; até 2 correções, depois os reprovados vão para a
-- quarentena. Gravadas antes de existir o FactSheet assinado, por isso ligadas a runs.
create table validacoes_ancora (
  run_id   text not null references runs (run_id),
  rodada   int not null check (rodada >= 1),
  claim_id text not null,
  passou   boolean not null,
  motivo   text,
  primary key (run_id, rodada, claim_id)
);

-- Quarentena (docs/fluxos/extracao.md, Quarentena): claim ainda reprovado depois da última rodada, com o
-- claim como o extractor o propôs (ClaimProposto em JSON) e o motivo da última recusa. Ligada a runs,
-- e não a factsheets, porque é gravada também quando nenhum claim passa e o run aborta.
create table claims_quarentena (
  run_id   text not null references runs (run_id),
  claim_id text not null,
  rodada   int not null check (rodada >= 1),
  proposto jsonb not null,
  motivo   text not null,
  primary key (run_id, claim_id)
);

create trigger claims_quarentena_imutavel
  before update or delete on claims_quarentena
  for each row execute function bloquear_alteracao();

create trigger claims_quarentena_sem_truncate
  before truncate on claims_quarentena
  for each statement execute function bloquear_alteracao();

-- Decisão humana sobre um claim em quarentena, uma por claim, final. 'aceitar' grava, na mesma
-- transação, o claim (origem humano) no FactSheet do run e os números novos em numeros.
create table decisoes_quarentena (
  run_id      text not null,
  claim_id    text not null,
  decisao     decisao_quarentena not null,
  autor       text not null check (autor <> ''),  -- nome informado por quem revisou
  decidido_em timestamptz not null default now(),
  primary key (run_id, claim_id),
  foreign key (run_id, claim_id) references claims_quarentena (run_id, claim_id)
);

create trigger decisoes_quarentena_imutavel
  before update or delete on decisoes_quarentena
  for each row execute function bloquear_alteracao();

create trigger decisoes_quarentena_sem_truncate
  before truncate on decisoes_quarentena
  for each statement execute function bloquear_alteracao();
