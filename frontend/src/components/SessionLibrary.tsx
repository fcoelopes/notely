import { useState } from "react";
import type { DocumentSummary, StudySession } from "../types";

interface Props {
  documents: DocumentSummary[];
  sessions: StudySession[];
  busy: boolean;
  onResumeSession: (sessionId: string) => void;
  onStartSession: (theme: string | null) => void;
}

export function SessionLibrary({ documents, sessions, busy, onResumeSession, onStartSession }: Props) {
  const [theme, setTheme] = useState("");

  return (
    <main className="session-library">
      <section className="library-start">
        <p className="eyebrow">Sessão de estudo</p>
        <h1>Leia vários documentos sobre o mesmo tema.</h1>
        <p className="empty-description">
          Uma sessão reúne os PDFs de um mesmo assunto. O tema é seu: escreva agora ou peça uma
          sugestão depois de trazer os documentos.
        </p>

        <form
          className="session-form"
          onSubmit={(event) => {
            event.preventDefault();
            onStartSession(theme.trim() || null);
          }}
        >
          <label htmlFor="session-theme">Tema da sessão (opcional)</label>
          <input
            id="session-theme"
            maxLength={200}
            onChange={(event) => setTheme(event.target.value)}
            placeholder="Ex.: IFRS, recuperação de informação, embeddings"
            value={theme}
          />
          <button className="primary-action" disabled={busy} type="submit">
            {busy ? "Preparando…" : "Começar sessão"}
          </button>
        </form>
      </section>

      <section className="library-columns">
        <div className="library-block">
          <h2>Sessões abertas</h2>
          {sessions.length === 0 ? (
            <p className="library-empty">Nenhuma sessão ainda.</p>
          ) : (
            <ul className="session-list">
              {sessions.map((session) => (
                <li key={session.id}>
                  <button onClick={() => onResumeSession(session.id)} type="button">
                    <strong>{session.theme ?? "Sem tema definido"}</strong>
                    <small>
                      {session.theme_origin === "ai_suggestion"
                        ? "tema aceito de uma sugestão"
                        : session.theme
                          ? "tema escrito por você"
                          : "defina o tema quando quiser"}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="library-block">
          <h2>Documentos já ingeridos</h2>
          {documents.length === 0 ? (
            <p className="library-empty">
              Nenhum documento ainda. Abra uma sessão e traga os primeiros PDFs.
            </p>
          ) : (
            <ul className="document-list">
              {documents.map((document) => (
                <li key={document.id}>
                  <strong>{document.title}</strong>
                  <small>
                    {document.page_count} página{document.page_count > 1 ? "s" : ""}
                  </small>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </main>
  );
}
