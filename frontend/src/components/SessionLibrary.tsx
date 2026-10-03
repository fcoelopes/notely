import { useState } from "react";
import type { DocumentSummary, StudySession } from "../types";

interface Props {
  documents: DocumentSummary[];
  sessions: StudySession[];
  busy: boolean;
  onResumeSession: (sessionId: string) => void;
  onStartSession: (theme: string | null) => void;
}

type View = "all" | "sessions" | "documents";

export function SessionLibrary({ documents, sessions, busy, onResumeSession, onStartSession }: Props) {
  const [theme, setTheme] = useState("");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>("all");
  const normalize = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
  const matches = (value: string) => normalize(value).includes(normalize(query.trim()));
  const filteredSessions = sessions.filter((session) => matches(session.theme ?? "Sem tema definido"));
  const filteredDocuments = documents.filter((document) => matches(`${document.title} ${document.filename}`));

  return (
    <main className="session-library">
      <aside className="library-nav" aria-label="Biblioteca">
        <p className="nav-caption">ESPAÇO DE ESTUDO</p>
        <nav aria-label="Navegação da biblioteca">
          {([
            ["all", "Visão geral", null],
            ["sessions", "Sessões de estudo", sessions.length],
            ["documents", "Documentos", documents.length],
          ] as const).map(([id, label, count]) => (
            <button key={id} type="button" aria-label={count === null ? label : `${label} ${count}`} aria-current={view === id ? "page" : undefined} onClick={() => setView(id)}>
              <span>{label}</span>{count !== null && <small>{count}</small>}
            </button>
          ))}
        </nav>
        <div className="nav-note"><span className="nav-note-mark" aria-hidden="true">N</span><p>O conhecimento começa<br />com a sua leitura.</p><small>Leia. Anote. Conecte.</small></div>
      </aside>

      <div className="library-content">
        <div className="library-topline"><span>Seu espaço de leitura</span><span className="workspace-label">Biblioteca pessoal</span></div>
        <section className="library-welcome">
          <div><p className="eyebrow">UM TRECHO. UMA IDEIA. UMA CONEXÃO.</p><h1>Guarde o que<br /><em>importa para você.</em></h1><p>Reúna seus documentos, acompanhe suas leituras e dê um lugar às ideias que merecem ficar.</p></div>
          <div className="library-illustration" aria-hidden="true"><div className="illustration-sheet"><span>NOTAS DE LEITURA</span><i /><i /><i /><mark>Uma ideia para guardar.</mark><i /><i /><div className="illustration-note">voltar a este trecho</div></div><span className="illustration-caption">Seu olhar dá sentido à leitura.</span></div>
        </section>

        <section className="library-start" aria-labelledby="new-session-heading">
          <div><p className="eyebrow">UM NOVO PONTO DE PARTIDA</p><h2 id="new-session-heading">O que você quer estudar?</h2><p>Crie uma sessão e reúna os PDFs do mesmo assunto.</p></div>
          <form className="session-form" onSubmit={(event) => { event.preventDefault(); if (!busy) onStartSession(theme.trim() || null); }}>
            <label htmlFor="session-theme">Tema da sessão (opcional)</label>
            <div className="session-form-controls"><input id="session-theme" maxLength={200} onChange={(event) => setTheme(event.target.value)} placeholder="Dê um nome à sua próxima leitura" value={theme} /><button className="primary-action" disabled={busy} type="submit">{busy ? "Preparando…" : "Começar sessão"}<span aria-hidden="true">↗</span></button></div>
            <small>Você pode definir ou editar o tema depois.</small>
          </form>
        </section>

        <div className="library-collection-heading"><div><p className="eyebrow">SUA BIBLIOTECA</p><h2>{view === "sessions" ? "Sessões de estudo" : view === "documents" ? "Seus documentos" : "Continue de onde parou"}</h2></div><label className="library-search"><span aria-hidden="true">⌕</span><input aria-label="Buscar na biblioteca" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar na biblioteca…" /></label></div>
        <section className={`library-columns ${view !== "all" ? "library-columns--single" : ""}`} aria-live="polite">
          {view !== "documents" && <div className="library-block"><div className="collection-title"><h3>Sessões abertas</h3><span>{sessions.length}</span></div>
            {filteredSessions.length === 0 ? <div className="library-empty"><span className="empty-symbol" aria-hidden="true">▤</span><h4>{query.trim() ? "Nenhuma sessão encontrada." : "Um espaço para cada assunto."}</h4><p>{query.trim() ? "Tente buscar por outro tema." : "Sua primeira sessão começa com uma curiosidade. Crie uma acima para começar."}</p></div> : <ul className="session-list">{filteredSessions.map((session) => <li key={session.id}><button disabled={busy} onClick={() => onResumeSession(session.id)} type="button"><span className="collection-icon" aria-hidden="true">▤</span><span><strong>{session.theme ?? "Sem tema definido"}</strong><small>{session.theme_origin === "ai_suggestion" ? "tema aceito de uma sugestão" : session.theme ? "tema escrito por você" : "defina o tema quando quiser"}</small></span><span className="collection-arrow" aria-hidden="true">↗</span></button></li>)}</ul>}
          </div>}
          {view !== "sessions" && <div className="library-block"><div className="collection-title"><h3>Documentos já ingeridos</h3><span>{documents.length}</span></div>
            {filteredDocuments.length === 0 ? <div className="library-empty"><span className="empty-symbol" aria-hidden="true">▱</span><h4>{query.trim() ? "Nenhum documento encontrado." : "Suas próximas descobertas."}</h4><p>{query.trim() ? "Tente buscar por outro título ou arquivo." : "Abra uma sessão e traga seus PDFs. Os documentos ficam aqui para novas leituras."}</p></div> : <ul className="document-list">{filteredDocuments.map((document) => <li key={document.id}><span className="collection-icon document-icon" aria-hidden="true">PDF</span><span><strong>{document.title}</strong><small>{document.page_count} página{document.page_count > 1 ? "s" : ""}</small></span></li>)}</ul>}
          </div>}
        </section>
        <footer className="library-footer"><span>Feito para ler com atenção.</span><span>Você lê. Você decide o que fica.</span></footer>
      </div>
    </main>
  );
}
