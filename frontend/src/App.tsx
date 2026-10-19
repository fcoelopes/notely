import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page } from "react-pdf";
import { AnnotationOverlay } from "./components/AnnotationOverlay";
import { AIModelPanel } from "./components/AIModelPanel";
import { AnnotationPanel } from "./components/AnnotationPanel";
import { ClaimPanel } from "./components/ClaimPanel";
import { CommentComposer } from "./components/CommentComposer";
import { ConceptPanel } from "./components/ConceptPanel";
import { DocumentTabs } from "./components/DocumentTabs";
import { LoginScreen } from "./components/LoginScreen";
import { SelectionToolbar } from "./components/SelectionToolbar";
import { SessionLibrary } from "./components/SessionLibrary";
import { SessionThemePanel } from "./components/SessionThemePanel";
import {
  ApiError,
  acceptThemeSuggestion,
  attachDocumentToSession,
  createAnnotation,
  createClaim,
  createConceptRelation,
  createStudySession,
  deleteAIModelConfig,
  deleteClaim,
  deleteClaimRelation,
  deleteDocumentBookmark,
  currentAccount,
  detachDocumentFromSession,
  documentContentUrl,
  getAIModelConfig,
  getStudySession,
  getReadingProgress,
  listAnnotations,
  listClaims,
  listConcepts,
  listDocuments,
  listDocumentBookmarks,
  listStudySessions,
  logout,
  recordViewedPage,
  rejectThemeSuggestion,
  requestThemeSuggestion,
  saveAIModelConfig,
  setStudySessionTheme,
  setDocumentBookmark,
  setUnauthorizedHandler,
  uploadDocument,
} from "./lib/api";
import { normalizeRects } from "./lib/geometry";
import { readPageCount } from "./lib/pdf";
import { useReadingSession } from "./lib/useReadingSession";
import type {
  Account,
  AIModelConfig,
  AIModelConfigInput,
  Annotation,
  AnnotationType,
  ClaimEvidence,
  ClaimEvidenceRelation,
  ConceptOverview,
  DocumentSummary,
  DocumentBookmark,
  ReadingProgressSnapshot,
  SelectionDraft,
  StudySession,
  StudySessionDetail,
} from "./types";

const COMMENT_TYPES: AnnotationType[] = ["note", "question", "disagreement"];

const API_UNAVAILABLE =
  "A API do Notely não está disponível. Confirme se os serviços estão em execução.";

function Reader({ account, onSignOut }: { account: Account; onSignOut: () => void }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [sessions, setSessions] = useState<StudySession[]>([]);
  const [progress, setProgress] = useState<ReadingProgressSnapshot | null>(null);
  const [bookmarksByDocument, setBookmarksByDocument] = useState<Record<string, DocumentBookmark>>({});
  const [bookmarkBusy, setBookmarkBusy] = useState(false);
  const [session, setSession] = useState<StudySessionDetail | null>(null);
  const [claims, setClaims] = useState<ClaimEvidence[]>([]);
  const [concepts, setConcepts] = useState<ConceptOverview[]>([]);
  const [claimsError, setClaimsError] = useState<string | null>(null);
  const [conceptsError, setConceptsError] = useState<string | null>(null);
  const [claimsBusy, setClaimsBusy] = useState(false);
  const [conceptsBusy, setConceptsBusy] = useState(false);
  const [aiConfig, setAIConfig] = useState<AIModelConfig | null>(null);
  const [aiConfigLoading, setAIConfigLoading] = useState(true);
  const [aiConfigBusy, setAIConfigBusy] = useState(false);
  const [aiConfigError, setAIConfigError] = useState<string | null>(null);
  const [aiConfigNotice, setAIConfigNotice] = useState<string | null>(null);
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null);
  const [localUrls, setLocalUrls] = useState<Record<string, string>>({});
  const [annotationsByDocument, setAnnotationsByDocument] = useState<Record<string, Annotation[]>>({});
  const [pageByDocument, setPageByDocument] = useState<Record<string, number>>({});
  const [scale, setScale] = useState(1.1);
  const [stageWidth, setStageWidth] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [selection, setSelection] = useState<SelectionDraft | null>(null);
  const [composing, setComposing] = useState<AnnotationType | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pageRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const localUrlsRef = useRef<Record<string, string>>({});
  const loadedAnnotations = useRef<Set<string>>(new Set());
  const pendingViewedPages = useRef<Set<string>>(new Set());
  const progressRevision = useRef(0);

  const describeError = (caught: unknown): string =>
    caught instanceof ApiError ? caught.message : API_UNAVAILABLE;

  const refreshProgress = useCallback(async () => {
    const revision = progressRevision.current;
    try {
      const snapshot = await getReadingProgress();
      if (revision === progressRevision.current) setProgress(snapshot);
    } catch {
      // Progresso é auxiliar: falhas não interrompem a leitura.
    }
  }, []);

  /**
   * Configuração do modelo de IA: `null` é ausência de configuração (a API responde `404`),
   * não erro. Falhar aqui vira mensagem no painel e não impede ler, navegar ou anotar.
   */
  const refreshAIConfig = useCallback(async () => {
    try {
      setAIConfig(await getAIModelConfig());
      setAIConfigError(null);
    } catch (caught) {
      setAIConfigError(describeError(caught));
    } finally {
      setAIConfigLoading(false);
    }
  }, []);

  const refreshLibrary = useCallback(async () => {
    try {
      const [library, openSessions] = await Promise.all([listDocuments(), listStudySessions()]);
      setDocuments(library);
      setSessions(openSessions);
      void refreshProgress();
      void refreshAIConfig();
    } catch (caught) {
      setError(describeError(caught));
    }
  }, [refreshAIConfig, refreshProgress]);

  useEffect(() => {
    void refreshLibrary();
  }, [refreshLibrary]);

  useEffect(() => {
    const revoke = (urls: Record<string, string>) => {
      Object.values(urls).forEach((url) => URL.revokeObjectURL(url));
    };
    return () => revoke(localUrlsRef.current);
  }, []);

  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (composing) {
        setComposing(null);
        return;
      }
      setSelection(null);
    };
    window.addEventListener("keydown", dismiss);
    return () => window.removeEventListener("keydown", dismiss);
  }, [composing]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    setStageWidth(stage.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setStageWidth(stage.clientWidth));
    observer.observe(stage);
    return () => observer.disconnect();
  }, [session]);

  const activeDocument = useMemo(
    () => session?.documents.find((item) => item.document_id === activeDocumentId) ?? null,
    [activeDocumentId, session],
  );

  const reportReadingError = useCallback((message: string) => setError(message), []);
  const reading = useReadingSession({
    documentId: activeDocumentId,
    filename: activeDocument?.filename ?? null,
    pageNumber: activeDocumentId ? pageByDocument[activeDocumentId] ?? 1 : 1,
    onError: reportReadingError,
  });

  const activeAnnotations = activeDocumentId ? annotationsByDocument[activeDocumentId] ?? [] : [];
  // A página é indexada pelo documento, não pelo vínculo da sessão: `activeDocument.id`
  // é o id do link e não bate com as chaves gravadas pelos handlers de navegação.
  const pageNumber = activeDocumentId ? pageByDocument[activeDocumentId] ?? 1 : 1;
  const numPages = activeDocument?.page_count ?? 0;
  const activeBookmark = activeDocumentId ? bookmarksByDocument[activeDocumentId] : undefined;

  useEffect(() => {
    setSelection(null);
    setComposing(null);
    window.getSelection()?.removeAllRanges();
  }, [activeDocumentId, pageNumber, scale]);

  useEffect(() => {
    if (!stageRef.current) return;
    stageRef.current.scrollTop = 0;
    stageRef.current.scrollLeft = 0;
  }, [activeDocumentId, pageNumber]);

  const currentAnnotations = useMemo(
    () => activeAnnotations.filter((annotation) => annotation.page_number === pageNumber),
    [activeAnnotations, pageNumber],
  );

  const attachableDocuments = useMemo(
    () =>
      documents.filter(
        (document) => !session?.documents.some((item) => item.document_id === document.id),
      ),
    [documents, session],
  );

  const sessionDocumentIds = useMemo(
    () => (session?.documents ?? []).map((document) => document.document_id),
    [session],
  );

  // Afirmações das obras da sessão. A afirmação sem evidência não tem documento para
  // filtrar, e continua listada: o domínio garante que ela segue sendo do usuário.
  const sessionClaims = useMemo(
    () =>
      claims.filter(
        (entry) =>
          entry.document_id === null || sessionDocumentIds.includes(entry.document_id),
      ),
    [claims, sessionDocumentIds],
  );

  const sessionConcepts = useMemo(() => {
    const inSession = new Set(sessionDocumentIds);
    return concepts.filter((concept) =>
      concept.documents.some((document) => inSession.has(document.document_id)),
    );
  }, [concepts, sessionDocumentIds]);

  const loadAnnotations = useCallback(async (documentId: string) => {
    if (loadedAnnotations.current.has(documentId)) return;
    loadedAnnotations.current.add(documentId);
    try {
      const saved = await listAnnotations(documentId);
      setAnnotationsByDocument((current) => ({ ...current, [documentId]: saved }));
    } catch (caught) {
      loadedAnnotations.current.delete(documentId);
      setError(describeError(caught));
    }
  }, []);

  /**
   * Afirmações e conceitos são leitura auxiliar da sessão: falhar aqui vira mensagem no
   * painel, e nunca impede ler, navegar ou anotar.
   */
  const refreshClaims = useCallback(async () => {
    try {
      setClaims(await listClaims());
      setClaimsError(null);
    } catch (caught) {
      setClaimsError(describeError(caught));
    }
  }, []);

  const refreshConcepts = useCallback(async () => {
    try {
      setConcepts(await listConcepts());
      setConceptsError(null);
    } catch (caught) {
      setConceptsError(describeError(caught));
    }
  }, []);

  const handleSaveAIConfig = useCallback(
    async (input: AIModelConfigInput): Promise<boolean> => {
      setAIConfigBusy(true);
      setAIConfigError(null);
      setAIConfigNotice(null);
      try {
        const saved = await saveAIModelConfig(input);
        setAIConfig(saved);
        setAIConfigNotice("Configuração salva. O assistente e a extração passam a usar este modelo.");
        return true;
      } catch (caught) {
        setAIConfigError(describeError(caught));
        return false;
      } finally {
        setAIConfigBusy(false);
      }
    },
    [],
  );

  const handleRemoveAIConfig = useCallback(async (): Promise<boolean> => {
    setAIConfigBusy(true);
    setAIConfigError(null);
    setAIConfigNotice(null);
    try {
      await deleteAIModelConfig();
      // Sem configuração, a tela volta ao estado inicial e a leitura da API responde 404.
      setAIConfig(null);
      setAIConfigNotice("Configuração removida. Nenhum modelo está configurado.");
      return true;
    } catch (caught) {
      setAIConfigError(describeError(caught));
      return false;
    } finally {
      setAIConfigBusy(false);
    }
  }, []);

  const refreshSession = useCallback(async (sessionId: string) => {
    const detail = await getStudySession(sessionId);
    setSession(detail);
    setSessions((current) => [
      detail,
      ...current.filter((item) => item.id !== detail.id),
    ]);
    void refreshProgress();
    return detail;
  }, [refreshProgress]);

  const openSession = useCallback(
    async (sessionId: string) => {
      setBusy(true);
      setError(null);
      try {
        const [detail, bookmarks] = await Promise.all([
          getStudySession(sessionId), listDocumentBookmarks().catch(() => null),
        ]);
        const savedBookmarks = bookmarks
          ? Object.fromEntries(bookmarks.map((bookmark) => [bookmark.document_id, bookmark]))
          : bookmarksByDocument;
        if (bookmarks) setBookmarksByDocument(savedBookmarks);
        setPageByDocument(Object.fromEntries(detail.documents.map((document) => [
          document.document_id, savedBookmarks[document.document_id]?.page_number ?? 1,
        ])));
        setSession(detail);
        void refreshProgress();
        void refreshClaims();
        void refreshConcepts();
        const first = detail.documents[0]?.document_id ?? null;
        setActiveDocumentId(first);
        if (first) await loadAnnotations(first);
      } catch (caught) {
        setError(describeError(caught));
      } finally {
        setBusy(false);
      }
    },
    [bookmarksByDocument, loadAnnotations, refreshClaims, refreshConcepts, refreshProgress],
  );

  const handlePageRendered = useCallback((sessionId: string, documentId: string, viewedPage: number) => {
    const key = `${sessionId}:${documentId}:${viewedPage}`;
    if (pendingViewedPages.current.has(key)) return;
    pendingViewedPages.current.add(key);
    void recordViewedPage(sessionId, documentId, viewedPage).then((update) => {
      progressRevision.current += 1;
      setProgress((current) => {
        const previousSession = current?.sessions[update.study_session_id];
        const previousDocument = current?.documents[update.document_id];
        return {
          documents: {
            ...current?.documents,
            [update.document_id]: previousDocument && previousDocument.viewed_pages > update.document_global.viewed_pages
              ? previousDocument : update.document_global,
          },
          sessions: {
            ...current?.sessions,
            [update.study_session_id]: previousSession && previousSession.progress.viewed_pages > update.session.progress.viewed_pages
              ? previousSession : update.session,
          },
        };
      });
    }).catch(() => {
      pendingViewedPages.current.delete(key);
    });
  }, []);

  const selectDocument = useCallback(
    (documentId: string) => {
      setSelection(null);
      setComposing(null);
      reading.end();
      setActiveDocumentId(documentId);
      void loadAnnotations(documentId);
    },
    [loadAnnotations, reading],
  );

  const handleSetBookmark = useCallback(async () => {
    if (!activeDocumentId) return;
    setBookmarkBusy(true);
    setError(null);
    try {
      const saved = await setDocumentBookmark(activeDocumentId, pageNumber);
      setBookmarksByDocument((current) => ({ ...current, [saved.document_id]: saved }));
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBookmarkBusy(false);
    }
  }, [activeDocumentId, pageNumber]);

  const handleDeleteBookmark = useCallback(async () => {
    if (!activeDocumentId) return;
    const documentId = activeDocumentId;
    setBookmarkBusy(true);
    setError(null);
    try {
      await deleteDocumentBookmark(documentId);
      setBookmarksByDocument((current) => {
        const next = { ...current };
        delete next[documentId];
        return next;
      });
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBookmarkBusy(false);
    }
  }, [activeDocumentId]);

  const storeLocalUrl = useCallback((documentId: string, file: File) => {
    const url = URL.createObjectURL(file);
    localUrlsRef.current = { ...localUrlsRef.current, [documentId]: url };
    setLocalUrls(localUrlsRef.current);
  }, []);

  const handleUpload = useCallback(
    async (files: File[]) => {
      if (!session || files.length === 0) return;
      setBusy(true);
      setError(null);
      try {
        for (const file of files) {
          const pageCount = await readPageCount(file);
          const record = await uploadDocument(file, pageCount);
          await attachDocumentToSession(session.id, record.id);
          setPageByDocument((current) => ({
            ...current, [record.id]: bookmarksByDocument[record.id]?.page_number ?? 1,
          }));
          storeLocalUrl(record.id, file);
        }
        const detail = await refreshSession(session.id);
        setActiveDocumentId((current) => current ?? detail.documents[0]?.document_id ?? null);
      } catch (caught) {
        setError(describeError(caught));
      } finally {
        setBusy(false);
      }
    },
    [bookmarksByDocument, refreshSession, session, storeLocalUrl],
  );

  const handleStartSession = useCallback(
    async (theme: string | null) => {
      setBusy(true);
      setError(null);
      try {
        const created = await createStudySession(theme);
        await openSession(created.id);
      } catch (caught) {
        setError(describeError(caught));
      } finally {
        setBusy(false);
      }
    },
    [openSession],
  );

  const handleAttachDocument = useCallback(
    async (documentId: string) => {
      if (!session) return;
      setBusy(true);
      setError(null);
      try {
        await attachDocumentToSession(session.id, documentId);
        await refreshSession(session.id);
        setPageByDocument((current) => ({
          ...current, [documentId]: bookmarksByDocument[documentId]?.page_number ?? 1,
        }));
        selectDocument(documentId);
      } catch (caught) {
        setError(describeError(caught));
      } finally {
        setBusy(false);
      }
    },
    [bookmarksByDocument, refreshSession, selectDocument, session],
  );

  const handleRemoveDocument = useCallback(
    async (documentId: string) => {
      if (!session) return;
      setBusy(true);
      setError(null);
      reading.end();
      try {
        await detachDocumentFromSession(session.id, documentId);
        const detail = await refreshSession(session.id);
        const remaining = detail.documents.filter((item) => item.document_id !== documentId);
        setActiveDocumentId((current) =>
          current === documentId ? remaining[0]?.document_id ?? null : current,
        );
        const localUrl = localUrlsRef.current[documentId];
        if (localUrl) {
          URL.revokeObjectURL(localUrl);
          const { [documentId]: _removed, ...rest } = localUrlsRef.current;
          localUrlsRef.current = rest;
          setLocalUrls(rest);
        }
        if (selection?.pageNumber && documentId === activeDocumentId) {
          setSelection(null);
          setComposing(null);
        }
      } catch (caught) {
        setError(describeError(caught));
      } finally {
        setBusy(false);
      }
    },
    [activeDocumentId, reading, refreshSession, selection, session],
  );

  const handleCloseSession = useCallback(() => {
    reading.end();
    Object.values(localUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
    localUrlsRef.current = {};
    setLocalUrls({});
    setSession(null);
    setActiveDocumentId(null);
    setSelection(null);
    setComposing(null);
    setClaims([]);
    setConcepts([]);
    setClaimsError(null);
    setConceptsError(null);
    void refreshLibrary();
  }, [reading, refreshLibrary]);

  /**
   * Afirmar a partir de um trecho. O estado local só muda depois do sucesso, como no
   * marcador de página: a lista é recarregada do servidor, e afirmar o mesmo texto de novo
   * reaproveita a afirmação existente em vez de duplicar.
   */
  const handleAssertClaim = useCallback(
    async (
      annotationId: string,
      text: string,
      relation: ClaimEvidenceRelation,
    ): Promise<boolean> => {
      setClaimsBusy(true);
      setClaimsError(null);
      try {
        await createClaim({ annotation_id: annotationId, text, relation });
        await refreshClaims();
        return true;
      } catch (caught) {
        setClaimsError(describeError(caught));
        return false;
      } finally {
        setClaimsBusy(false);
      }
    },
    [refreshClaims],
  );

  const handleRemoveClaim = useCallback(async (claimId: string) => {
    setClaimsBusy(true);
    setClaimsError(null);
    try {
      // A remoção é suave no domínio e não toca a anotação que servia de evidência.
      await deleteClaim(claimId);
      setClaims((current) => current.filter((entry) => entry.claim.id !== claimId));
    } catch (caught) {
      setClaimsError(describeError(caught));
    } finally {
      setClaimsBusy(false);
    }
  }, []);

  const handleRelateConcepts = useCallback(
    async (sourceConceptId: string, targetConceptId: string) => {
      setConceptsBusy(true);
      setConceptsError(null);
      try {
        await createConceptRelation(sourceConceptId, targetConceptId);
        await refreshConcepts();
      } catch (caught) {
        setConceptsError(describeError(caught));
      } finally {
        setConceptsBusy(false);
      }
    },
    [refreshConcepts],
  );

  const handleRemoveConceptRelation = useCallback(
    async (relationId: string) => {
      setConceptsBusy(true);
      setConceptsError(null);
      try {
        await deleteClaimRelation(relationId);
        await refreshConcepts();
      } catch (caught) {
        setConceptsError(describeError(caught));
      } finally {
        setConceptsBusy(false);
      }
    },
    [refreshConcepts],
  );

  const handleEditTheme = useCallback(
    async (theme: string) => {
      if (!session) return;
      setSaving(true);
      setError(null);
      try {
        await setStudySessionTheme(session.id, theme);
        await refreshSession(session.id);
      } catch (caught) {
        setError(describeError(caught));
      } finally {
        setSaving(false);
      }
    },
    [refreshSession, session],
  );

  const handleRequestSuggestion = useCallback(async () => {
    if (!session) return;
    setSuggesting(true);
    setError(null);
    try {
      await requestThemeSuggestion(session.id);
      await refreshSession(session.id);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setSuggesting(false);
    }
  }, [refreshSession, session]);

  const handleAcceptSuggestion = useCallback(
    async (suggestionId: string) => {
      if (!session) return;
      setSaving(true);
      setError(null);
      try {
        await acceptThemeSuggestion(session.id, suggestionId);
        await refreshSession(session.id);
      } catch (caught) {
        setError(describeError(caught));
      } finally {
        setSaving(false);
      }
    },
    [refreshSession, session],
  );

  const handleRejectSuggestion = useCallback(
    async (suggestionId: string) => {
      if (!session) return;
      setSaving(true);
      setError(null);
      try {
        await rejectThemeSuggestion(session.id, suggestionId);
        await refreshSession(session.id);
      } catch (caught) {
        setError(describeError(caught));
      } finally {
        setSaving(false);
      }
    },
    [refreshSession, session],
  );

  const captureSelection = useCallback(() => {
    const page = pageRef.current;
    const browserSelection = window.getSelection();
    if (!page || !browserSelection || browserSelection.isCollapsed || browserSelection.rangeCount === 0) return;

    const range = browserSelection.getRangeAt(0);
    if (!page.contains(range.commonAncestorContainer)) return;
    const pageBounds = page.getBoundingClientRect();
    const rects = normalizeRects(Array.from(range.getClientRects()), pageBounds);
    const quote = browserSelection.toString().trim();
    if (!quote || rects.length === 0) return;

    const bounds = range.getBoundingClientRect();
    reading.noteActivity(pageNumber);
    setComposing(null);
    setSelection({
      pageNumber,
      quote,
      rects,
      toolbarX: Math.min(
        Math.max(bounds.left - pageBounds.left + bounds.width / 2, 160),
        pageBounds.width - 160,
      ),
      toolbarY: Math.max(bounds.top - pageBounds.top - 58, 8),
    });
  }, [pageNumber, reading]);

  const saveSelection = useCallback(
    async (type: AnnotationType, comment: string | null) => {
      if (!selection || !activeDocumentId) return;

      setSaving(true);
      setError(null);
      try {
        const saved = await createAnnotation({
          document_id: activeDocumentId,
          page_number: selection.pageNumber,
          type,
          quote: selection.quote,
          comment,
          reading_session_id: reading.sessionId,
          study_session_id: session?.id ?? null,
          position: {
            version: 1,
            rects: selection.rects,
            textQuoteSelector: { exact: selection.quote },
          },
        });
        setAnnotationsByDocument((current) => ({
          ...current,
          [activeDocumentId]: [...(current[activeDocumentId] ?? []), saved],
        }));
        setSelection(null);
        setComposing(null);
        reading.noteActivity(selection.pageNumber);
        window.getSelection()?.removeAllRanges();
      } catch {
        setError("Não foi possível salvar a anotação. Sua seleção foi mantida para tentar novamente.");
      } finally {
        setSaving(false);
      }
    },
    [activeDocumentId, reading, selection, session?.id],
  );

  const chooseAnnotationType = useCallback(
    (type: AnnotationType) => {
      if (!selection || !activeDocumentId) return;
      if (COMMENT_TYPES.includes(type)) {
        setComposing(type);
        return;
      }
      void saveSelection(type, null);
    },
    [activeDocumentId, saveSelection, selection],
  );

  if (!session) {
    return (
      <div className="app-shell">
        <Header account={account} onSignOut={onSignOut} />
        {error && <StatusBanner message={error} />}
        <SessionLibrary
          busy={busy}
          documents={documents}
          onResumeSession={(sessionId) => void openSession(sessionId)}
          onStartSession={(theme) => void handleStartSession(theme)}
          sessions={sessions}
          progress={progress}
        />
      </div>
    );
  }

  const documentUrl = activeDocumentId
    ? localUrls[activeDocumentId] ?? documentContentUrl(activeDocumentId)
    : null;

  return (
    <div className="app-shell app-shell--reading">
      <Header onClose={handleCloseSession} account={account} onSignOut={onSignOut}>
        <div className="reader-header-content">
          <div className="session-heading">
            <span className="session-tag">Sessão de estudo</span>
            <strong>{session.theme ?? "Sem tema definido"}</strong>
            <small className="reading-progress">{progress ? `${progress.sessions[session.id]?.progress.percent ?? 0}% · ${progress.sessions[session.id]?.progress.viewed_pages ?? 0}/${progress.sessions[session.id]?.progress.total_pages ?? 0} páginas visualizadas` : "Progresso indisponível"}</small>
          </div>

          <div className="session-actions">
            <label className={`file-button file-button--compact ${busy ? "is-busy" : ""}`}>
              <input
                aria-label="Abrir PDFs"
                accept="application/pdf,.pdf"
                disabled={busy}
                multiple
                onChange={(event) => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = "";
                  void handleUpload(files);
                }}
                type="file"
              />
              <span>{busy ? "Preparando…" : "Abrir PDFs"}</span>
            </label>

            {attachableDocuments.length > 0 && (
              <form
                className="attach-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  const select = event.currentTarget.elements.namedItem("library-document");
                  if (select instanceof HTMLSelectElement && select.value) {
                    void handleAttachDocument(select.value);
                  }
                }}
              >
                <select aria-label="Documentos já ingeridos" name="library-document" defaultValue="">
                  <option disabled value="">
                    Documentos já ingeridos…
                  </option>
                  {attachableDocuments.map((document) => (
                    <option key={document.id} value={document.id}>
                      {document.title}
                    </option>
                  ))}
                </select>
                <button disabled={busy} type="submit">
                  Adicionar
                </button>
              </form>
            )}
          </div>
        </div>
      </Header>
      {error && <StatusBanner message={error} />}

      <div className="session-bar">
        <DocumentTabs
          activeDocumentId={activeDocumentId}
          documents={session.documents}
          progress={progress ? progress.sessions[session.id] ?? { progress: { viewed_pages: 0, total_pages: 0, percent: 0 }, documents: {} } : null}
          onClose={(documentId) => void handleRemoveDocument(documentId)}
          onSelect={selectDocument}
        />
      </div>

      <div className={`reader-layout ${sidebarOpen ? "" : "reader-layout--focused"}`}>
        <section className="reader-column">
          <div className="reader-toolbar">
            <div className="page-controls" aria-label="Navegação do documento">
              <button
                aria-label="Página anterior"
                disabled={!activeDocument || pageNumber <= 1}
                onClick={() => {
                  if (!activeDocumentId) return;
                  const next = Math.max(pageNumber - 1, 1);
                  setPageByDocument((current) => ({ ...current, [activeDocumentId]: next }));
                  reading.noteActivity(next);
                }}
              >
                ←
              </button>
              <form className="page-jump" onSubmit={(event) => {
                event.preventDefault();
                const input = event.currentTarget.elements.namedItem("page");
                if (!activeDocumentId || !(input instanceof HTMLInputElement)) return;
                const page = Number(input.value);
                if (!Number.isInteger(page) || page < 1 || page > numPages) return;
                setPageByDocument((current) => ({ ...current, [activeDocumentId]: page }));
                reading.noteActivity(page);
              }}>
                <input key={`${activeDocumentId}-${pageNumber}`} aria-label="Ir para página" name="page" type="number" min={1} max={numPages || 1} defaultValue={pageNumber} disabled={!activeDocument} />
              </form>
              <span className="page-indicator">/ {numPages || "–"}</span>
              <button
                aria-label="Próxima página"
                disabled={!activeDocument || pageNumber >= numPages}
                onClick={() => {
                  if (!activeDocumentId) return;
                  const next = Math.min(pageNumber + 1, activeDocument?.page_count ?? 1);
                  setPageByDocument((current) => ({ ...current, [activeDocumentId]: next }));
                  reading.noteActivity(next);
                }}
              >
                →
              </button>
            </div>
            <div className="bookmark-controls" aria-label="Marcador de página">
              <button
                aria-label={activeBookmark ? "Mover marcador para a página atual" : "Marcar página atual"}
                disabled={!activeDocument || bookmarkBusy || activeBookmark?.page_number === pageNumber}
                onClick={() => void handleSetBookmark()}
                title="Marcar a página atual para retomar a leitura"
                type="button"
              >
                Marcar
              </button>
              {activeBookmark && (
                <>
                  <button
                    aria-label={`Ir para a página marcada ${activeBookmark.page_number}`}
                    className="bookmark-go"
                    disabled={bookmarkBusy || activeBookmark.page_number === pageNumber}
                    onClick={() => {
                      if (!activeDocumentId) return;
                      setPageByDocument((current) => ({ ...current, [activeDocumentId]: activeBookmark.page_number }));
                      reading.noteActivity(activeBookmark.page_number);
                    }}
                    title={`Ir para a página marcada ${activeBookmark.page_number}`}
                    type="button"
                  >
                    Marcador · pág. {activeBookmark.page_number}
                  </button>
                  <button
                    aria-label="Remover marcador"
                    className="bookmark-remove"
                    disabled={bookmarkBusy}
                    onClick={() => void handleDeleteBookmark()}
                    title="Remover marcador"
                    type="button"
                  >×</button>
                </>
              )}
            </div>
              <div className="zoom-controls" aria-label="Zoom">
              <button aria-label="Diminuir zoom" disabled={scale <= 0.5} onClick={() => setScale((value) => Math.max(0.5, Number((value - 0.1).toFixed(1))))}>−</button>
              <span>{Math.round(scale * 100)}%</span>
              <button aria-label="Aumentar zoom" disabled={scale >= 3} onClick={() => setScale((value) => Math.min(3, Number((value + 0.1).toFixed(1))))}>+</button>
            </div>
            <button className="sidebar-toggle" aria-controls="reader-notes" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen((open) => !open)}>
              Anotações <span>{activeAnnotations.length}</span>
            </button>
          </div>

          <div className="document-stage" ref={stageRef} role="region" aria-label="Página do PDF" tabIndex={0} onMouseUp={captureSelection}>
            {!documentUrl ? (
              <div className="document-placeholder">
                <p>Traga os PDFs deste tema.</p>
                <small>
                  Use “Abrir PDFs” para enviar arquivos novos ou adicione um documento já ingerido.
                </small>
              </div>
            ) : (
              /* react-pdf 11 carrega via Suspense por padrão; o Reader não tem boundary,
                 então o documento opta por loading/error. */
              <Document
                file={documentUrl}
                key={activeDocumentId}
                loading={<div className="document-loading">Preparando as páginas…</div>}
                error={<div className="document-loading" role="alert">Não foi possível abrir este PDF. Tente reabrir o documento.</div>}
                onSourceError={() => setError("Não foi possível carregar o arquivo PDF.")}
                onLoadError={() => { setBusy(false); setError("O PDF não pôde ser interpretado."); }}
                suspense={false}
              >
                <div className="page-shell" ref={pageRef}>
                  <Page
                    pageNumber={pageNumber}
                    renderAnnotationLayer
                    renderTextLayer
                    scale={scale}
                    width={stageWidth > 0 && stageWidth < 700 ? Math.max(stageWidth - 48, 240) / 1.1 : undefined}
                    suspense={false}
                    loading={<div className="document-loading">Carregando página…</div>}
                    error={<div className="document-loading" role="alert">Não foi possível exibir esta página.</div>}
                    onLoadError={() => setError("Não foi possível carregar esta página do PDF.")}
                    onRenderError={() => setError("Não foi possível renderizar esta página do PDF.")}
                    onRenderSuccess={() => { if (activeDocumentId) handlePageRendered(session.id, activeDocumentId, pageNumber); }}
                  />
                  <AnnotationOverlay annotations={currentAnnotations} />
                  {selection && composing && (
                    <CommentComposer
                      draft={selection}
                      onCancel={() => setComposing(null)}
                      onSave={(comment) => { void saveSelection(composing, comment); }}
                      saving={saving}
                      type={composing}
                    />
                  )}
                  {selection && !composing && (
                    <SelectionToolbar draft={selection} onChoose={chooseAnnotationType} saving={saving} />
                  )}
                </div>
              </Document>
            )}
          </div>
        </section>

        <aside className="reader-sidebar" id="reader-notes" hidden={!sidebarOpen}>
          <details className="session-settings">
            <summary>Tema e sugestões da sessão</summary>
          <SessionThemePanel
            onAcceptSuggestion={(suggestionId) => void handleAcceptSuggestion(suggestionId)}
            onEditTheme={(theme) => void handleEditTheme(theme)}
            onRejectSuggestion={(suggestionId) => void handleRejectSuggestion(suggestionId)}
            onRequestSuggestion={() => void handleRequestSuggestion()}
            saving={saving}
            session={session}
            suggesting={suggesting}
          />
          </details>
          <details className="session-settings">
            <summary>Modelo de IA</summary>
            <AIModelPanel
              busy={aiConfigBusy}
              config={aiConfig}
              error={aiConfigError}
              loading={aiConfigLoading}
              notice={aiConfigNotice}
              onReload={() => void refreshAIConfig()}
              onRemove={handleRemoveAIConfig}
              onSave={handleSaveAIConfig}
            />
          </details>
          <AnnotationPanel
            annotations={activeAnnotations}
            sessionId={session.id}
            availableDocumentIds={session.documents.map((item) => item.document_id)}
            onGoToSource={(documentId, page) => {
              setActiveDocumentId(documentId);
              setPageByDocument((current) => ({ ...current, [documentId]: page }));
              setSidebarOpen(false);
            }}
            onGoToPage={(page) => {
              if (!activeDocumentId) return;
              setPageByDocument((current) => ({ ...current, [activeDocumentId]: page }));
              reading.noteActivity(page);
            }}
            onAssert={handleAssertClaim}
            pageNumber={pageNumber}
          />
          <details className="session-settings">
            <summary>Afirmações</summary>
            <ClaimPanel
              claims={sessionClaims}
              busy={claimsBusy}
              error={claimsError}
              onReload={() => void refreshClaims()}
              onRemove={(claimId) => void handleRemoveClaim(claimId)}
            />
          </details>
          <details className="session-settings">
            <summary>Conceitos e associações</summary>
            <ConceptPanel
              concepts={sessionConcepts}
              documentIds={sessionDocumentIds}
              busy={conceptsBusy}
              error={conceptsError}
              onAssociate={(sourceConceptId, targetConceptId) =>
                void handleRelateConcepts(sourceConceptId, targetConceptId)
              }
              onReload={() => void refreshConcepts()}
              onRemoveAssociation={(relationId) =>
                void handleRemoveConceptRelation(relationId)
              }
            />
          </details>
        </aside>
      </div>
    </div>
  );
}

function Header({
  onClose,
  children,
  account,
  onSignOut,
}: {
  onClose?: () => void;
  account: Account;
  onSignOut: () => void;
  children?: React.ReactNode;
}) {
  return (
    <header className="site-header">
      <a className="brand" href="/" onClick={(event) => { if (onClose) { event.preventDefault(); onClose(); } }}>
        <span className="brand-mark">N</span>
        <span>notely</span>
      </a>
      {children ?? <p>Leia. Anote. Conecte.</p>}
      <div className="header-actions">
        <span className="account-email" title={account.email}>{account.email}</span>
        <button className="sign-out" onClick={onSignOut}>Sair</button>
        {onClose && <button className="close-reader" onClick={onClose}>Fechar sessão</button>}
      </div>
    </header>
  );
}

/**
 * Porta da aplicação: sem sessão ativa o Reader não é montado. Uma resposta 401 de
 * qualquer chamada derruba a sessão local, porque o cookie é a única fonte de verdade.
 */
function App() {
  const [account, setAccount] = useState<Account | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    setUnauthorizedHandler(() => setAccount(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    let active = true;
    currentAccount()
      .then((found) => {
        if (active) setAccount(found);
      })
      .catch(() => {
        if (active) setAccount(null);
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const signOut = useCallback(() => {
    // A sessão local termina de imediato; revogar no servidor é melhor esforço e não
    // pode impedir o usuário de sair.
    setAccount(null);
    void logout().catch(() => undefined);
  }, []);

  if (checking) {
    return (
      <main className="login">
        <p className="login-hint">Carregando…</p>
      </main>
    );
  }

  if (account === null) {
    return <LoginScreen onAuthenticated={setAccount} />;
  }

  return <Reader account={account} onSignOut={signOut} />;
}

function StatusBanner({ message }: { message: string }) {
  return <div className="status-banner" role="alert">{message}</div>;
}

export { App };
