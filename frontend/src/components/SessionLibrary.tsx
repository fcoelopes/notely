import { useMemo, useRef, useState } from "react";
import type { DocumentSummary, PageProgress, ReadingProgressSnapshot, StudySession } from "../types";

interface Props {
  documents: DocumentSummary[];
  sessions: StudySession[];
  busy: boolean;
  progress: ReadingProgressSnapshot | null;
  onResumeSession: (sessionId: string) => void;
  onStartSession: (theme: string | null) => void;
}

type View = "all" | "sessions" | "documents";
type SortOrder = "recent" | "title";

const OVERVIEW_LIMIT = 3;
const PAGE_SIZE = 8;

function normalize(value: string): string {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
}

function timestamp(value: string | undefined): number {
  const parsed = Date.parse(value ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function shortDate(value: string | undefined): string | null {
  if (!timestamp(value)) return null;
  return new Intl.DateTimeFormat("pt-BR", { day: "numeric", month: "short", year: "numeric" }).format(new Date(value ?? ""));
}

function progressText(value: PageProgress | undefined, totalPages: number, available: boolean): string {
  if (!available) return "Progresso indisponível";
  return `${value?.percent ?? 0}% · ${value?.viewed_pages ?? 0}/${value?.total_pages ?? totalPages} páginas visualizadas`;
}

function ProgressTrack({ percent }: { percent: number | null }) {
  return (
    <span className="library-progress-track" aria-hidden="true">
      <span style={{ width: `${percent ?? 0}%` }} />
    </span>
  );
}

function Pagination({ page, pages, label, onChange }: {
  page: number;
  pages: number;
  label: string;
  onChange: (page: number) => void;
}) {
  if (pages <= 1) return null;
  return (
    <nav aria-label={`Páginas de ${label}`} className="library-pagination">
      <button aria-label={`Página anterior de ${label}`} disabled={page === 1} onClick={() => onChange(page - 1)} type="button">Anterior</button>
      <span>Página {page} de {pages}</span>
      <button aria-label={`Próxima página de ${label}`} disabled={page === pages} onClick={() => onChange(page + 1)} type="button">Próxima</button>
    </nav>
  );
}

export function SessionLibrary({ documents, sessions, busy, progress, onResumeSession, onStartSession }: Props) {
  const [theme, setTheme] = useState("");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<View>("all");
  const [sortOrder, setSortOrder] = useState<SortOrder>("recent");
  const [sessionPage, setSessionPage] = useState(1);
  const [documentPage, setDocumentPage] = useState(1);
  const sessionBlockRef = useRef<HTMLDivElement>(null);
  const documentBlockRef = useRef<HTMLDivElement>(null);

  const documentById = useMemo(() => new Map(documents.map((document) => [document.id, document])), [documents]);
  const sessionDocumentTitles = (sessionId: string): string[] =>
    Object.keys(progress?.sessions[sessionId]?.documents ?? {})
      .map((documentId) => documentById.get(documentId)?.title)
      .filter((title): title is string => Boolean(title))
      .sort((first, second) => first.localeCompare(second, "pt-BR"));
  const sessionSummary = (sessionId: string): string => {
    if (!progress) return "Documentos da sessão indisponíveis";
    const titles = sessionDocumentTitles(sessionId);
    if (titles.length === 0) return "Nenhum PDF nesta sessão";
    const names = titles.slice(0, 2).join(" · ");
    return `${titles.length} PDF${titles.length === 1 ? "" : "s"} · ${names}${titles.length > 2 ? ` +${titles.length - 2}` : ""}`;
  };

  const search = normalize(query.trim());
  const filteredSessions = sessions
    .filter((session) => normalize(`${session.theme ?? "Sem tema definido"} ${sessionDocumentTitles(session.id).join(" ")}`).includes(search))
    .sort((first, second) => sortOrder === "title"
      ? (first.theme ?? "Sem tema definido").localeCompare(second.theme ?? "Sem tema definido", "pt-BR")
      : timestamp(second.updated_at) - timestamp(first.updated_at));
  const filteredDocuments = documents
    .filter((document) => normalize(`${document.title} ${document.filename}`).includes(search))
    .sort((first, second) => sortOrder === "title"
      ? first.title.localeCompare(second.title, "pt-BR")
      : timestamp(second.created_at) - timestamp(first.created_at));
  const sessionPages = Math.max(1, Math.ceil(filteredSessions.length / PAGE_SIZE));
  const documentPages = Math.max(1, Math.ceil(filteredDocuments.length / PAGE_SIZE));
  const currentSessionPage = Math.min(sessionPage, sessionPages);
  const currentDocumentPage = Math.min(documentPage, documentPages);
  const visibleSessions = view === "all"
    ? filteredSessions.slice(0, OVERVIEW_LIMIT)
    : filteredSessions.slice((currentSessionPage - 1) * PAGE_SIZE, currentSessionPage * PAGE_SIZE);
  const visibleDocuments = view === "all"
    ? filteredDocuments.slice(0, OVERVIEW_LIMIT)
    : filteredDocuments.slice((currentDocumentPage - 1) * PAGE_SIZE, currentDocumentPage * PAGE_SIZE);

  const changeView = (next: View) => {
    setView(next);
    setSessionPage(1);
    setDocumentPage(1);
  };
  const changePage = (next: number, collection: View) => {
    if (collection === "sessions") {
      setSessionPage(next);
      sessionBlockRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    } else {
      setDocumentPage(next);
      documentBlockRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    }
  };

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
            <button key={id} type="button" aria-label={count === null ? label : `${label} ${count}`} aria-current={view === id ? "page" : undefined} onClick={() => changeView(id)}>
              <span>{label}</span>{count !== null && <small>{count}</small>}
            </button>
          ))}
        </nav>
        <div className="nav-note"><span className="nav-note-mark" aria-hidden="true">N</span><p>O conhecimento começa<br />com a sua leitura.</p><small>Leia. Anote. Conecte.</small></div>
      </aside>

      <div className="library-content">
        <div className="library-topline"><span>Seu espaço de leitura</span><span className="workspace-label">Biblioteca pessoal</span></div>
        {view === "all" && (
          <section className="library-welcome">
            <div><p className="eyebrow">UM TRECHO. UMA IDEIA. UMA CONEXÃO.</p><h1>Guarde o que<br /><em>importa para você.</em></h1><p>Reúna seus documentos, acompanhe suas leituras e dê um lugar às ideias que merecem ficar.</p></div>
            <div className="library-illustration" aria-hidden="true"><div className="illustration-sheet"><span>NOTAS DE LEITURA</span><i /><i /><i /><mark>Uma ideia para guardar.</mark><i /><i /><div className="illustration-note">voltar a este trecho</div></div><span className="illustration-caption">Seu olhar dá sentido à leitura.</span></div>
          </section>
        )}

        {view !== "documents" && (
          <section className="library-start" aria-labelledby="new-session-heading">
            <div><p className="eyebrow">UM NOVO PONTO DE PARTIDA</p><h2 id="new-session-heading">O que você quer estudar?</h2><p>Crie uma sessão e reúna os PDFs do mesmo assunto.</p></div>
            <form className="session-form" onSubmit={(event) => { event.preventDefault(); if (!busy) onStartSession(theme.trim() || null); }}>
              <label htmlFor="session-theme">Tema da sessão (opcional)</label>
              <div className="session-form-controls"><input id="session-theme" maxLength={200} onChange={(event) => setTheme(event.target.value)} placeholder="Dê um nome à sua próxima leitura" value={theme} /><button className="primary-action" disabled={busy} type="submit">{busy ? "Preparando…" : "Começar sessão"}<span aria-hidden="true">↗</span></button></div>
              <small>Você pode definir ou editar o tema depois.</small>
            </form>
          </section>
        )}

        <div className="library-collection-heading">
          <div>
            <p className="eyebrow">SUA BIBLIOTECA</p>
            <h2>{view === "sessions" ? "Sessões de estudo" : view === "documents" ? "Seus documentos" : "Continue de onde parou"}</h2>
            {view === "all" && <p className="library-heading-note">Uma seleção recente. Abra uma coleção para explorar tudo.</p>}
          </div>
          <div className="library-controls">
            <label className="library-search"><span aria-hidden="true">⌕</span><input aria-label="Buscar na biblioteca" type="search" value={query} onChange={(event) => { setQuery(event.target.value); setSessionPage(1); setDocumentPage(1); }} placeholder="Buscar na biblioteca…" /></label>
            <select aria-label="Ordenar biblioteca" value={sortOrder} onChange={(event) => { setSortOrder(event.target.value as SortOrder); setSessionPage(1); setDocumentPage(1); }}>
              <option value="recent">Mais recentes</option>
              <option value="title">Título A–Z</option>
            </select>
          </div>
        </div>

        <section className={`library-columns ${view !== "all" ? "library-columns--single" : ""}`} aria-live="polite">
          {view !== "documents" && (
            <div className="library-block" ref={sessionBlockRef}>
              <div className="collection-title">
                <h3>Sessões abertas</h3><span>{filteredSessions.length}</span>
                {view === "all" && filteredSessions.length > OVERVIEW_LIMIT && <button className="collection-view-all" onClick={() => changeView("sessions")} type="button">Ver todas as sessões <span aria-hidden="true">→</span></button>}
              </div>
              {filteredSessions.length === 0 ? (
                <div className="library-empty"><span className="empty-symbol" aria-hidden="true">▤</span><h4>{search ? "Nenhuma sessão encontrada." : "Um espaço para cada assunto."}</h4><p>{search ? "Tente buscar por outro tema ou nome de PDF." : "Sua primeira sessão começa com uma curiosidade. Crie uma acima para começar."}</p></div>
              ) : (
                <ul className="session-list">{visibleSessions.map((session) => {
                  const value = progress?.sessions[session.id]?.progress;
                  return <li key={session.id}>
                    <button disabled={busy} onClick={() => onResumeSession(session.id)} type="button">
                      <span className="collection-icon" aria-hidden="true">▤</span>
                      <span className="collection-card-body">
                        <span className="collection-card-top"><strong>{session.theme ?? "Sem tema definido"}</strong><small>{shortDate(session.updated_at)}</small></span>
                        <small className="collection-origin">{session.theme_origin === "ai_suggestion" ? "tema aceito de uma sugestão" : session.theme ? "tema escrito por você" : "defina o tema quando quiser"}</small>
                        <small className="collection-description" title={sessionSummary(session.id)}>{sessionSummary(session.id)}</small>
                        <small className="reading-progress">{progressText(value, 0, progress !== null)}</small>
                        <ProgressTrack percent={progress ? value?.percent ?? 0 : null} />
                      </span>
                      <span className="collection-arrow" aria-hidden="true">↗</span>
                    </button>
                  </li>;
                })}</ul>
              )}
              {view === "sessions" && <Pagination page={currentSessionPage} pages={sessionPages} label="sessões" onChange={(next) => changePage(next, "sessions")} />}
            </div>
          )}
          {view !== "sessions" && (
            <div className="library-block" ref={documentBlockRef}>
              <div className="collection-title">
                <h3>Documentos já ingeridos</h3><span>{filteredDocuments.length}</span>
                {view === "all" && filteredDocuments.length > OVERVIEW_LIMIT && <button className="collection-view-all" onClick={() => changeView("documents")} type="button">Ver todos os documentos <span aria-hidden="true">→</span></button>}
              </div>
              {filteredDocuments.length === 0 ? (
                <div className="library-empty"><span className="empty-symbol" aria-hidden="true">▱</span><h4>{search ? "Nenhum documento encontrado." : "Suas próximas descobertas."}</h4><p>{search ? "Tente buscar por outro título ou arquivo." : "Abra uma sessão e traga seus PDFs. Os documentos ficam aqui para novas leituras."}</p></div>
              ) : (
                <ul className="document-list">{visibleDocuments.map((document) => {
                  const value = progress?.documents[document.id];
                  return <li key={document.id}>
                    <span className="collection-icon document-icon" aria-hidden="true">PDF</span>
                    <span className="collection-card-body">
                      <span className="collection-card-top"><strong>{document.title}</strong><small>{shortDate(document.created_at)}</small></span>
                      <small className="collection-description">{document.page_count} página{document.page_count === 1 ? "" : "s"} · {document.filename}</small>
                      <small className="reading-progress">{progressText(value, document.page_count, progress !== null)}</small>
                      <ProgressTrack percent={progress ? value?.percent ?? 0 : null} />
                    </span>
                  </li>;
                })}</ul>
              )}
              {view === "documents" && <Pagination page={currentDocumentPage} pages={documentPages} label="documentos" onChange={(next) => changePage(next, "documents")} />}
            </div>
          )}
        </section>
        <footer className="library-footer"><span>Feito para ler com atenção.</span><span>Você lê. Você decide o que fica.</span></footer>
      </div>
    </main>
  );
}
