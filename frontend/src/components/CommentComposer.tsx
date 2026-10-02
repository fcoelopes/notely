import { useEffect, useRef, useState } from "react";
import type { AnnotationType, SelectionDraft } from "../types";
import { annotationLabel } from "./AnnotationOverlay";

interface Props {
  draft: SelectionDraft;
  type: AnnotationType;
  saving: boolean;
  onCancel: () => void;
  onSave: (comment: string | null) => void;
}

const placeholders: Partial<Record<AnnotationType, string>> = {
  note: "O que você quer guardar deste trecho?",
  question: "Qual é a sua dúvida?",
  disagreement: "Com o que você não concorda?",
};

export function CommentComposer({ draft, type, saving, onCancel, onSave }: Props) {
  const [comment, setComment] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const required = type === "question";
  const trimmed = comment.trim();
  const canSave = !saving && (!required || trimmed.length > 0);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const submit = () => {
    if (!canSave) return;
    onSave(required ? trimmed : trimmed || null);
  };

  return (
    <form
      className="comment-composer"
      onKeyDown={(event) => {
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
          event.preventDefault();
          submit();
        }
      }}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      style={{ left: draft.toolbarX, top: draft.toolbarY }}
    >
      <div className="comment-composer-heading">
        <span className={`annotation-kind annotation-kind--${type}`}>{annotationLabel(type)}</span>
        <q>{draft.quote}</q>
      </div>
      <textarea
        aria-label={placeholders[type] ?? "Comentário"}
        disabled={saving}
        onChange={(event) => setComment(event.target.value)}
        placeholder={placeholders[type] ?? "Adicione seu comentário"}
        ref={textareaRef}
        rows={3}
        value={comment}
      />
      <div className="comment-composer-actions">
        <small>{required && !trimmed ? "Escreva a dúvida para salvar." : "Ctrl + Enter para salvar"}</small>
        <div>
          <button className="composer-cancel" onClick={onCancel} type="button">Cancelar</button>
          <button className="composer-save" disabled={!canSave} type="submit">
            {saving ? "Salvando…" : "Salvar"}
          </button>
        </div>
      </div>
    </form>
  );
}
