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

-- Saída do planejador em cada rodada do gate, inclusive a que passou (auditoria do que cada correção mudou).
create table rodadas_plano (
  run_id text not null references runs (run_id),
  rodada int not null check (rodada between 1 and 3),
  saida  jsonb not null,  -- SaidaPlanejador, como o modelo devolveu
  primary key (run_id, rodada)
);

-- PlanoEditorial (docs/arquiteturas.md, Plano editorial). Um por run, gravado depois do gate e nunca
-- regerado no retry das células (docs/arquiteturas.md, Regras do loop).
create table planos_editoriais (
  run_id  text primary key references factsheets (run_id),
  nucleo  text[] not null check (cardinality(nucleo) between 1 and 3),
  rodadas int not null check (rodadas between 1 and 3),
  aviso   text  -- o que a limpeza do código mudou depois da última rodada; null quando passou. Mostrado na matriz do dashboard.
);

-- PlanoNivel, um por nível, compartilhado pelas 3 células do nível.
create table planos_nivel (
  run_id         text not null references planos_editoriais (run_id),
  nivel          nivel not null,
  tese           text not null,
  tese_claim_ids text[] not null,
  claims         jsonb not null,                -- list[ClaimPlanejado]: {claim_id, papel, prioridade, atribuir_fonte}, em ordem de leitura
  descartes      jsonb not null default '[]',   -- list[Descarte]: {claim_id, motivo, relacionado, origem}, auditoria na matriz do dashboard
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
  motivo_reprovacao  motivo_reprovacao,  -- só em REPROVADA; a tentativa que explica é `tentativa`
  erro_classe        classe_falha,       -- só em ERRO_INFRA
  erro_mensagem      text,               -- só em ERRO_INFRA
  atualizado_em      timestamptz not null default now(),  -- escrito por persistencia.atualizar_celula
  primary key (run_id, celula_id),
  check (celula_id = nivel::text || '__' || formato::text),
  check ((estado = 'REPROVADA') = (motivo_reprovacao is not null)),
  check ((estado = 'ERRO_INFRA') = (erro_classe is not null)),
  check ((erro_classe is null) = (erro_mensagem is null))
);

comment on column celulas.estado is
  'APROVADA, REPROVADA e ERRO_INFRA são finais e gravados só pela E7 (docs/arquiteturas.md, E7. Router).';

comment on column celulas.tentativa is
  'Número da última peça gerada (pecas.tentativa, 1-indexada). 0 = nenhuma. Atualizado na mesma transação que grava a peça.';

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
-- (docs/arquiteturas.md, E5. Format Synthesizer, Matriz no dashboard e 6. Observability / tracing). Permite consulta claim -> peças e claim -> página do PDF.
-- Só os claims que existem no FactSheet do run: um id que o redator inventou fica em pecas.payload e
-- pecas.claim_ids e é violação do plan_contract, mas não tem página para rastrear.
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
-- vindo de um marcador {{<claim_id>.<ordem>}}, com o campo (texto da unidade ou título do artigo), a posição
-- no campo e o número do claim. Pelo claim_numeros chega à entrada de numeros e à âncora no PDF; a M1 confere
-- que todo número da peça que não seja data está aqui. O título não tem unidade, por isso a chave é o id, e a
-- unicidade da posição trata o unidade_indice nulo como um valor.
create table peca_unidade_numeros (
  id             bigint generated always as identity primary key,
  run_id         text not null,
  celula_id      text not null,
  tentativa      int not null,
  campo          campo_numero not null,
  unidade_indice int check (unidade_indice >= 0),  -- posição da seção, slide ou cena; null no título
  inicio         int not null check (inicio >= 0),
  fim            int not null,
  claim_id       text not null,
  ordem          int not null check (ordem >= 1),
  unique nulls not distinct (run_id, celula_id, tentativa, campo, unidade_indice, inicio),
  foreign key (run_id, celula_id, tentativa) references pecas (run_id, celula_id, tentativa),
  foreign key (run_id, claim_id, ordem) references claim_numeros (run_id, claim_id, ordem),
  check ((campo = 'titulo') = (unidade_indice is null)),
  check (fim > inicio)
);
