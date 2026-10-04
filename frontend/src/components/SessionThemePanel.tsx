import { useEffect, useState } from "react";
import type { StudySessionDetail, ThemeSuggestion } from "../types";

interface Props {
  session: StudySessionDetail;
  suggesting: boolean;
  saving: boolean;
  onEditTheme: (theme: string) => void;
  onRequestSuggestion: () => void;
  onAcceptSuggestion: (suggestionId: string) => void;
  onRejectSuggestion: (suggestionId: string) => void;
}

export function SessionThemePanel({
  session,
  suggesting,
  saving,
  onEditTheme,
  onRequestSuggestion,
  onAcceptSuggestion,
  onRejectSuggestion,
}: Props) {
  const [draft, setDraft] = useState(session.theme ?? "");
  const pending = session.suggestions.find((suggestion) => suggestion.status === "pending") ?? null;

  useEffect(() => {
    setDraft(session.theme ?? "");
  }, [session.theme]);

  return (
    <section className="theme-panel">
      <div className="theme-heading">
        <p className="eyebrow">Tema da sessão</p>
        <span className={`theme-origin theme-origin--${session.theme_origin ?? "none"}`}>
          {session.theme_origin === "ai_suggestion"
            ? "aceito de uma sugestão"
            : session.theme_origin === "user"
              ? "escrito por você"
              : "não definido"}
        </span>
      </div>

      <form
        className="theme-form"
        onSubmit={(event) => {
          event.preventDefault();
          const value = draft.trim();
          if (value && value !== session.theme) onEditTheme(value);
        }}
      >
        <input
          aria-label="Tema da sessão"
          maxLength={200}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Descreva o tema desta leitura"
          value={draft}
        />
        <button disabled={saving || !draft.trim() || draft.trim() === session.theme} type="submit">
          Salvar tema
        </button>
      </form>

      <button
        className="suggest-action"
        disabled={suggesting || session.documents.length === 0}
        onClick={onRequestSuggestion}
        type="button"
      >
        {suggesting ? "Analisando a sessão…" : "Sugerir tema com IA"}
      </button>
      {session.documents.length === 0 && (
        <small className="theme-hint">Traga ao menos um documento para pedir uma sugestão.</small>
      )}

      {pending && <SuggestionCard onAccept={onAcceptSuggestion} onReject={onRejectSuggestion} suggestion={pending} />}
    </section>
  );
}

function SuggestionCard({
  suggestion,
  onAccept,
  onReject,
}: {
  suggestion: ThemeSuggestion;
  onAccept: (suggestionId: string) => void;
  onReject: (suggestionId: string) => void;
}) {
  return (
    <article className="suggestion-card">
      <p className="suggestion-label">
        Sugestão · {suggestion.provider}/{suggestion.model}
      </p>
      <strong>{suggestion.payload.theme ?? "sem tema"}</strong>
      {suggestion.payload.rationale && <p>{suggestion.payload.rationale}</p>}
      <small>Ela só entra na sessão se você aceitar. Você pode editar depois.</small>
      <div className="suggestion-actions">
        <button onClick={() => onAccept(suggestion.id)} type="button">
          Aceitar
        </button>
        <button className="ghost-action" onClick={() => onReject(suggestion.id)} type="button">
          Descartar
        </button>
      </div>
    </article>
  );
}
