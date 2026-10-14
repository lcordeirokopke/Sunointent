-- E4 (PlanoEditorial e gate do plano), células e E5 (Peca por tentativa).

-- Rodadas do gate do plano (docs/arquiteturas.md, E4.3 Complemento e gate do plano): uma linha por problema acusado.
-- Rodada sem linha passou. A rodada 1 é o plano; até 2 correções. Gravadas antes do plano final,
-- por isso ligadas a runs.
create table validacoes_plano (
  run_id  text not null references runs (run_id),
  rodada  int not null check (rodada between 1 and 3),
  ordem   int not null check (ordem >= 1),
  nivel   nivel,          -- null em problema do plano inteiro (ex.: id inexistente no núcleo)
  regra   regra_plano not null,
  detalhe text not null,  -- a mensagem devolvida ao planejador
  primary key (run_id, rodada, ordem)
);

-- PlanoEditorial (docs/arquiteturas.md, Plano editorial). Um por run, gravado depois do gate e nunca
-- regerado no retry das células (docs/arquiteturas.md, Regras do loop).
create table planos_editoriais (
  run_id  text primary key references factsheets (run_id),
  nucleo  text[] not null check (cardinality(nucleo) between 1 and 3),
  rodadas int not null check (rodadas between 1 and 3),
  aviso   text  -- o que o gate ainda acusava depois da última rodada; null quando passou. Mostrado na E8.
);

-- PlanoNivel, um por nível, compartilhado pelas 3 células do nível.
create table planos_nivel (
  run_id         text not null references planos_editoriais (run_id),
  nivel          nivel not null,
  tese           text not null,
  tese_claim_ids text[] not null,
  claims         jsonb not null,                -- list[ClaimPlanejado]: {claim_id, papel, atribuir_fonte}, em ordem de leitura
  descartes      jsonb not null default '[]',   -- list[Descarte]: {claim_id, motivo, relacionado, origem}, auditoria na E8
  analogias      jsonb not null default '[]',   -- list[Analogia]: {termo, explicacao, origem, glossario_ref, revisada}
  primary key (run_id, nivel)
);

-- Estado da célula (CelulaState, docs/arquiteturas.md, O objeto de estado). Sempre 9 por run, criadas pela E4.
create table celulas (
  run_id             text not null references runs (run_id),
  celula_id          text not null,
  nivel              nivel not null,
  formato            formato not null,
  estado             estado_celula not null default 'PENDENTE',
  tentativa          int not null default 0 check (tentativa >= 0),
  -- PENDENTE: motivo de REPROVADA (incluindo não convergência, docs/arquiteturas.md, E7. Router, Regras do loop e Ciclo de vida de uma célula)
  -- não tem campo na documentação.
  motivo_reprovacao  text,
  -- PENDENTE: contador de retry de parse separado de K (docs/arquiteturas.md, 1. Model client / router) sem campo definido.
  tentativas_parse   int not null default 0,
  atualizado_em      timestamptz not null default now(),
  primary key (run_id, celula_id),
  check (celula_id = nivel::text || '__' || formato::text)
);

comment on column celulas.tentativa is
  'Última tentativa gerada. 0 = nenhuma. PENDENTE: CelulaState começa em 0 e Peca é 1-indexada.';

-- Peca, uma linha por tentativa (docs/arquiteturas.md, Peça final). O histórico é necessário para o
-- Router comparar violações de tentativas seguidas (docs/arquiteturas.md, E7. Router).
create table pecas (
  run_id    text not null,
  celula_id text not null,
  tentativa int not null check (tentativa >= 1),
  nivel     nivel not null,
  formato   formato not null,
  payload   jsonb not null,   -- ArtigoPayload | CarrosselPayload | RoteiroPayload, discriminado por formato
  claim_ids text[] not null,  -- união dos claim_ids internos
  criado_em timestamptz not null default now(),
  primary key (run_id, celula_id, tentativa),
  foreign key (run_id, celula_id) references celulas (run_id, celula_id),
  check (celula_id = nivel::text || '__' || formato::text)
);

-- Rastreabilidade granular: cada unidade estrutural (seção, slide, cena) ligada aos seus claims
-- (docs/arquiteturas.md, E5. Format Synthesizer, E8. Revisão humana e 7. Observability / tracing). Permite consulta claim -> peças e claim -> página do PDF.
create table peca_unidade_claims (
  run_id         text not null,
  celula_id      text not null,
  tentativa      int not null,
  unidade_indice int not null check (unidade_indice >= 0),  -- posição da seção, slide ou cena no payload
  claim_id       text not null,
  primary key (run_id, celula_id, tentativa, unidade_indice, claim_id),
  foreign key (run_id, celula_id, tentativa) references pecas (run_id, celula_id, tentativa),
  foreign key (run_id, claim_id) references claims (run_id, claim_id)
);

create index peca_unidade_claims_claim_idx on peca_unidade_claims (run_id, claim_id);

-- Peca.numeros_usados (docs/arquiteturas.md, Peça final e E5.3 Montagem): cada número do texto final,
-- vindo de um marcador {{<claim_id>.<ordem>}}, com a posição no texto da unidade e o número do claim.
-- Pelo claim_numeros chega à entrada de numeros e à âncora no PDF; a M1 confere que todo número da peça
-- que não seja data está aqui.
create table peca_unidade_numeros (
  run_id         text not null,
  celula_id      text not null,
  tentativa      int not null,
  unidade_indice int not null check (unidade_indice >= 0),
  inicio         int not null check (inicio >= 0),
  fim            int not null,
  claim_id       text not null,
  ordem          int not null check (ordem >= 1),
  primary key (run_id, celula_id, tentativa, unidade_indice, inicio),
  foreign key (run_id, celula_id, tentativa) references pecas (run_id, celula_id, tentativa),
  foreign key (run_id, claim_id, ordem) references claim_numeros (run_id, claim_id, ordem),
  check (fim > inicio)
);
