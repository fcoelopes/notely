import { useCallback, useEffect, useState } from "react";
import { Document, Page } from "react-pdf";
import type {
  Annotation,
  ClaimEvidenceRelation,
  CuratedSource,
  QuestionSources as SourcesState,
} from "../types";
import { documentContentUrl, getQuestionSources, retryQuestionSources } from "../lib/api";
import { annotationLabel } from "./AnnotationOverlay";

interface Props {
  annotations: Annotation[];
  pageNumber: number;
  sessionId: string;
  availableDocumentIds: string[];
  onGoToPage: (page: number) => void;
  onGoToSource: (documentId: string, page: number) => void;
  /** Afirma a partir do trecho. `false` diz que a gravação falhou e o rascunho fica. */
  onAssert: (
    annotationId: string,
    text: string,
    relation: ClaimEvidenceRelation,
  ) => Promise<boolean>;
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

export function AnnotationPanel({ annotations, pageNumber, sessionId, availableDocumentIds, onGoToPage, onGoToSource, onAssert }: Props) {
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
              <AssertAction annotation={annotation} onAssert={onAssert} />
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

/**
 * Afirmar a partir do trecho anotado.
 *
 * A afirmação nasce de uma anotação existente: o trecho é a evidência do que a pessoa leu.
 * Enquanto a gravação não termina bem, o formulário continua aberto com o texto escrito — o
 * domínio reaproveita a afirmação quando o texto já existe, e nada se perde por tentar de
 * novo.
 */
function AssertAction({
  annotation,
  onAssert,
}: {
  annotation: Annotation;
  onAssert: (annotationId: string, text: string, relation: ClaimEvidenceRelation) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [relation, setRelation] = useState<ClaimEvidenceRelation>("supports");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const clean = text.trim();
    if (!clean || saving) return;
    setSaving(true);
    setFailed(false);
    try {
      if (await onAssert(annotation.id, clean, relation)) {
        setOpen(false);
        setText("");
        setRelation("supports");
      } else {
        setFailed(true);
      }
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    return (
      <div className="annotation-actions">
        <button
          aria-label={`Afirmar a partir deste trecho da página ${annotation.page_number}`}
          className="assert-toggle"
          onClick={() => setOpen(true)}
          type="button"
        >
          Afirmar
        </button>
      </div>
    );
  }

  return (
    <form className="assert-form" onSubmit={(event) => void submit(event)}>
      <label>
        Afirmação
        <textarea
          aria-label="Texto da afirmação"
          autoFocus
          maxLength={2000}
          onChange={(event) => setText(event.target.value)}
          placeholder="O que este trecho permite afirmar"
          value={text}
        />
      </label>
      <label>
        Relação com o trecho
        <select
          aria-label="Relação com o trecho"
          onChange={(event) => setRelation(event.target.value as ClaimEvidenceRelation)}
          value={relation}
        >
          <option value="supports">sustenta</option>
          <option value="contradicts">contradiz</option>
        </select>
      </label>
      <small>Afirmar o mesmo texto de novo só acrescenta esta evidência à afirmação existente.</small>
      {failed && (
        <p className="assert-failure" role="alert">
          Não foi possível gravar a afirmação. O texto foi mantido para tentar de novo.
        </p>
      )}
      <div className="assert-form-actions">
        <button
          className="assert-cancel"
          disabled={saving}
          onClick={() => { setOpen(false); setText(""); setRelation("supports"); setFailed(false); }}
          type="button"
        >
          Cancelar
        </button>
        <button className="assert-save" disabled={saving || !text.trim()} type="submit">
          {saving ? "Salvando…" : "Salvar afirmação"}
        </button>
      </div>
    </form>
  );
}
