import type { Annotation } from "../types";
import { annotationLabel } from "./AnnotationOverlay";

interface Props {
  annotations: Annotation[];
  pageNumber: number;
  onGoToPage: (page: number) => void;
}

export function AnnotationPanel({ annotations, pageNumber, onGoToPage }: Props) {
  return (
    <aside className="annotation-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Caderno de leitura</p>
          <h2>Anotações</h2>
        </div>
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
              <button
                className={`annotation-card ${annotation.page_number === pageNumber ? "is-current" : ""}`}
                onClick={() => onGoToPage(annotation.page_number)}
                type="button"
              >
                <span className={`annotation-kind annotation-kind--${annotation.type}`}>
                  {annotationLabel(annotation.type)}
                </span>
                <q>{annotation.quote}</q>
                {annotation.comment && <p>{annotation.comment}</p>}
                <small>Página {annotation.page_number}</small>
              </button>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}

