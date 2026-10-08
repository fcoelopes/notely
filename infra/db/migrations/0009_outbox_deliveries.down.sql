BEGIN;

-- O log de eventos permanece intacto; apenas o estado de entrega por consumidor é
-- removido. O código anterior volta a decidir por outbox_events.processed_at.
DROP INDEX IF EXISTS outbox_deliveries_pending_idx;
DROP TABLE IF EXISTS outbox_deliveries;

COMMIT;
