import type { Annotation, AnnotationType } from "../types";

const typeLabels: Record<AnnotationType, string> = {
  highlight: "Destaque",
  note: "Nota",
  question: "Dúvida",
  important: "Importante",
  disagreement: "Discordância",
  relation: "Relação",
};

export function annotationLabel(type: AnnotationType): string {
  return typeLabels[type];
}

export function AnnotationOverlay({ annotations }: { annotations: Annotation[] }) {
  return (
    <div className="annotation-layer" aria-label="Marcações da página">
      {annotations.flatMap((annotation) =>
        annotation.position.rects.map((rect, index) => (
          <span
            className={`annotation-mark annotation-mark--${annotation.type}`}
            key={`${annotation.id}-${index}`}
            title={`${annotationLabel(annotation.type)}: ${annotation.quote}`}
            style={{
              left: `${rect.x * 100}%`,
              top: `${rect.y * 100}%`,
              width: `${rect.width * 100}%`,
              height: `${rect.height * 100}%`,
            }}
          />
        )),
      )}
    </div>
  );
}

