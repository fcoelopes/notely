import { useCallback, useEffect, useRef, useState } from "react";
import { endReadingSessionBeacon, startReadingSession, updateReadingSession } from "./api";

const DEFAULT_IDLE_TIMEOUT_MS = 20 * 60 * 1000;
const DEFAULT_TOUCH_INTERVAL_MS = 30 * 1000;

function readPositiveNumber(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export const READING_IDLE_TIMEOUT_MS = readPositiveNumber(
  import.meta.env.VITE_READING_IDLE_TIMEOUT_MS,
  DEFAULT_IDLE_TIMEOUT_MS,
);

export const READING_TOUCH_INTERVAL_MS = readPositiveNumber(
  import.meta.env.VITE_READING_TOUCH_INTERVAL_MS,
  DEFAULT_TOUCH_INTERVAL_MS,
);

interface Options {
  documentId: string | null;
  filename: string | null;
  pageNumber: number;
  onError?: (message: string) => void;
}

interface ReadingSessionState {
  sessionId: string | null;
  noteActivity: (pageNumber?: number) => void;
  end: () => void;
}

/**
 * Sessão de leitura do documento ativo.
 *
 * O upload não conta como leitura: a sessão começa na primeira interação real do usuário
 * com o documento. Enquanto há atividade, `last_activity_at` é atualizado com throttle.
 * A sessão termina por ação explícita, ao fechar a página (best-effort) ou por timeout
 * de inatividade configurável em `VITE_READING_IDLE_TIMEOUT_MS`.
 */
export function useReadingSession({
  documentId,
  filename,
  pageNumber,
  onError,
}: Options): ReadingSessionState {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const documentIdRef = useRef<string | null>(null);
  const pageRef = useRef(pageNumber);
  const lastTouchRef = useRef(0);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const endingRef = useRef(false);

  pageRef.current = pageNumber;

  const clearIdleTimer = useCallback(() => {
    if (idleTimerRef.current !== null) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
  }, []);

  const endSession = useCallback(
    (session: string | null = sessionIdRef.current) => {
      if (!session) return;
      sessionIdRef.current = null;
      setSessionId(null);
      clearIdleTimer();
      void updateReadingSession(session, { ended: true }).catch(() => undefined);
    },
    [clearIdleTimer],
  );

  const scheduleIdleEnd = useCallback(() => {
    clearIdleTimer();
    idleTimerRef.current = setTimeout(() => {
      endSession();
    }, READING_IDLE_TIMEOUT_MS);
  }, [clearIdleTimer, endSession]);

  const noteActivity = useCallback(
    (nextPage?: number) => {
      if (!documentId || endingRef.current) return;
      const page = nextPage ?? pageRef.current ?? 1;
      const current = sessionIdRef.current;

      if (current === null) {
        sessionIdRef.current = `pending:${documentId}`;
        void startReadingSession({
          document_id: documentId,
          page_number: page,
          filename,
        })
          .then((session) => {
            // O documento pode ter mudado enquanto a sessão era criada.
            if (documentIdRef.current !== documentId) {
              void updateReadingSession(session.id, { ended: true }).catch(() => undefined);
              return;
            }
            sessionIdRef.current = session.id;
            setSessionId(session.id);
            lastTouchRef.current = Date.now();
            scheduleIdleEnd();
          })
          .catch((caught: unknown) => {
            sessionIdRef.current = null;
            onError?.(
              caught instanceof Error && caught.message
                ? caught.message
                : "Não foi possível registrar a sessão de leitura.",
            );
          });
        return;
      }

      if (current.startsWith("pending:")) return;

      scheduleIdleEnd();
      if (Date.now() - lastTouchRef.current < READING_TOUCH_INTERVAL_MS) return;
      lastTouchRef.current = Date.now();
      void updateReadingSession(current, { page_number: page }).catch(() => undefined);
    },
    [documentId, filename, onError, scheduleIdleEnd],
  );

  // Trocar de documento encerra a sessão anterior antes de começar a próxima.
  useEffect(() => {
    const previous = documentIdRef.current;
    if (previous !== documentId) {
      endSession();
      documentIdRef.current = documentId;
      lastTouchRef.current = 0;
    }
  }, [documentId, endSession]);

  // Fechar ou esconder a página encerra a sessão sem poder esperar resposta.
  useEffect(() => {
    const finish = () => {
      const current = sessionIdRef.current;
      if (current && !current.startsWith("pending:")) {
        endReadingSessionBeacon(current);
      }
      sessionIdRef.current = null;
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") finish();
    };
    window.addEventListener("pagehide", finish);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", finish);
      document.removeEventListener("visibilitychange", onVisibility);
      clearIdleTimer();
    };
  }, [clearIdleTimer]);

  const end = useCallback(() => {
    endingRef.current = true;
    endSession();
    endingRef.current = false;
  }, [endSession]);

  return { sessionId, noteActivity, end };
}
