BEGIN;

-- Entrega do outbox por consumidor: o mesmo evento passa a ser projetado por mais de um
-- worker. A decisão de entrega sai de outbox_events.processed_at e passa a viver aqui,
-- com retry e backoff independentes por consumidor.
CREATE TABLE outbox_deliveries (
    event_id uuid NOT NULL REFERENCES outbox_events(id) ON DELETE CASCADE,
    consumer text NOT NULL CHECK (length(btrim(consumer)) > 0),
    processed_at timestamptz,
    claimed_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at timestamptz NOT NULL,
    last_error text,
    PRIMARY KEY (event_id, consumer)
);

CREATE INDEX outbox_deliveries_pending_idx
    ON outbox_deliveries (consumer, available_at)
    WHERE processed_at IS NULL;

-- Adoção do histórico: eventos já processados contam como entregues a todos os
-- consumidores existentes. Consumidores novos começam do zero e usam backfill explícito
-- em vez de reprojetar o acervo inteiro.
INSERT INTO outbox_deliveries (event_id, consumer, processed_at, attempt_count, available_at)
SELECT event.id, owners.consumer, event.processed_at, 0, event.created_at
FROM outbox_events AS event
CROSS JOIN (VALUES ('timescale'), ('corpus'), ('curation'), ('graph')) AS owners(consumer)
WHERE event.processed_at IS NOT NULL;

COMMIT;
