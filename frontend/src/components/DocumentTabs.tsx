import type { SessionDocument, SessionProgress } from "../types";

interface Props {
  documents: SessionDocument[];
  activeDocumentId: string | null;
  progress: SessionProgress | null;
  onSelect: (documentId: string) => void;
  onClose: (documentId: string) => void;
}

export function DocumentTabs({ documents, activeDocumentId, progress, onSelect, onClose }: Props) {
  return (
    <div aria-label="Documentos da sessão" className="document-tabs" role="tablist">
      {documents.map((document) => (
        <div
          className={`document-tab ${document.document_id === activeDocumentId ? "is-active" : ""}`}
          key={document.document_id}
        >
          <button
            aria-selected={document.document_id === activeDocumentId}
            className="document-tab-open"
            onClick={() => onSelect(document.document_id)}
            role="tab"
            title={document.filename}
            type="button"
          >
            <span className="document-tab-title">{document.title}</span>
            <small title={progress ? `${progress.documents[document.document_id]?.viewed_pages ?? 0}/${document.page_count} páginas visualizadas` : "Progresso indisponível"}>{progress ? `${progress.documents[document.document_id]?.percent ?? 0}%` : "—"}</small>
          </button>
          <button
            aria-label={`Remover ${document.title} da sessão`}
            className="document-tab-close"
            onClick={() => onClose(document.document_id)}
            title="Remover da sessão"
            type="button"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
