import { useCallback, useEffect, useState } from "react";
import { Document, Page } from "react-pdf";
import type { Annotation, CuratedSource, QuestionSources as SourcesState } from "../types";
import { documentContentUrl, getQuestionSources, retryQuestionSources } from "../lib/api";
import { annotationLabel } from "./AnnotationOverlay";

interface Props {
  annotations: Annotation[];
  pageNumber: number;
  sessionId: string;
  availableDocumentIds: string[];
  onGoToPage: (page: number) => void;
  onGoToSource: (documentId: string, page: number) => void;
}

function QuestionSources({
  annotationId, sessionId, availableDocumentIds, onGoToSource,
}: {
  annotationId: string;
  sessionId: string;
  availableDocumentIds: string[];
  onGoToSource: (documentId: string, page: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<SourcesState | null>(null);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<CuratedSource | null>(null);
  const [previewError, setPreviewError] = useState(false);

  const loadSources = useCallback(async () => {
    try {
      setState(await getQuestionSources(sessionId, annotationId));
      setError(false);
    } catch { setError(true); }
  }, [sessionId, annotationId]);

  useEffect(() => { void loadSources(); }, [loadSources]);

  useEffect(() => {
    if (!open || state?.status !== "pending") return;
    const timer = window.setInterval(() => { void loadSources(); }, 2500);
    return () => window.clearInterval(timer);
  }, [open, state?.status, loadSources]);

  const retry = async () => {
    try {
      setState(await retryQuestionSources(sessionId, annotationId));
      setError(false);
      setSelected(null);
    } catch { setError(true); }
  };

  return (
    <div className="question-sources">
      <button type="button" className="sources-toggle" aria-expanded={open} onClick={() => { if (!open) void loadSources(); setOpen(!open); }}>
        {open ? "Ocultar fontes" : "Ver fontes"}
        <span className="source-state" aria-live="polite">
          {error ? " · indisponível" : state?.status === "pending" ? " · em busca"
            : state?.status === "ready" ? " · prontas" : state?.status === "no_source" ? " · sem fonte"
              : state?.status === "failed" ? " · indisponível" : ""}
        </span>
      </button>
      {open && (
        <div className="sources-content">
          <strong>Fontes sugeridas</strong>
          {!state && !error && <p>Consultando fontes…</p>}
          {state?.status === "pending" && <p>{state.last_error === "Indexação em andamento" ? "Indexação em andamento" : "Procurando fontes nesta sessão…"}</p>}
          {state?.status === "no_source" && <p>Nenhuma fonte útil encontrada nesta sessão.</p>}
          {state?.status === "failed" && <p role="status">Curadoria indisponível. Sua dúvida continua salva.</p>}
          {error && <p role="alert">Não foi possível consultar as fontes.</p>}
          {(state?.status === "failed" || state?.status === "no_source") && (
            <button type="button" onClick={() => void retry()}>Tentar novamente</button>
          )}
          {error && <button type="button" onClick={() => void loadSources()}>Atualizar</button>}
          {state?.status === "ready" && !error && (
            <ol className="sources-list">
              {state.sources.map((source) => (
                <li key={source.id}>
                  <button type="button" disabled={!source.available || !availableDocumentIds.includes(source.document_id)} onClick={() => { setSelected(source); setPreviewError(false); }}>
                    <strong>{source.document_title}</strong> · página {source.page_number}
                  </button>
                  <p><span>Trecho do PDF:</span> “{source.excerpt}”</p>
                  <p><span>Motivo ({source.provider === "lexical_search" ? "busca lexical" : "IA"}):</span> {source.reason}</p>
                  <small>{source.provider} · {source.model}</small>
                  {(!source.available || !availableDocumentIds.includes(source.document_id)) && <small>Fonte indisponível nesta sessão</small>}
                </li>
              ))}
            </ol>
          )}
          {selected?.available && !error && availableDocumentIds.includes(selected.document_id) && (
            <div className="source-preview">
              <div className="source-preview-heading">
                <strong>Prévia · página {selected.page_number}</strong>
                <button type="button" onClick={() => setSelected(null)} aria-label="Fechar prévia">Fechar</button>
              </div>
              {previewError ? <p role="alert">Não foi possível abrir a prévia.</p> : (
                <Document file={documentContentUrl(selected.document_id)} suspense={false}
                  loading={<p>Carregando prévia…</p>}
                  error={<p role="alert">Não foi possível abrir a prévia.</p>}
                  onLoadError={() => setPreviewError(true)}>
                  <Page pageNumber={selected.page_number} width={260} suspense={false}
                    renderTextLayer={false} renderAnnotationLayer={false}
                    onLoadError={() => setPreviewError(true)} />
                </Document>
              )}
              <button type="button" onClick={() => onGoToSource(selected.document_id, selected.page_number)}>
                Abrir no Reader
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function AnnotationPanel({ annotations, pageNumber, sessionId, availableDocumentIds, onGoToPage, onGoToSource }: Props) {
  return (
    <aside className="annotation-panel">
      <div className="panel-heading">
        <div><p className="eyebrow">Caderno de leitura</p><h2>Anotações</h2></div>
        <span className="annotation-count">{annotations.length}</span>
      </div>
      {annotations.length === 0 ? (
        <div className="panel-empty">
          <span className="panel-empty-glyph">Aa</span>
          <p>Selecione um trecho para começar.</p>
          <small>Suas marcações aparecerão aqui e continuarão disponíveis ao reabrir o PDF.</small>
        </div>
      ) : (
        <ol className="annotation-list">
          {annotations.map((annotation) => (
            <li key={annotation.id}>
              <button className={`annotation-card ${annotation.page_number === pageNumber ? "is-current" : ""}`}
                onClick={() => onGoToPage(annotation.page_number)} type="button">
                <span className={`annotation-kind annotation-kind--${annotation.type}`}>
                  {annotationLabel(annotation.type)}
                </span>
                <q>{annotation.quote}</q>
                {annotation.comment && <p>{annotation.comment}</p>}
                <small>Página {annotation.page_number}</small>
              </button>
              {annotation.type === "question" && annotation.study_session_id === sessionId && (
                <QuestionSources annotationId={annotation.id} sessionId={sessionId}
                  availableDocumentIds={availableDocumentIds} onGoToSource={onGoToSource} />
              )}
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
