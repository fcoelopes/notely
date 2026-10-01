import type { AnnotationType, SelectionDraft } from "../types";

const actions: Array<{ type: AnnotationType; label: string; key: string }> = [
  { type: "highlight", label: "Destacar", key: "H" },
  { type: "note", label: "Nota", key: "N" },
  { type: "question", label: "Dúvida", key: "?" },
  { type: "important", label: "Importante", key: "!" },
  { type: "disagreement", label: "Discordo", key: "×" },
];

interface Props {
  draft: SelectionDraft;
  saving: boolean;
  onChoose: (type: AnnotationType) => void;
}

export function SelectionToolbar({ draft, saving, onChoose }: Props) {
  return (
    <div
      className="selection-toolbar"
      role="toolbar"
      aria-label="Criar anotação"
      style={{ left: draft.toolbarX, top: draft.toolbarY }}
    >
      {actions.map((action) => (
        <button
          className={`selection-action selection-action--${action.type}`}
          disabled={saving}
          key={action.type}
          onClick={() => onChoose(action.type)}
          title={action.label}
          type="button"
        >
          <span aria-hidden="true">{action.key}</span>
          <span>{action.label}</span>
        </button>
      ))}
    </div>
  );
}

