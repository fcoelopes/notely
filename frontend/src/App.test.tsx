import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError } from "./lib/api";
import type {
  AIModelConfig,
  AIModelConfigInput,
  Annotation,
  ClaimEvidence,
  ConceptOverview,
  DocumentSummary,
  SessionDocument,
  StudySessionDetail,
  ThemeSuggestion,
} from "./types";

interface DocumentProps {
  file: string;
  loading: unknown;
  onLoadError: () => void;
  onSourceError: () => void;
  suspense?: boolean;
  children: React.ReactNode;
}

interface CreateAnnotationInput {
  document_id: string;
  page_number: number;
  type: Annotation["type"];
  quote: string;
  comment: string | null;
  reading_session_id: string | null;
  study_session_id: string | null;
}

const pdfDocument = vi.hoisted(() => ({ props: null as DocumentProps | null, pageRendered: null as (() => void) | null }));
const testAccount = { id: "user-1", email: "dono@exemplo.com", totp_enabled: false };
const api = vi.hoisted(() => ({
  acceptThemeSuggestion: vi.fn(),
  currentAccount: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
  submitMfaCode: vi.fn(),
  submitRecoveryCode: vi.fn(),
  startReadingSession: vi.fn(),
  updateReadingSession: vi.fn(),
  attachDocumentToSession: vi.fn(),
  createAnnotation: vi.fn(),
  createStudySession: vi.fn(),
  detachDocumentFromSession: vi.fn(),
  deleteDocumentBookmark: vi.fn(),
  getStudySession: vi.fn(),
  getReadingProgress: vi.fn(),
  recordViewedPage: vi.fn(),
  getQuestionSources: vi.fn(),
  retryQuestionSources: vi.fn(),
  listAnnotations: vi.fn(),
  listDocuments: vi.fn(),
  listDocumentBookmarks: vi.fn(),
  listStudySessions: vi.fn(),
  rejectThemeSuggestion: vi.fn(),
  requestThemeSuggestion: vi.fn(),
  setStudySessionTheme: vi.fn(),
  setDocumentBookmark: vi.fn(),
  uploadDocument: vi.fn(),
  listClaims: vi.fn(),
  createClaim: vi.fn(),
  deleteClaim: vi.fn(),
  listConcepts: vi.fn(),
  createConceptRelation: vi.fn(),
  deleteClaimRelation: vi.fn(),
  getAIModelConfig: vi.fn(),
  saveAIModelConfig: vi.fn(),
  deleteAIModelConfig: vi.fn(),
}));
const readPageCount = vi.hoisted(() => vi.fn());

vi.mock("react-pdf", async () => {
  const { useEffect } = await import("react");

  return {
    pdfjs: { GlobalWorkerOptions: { workerSrc: "" }, getDocument: vi.fn() },
    Document: (props: DocumentProps) => {
      pdfDocument.props = props;
      // react-pdf 11 carrega via Suspense por padrão e o Reader não tem boundary.
      if (props.suspense !== false) {
        throw new Error("react-pdf requires a Suspense boundary when suspense is enabled");
      }
      useEffect(() => {
        return undefined;
      }, [props.file]);
      return <div className="react-pdf__Document">{props.children}</div>;
    },
    Page: (props: { pageNumber: number; onRenderSuccess?: () => void }) => {
      pdfDocument.pageRendered = props.onRenderSuccess ?? null;
      return (
      <div className="react-pdf__Page" data-page-number={props.pageNumber}>
        <span>Trecho selecionável do documento</span>
      </div>
    );
    },
  };
});

vi.mock("./lib/pdf", () => ({ readPageCount }));

vi.mock("./lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(readonly status: number, message: string) {
      super(message);
    }
  },
  ...api,
  documentContentUrl: (documentId: string) => `/api/documents/${documentId}/content`,
}));

function documentSummary(id: string, title: string, pageCount = 4): DocumentSummary {
  return {
    id,
    sha256: "a".repeat(64),
    title,
    filename: `${title}.pdf`,
    page_count: pageCount,
    storage_uri: `documents/${id}.pdf`,
    mime_type: "application/pdf",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function sessionDocument(documentId: string, title: string, position: number, pageCount = 4): SessionDocument {
  return {
    id: `link-${documentId}`,
    study_session_id: "session-1",
    document_id: documentId,
    position,
    added_at: "2026-01-01T00:00:00Z",
    title,
    filename: `${title}.pdf`,
    page_count: pageCount,
  };
}

function sessionDetail(overrides: Partial<StudySessionDetail> = {}): StudySessionDetail {
  return {
    id: "session-1",
    theme: null,
    theme_origin: null,
    theme_updated_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    documents: [],
    suggestions: [],
    ...overrides,
  };
}

function suggestion(overrides: Partial<ThemeSuggestion> = {}): ThemeSuggestion {
  return {
    id: "suggestion-1",
    suggestion_type: "study_session_theme",
    subject_type: "study_session",
    subject_id: "session-1",
    status: "pending",
    payload: { theme: "embeddings · recuperação", rationale: "Vem dos títulos da sessão." },
    provider: "heuristic",
    model: "frequency-v1",
    created_at: "2026-01-01T00:00:00Z",
    accepted_at: null,
    rejected_at: null,
    ...overrides,
  };
}

const pageBounds = { left: 0, top: 0, right: 400, bottom: 800, width: 400, height: 800 };

function annotation(documentId: string, overrides: Partial<Annotation> = {}): Annotation {
  const record: Annotation = {
    id: `annotation-${savedAnnotations.length + 1}`,
    document_id: documentId,
    page_number: 1,
    type: "highlight",
    quote: "Trecho",
    comment: null,
    position: { version: 1, rects: [], textQuoteSelector: { exact: "Trecho" } },
    passage_id: "a".repeat(64),
    passage_id_version: 1,
    source: "user_selection",
    author_type: "user",
    reading_session_id: null,
    study_session_id: "session-1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
  savedAnnotations.push(record);
  return record;
}

function claimEvidence(overrides: Partial<ClaimEvidence> = {}): ClaimEvidence {
  return {
    claim: {
      id: "claim-1",
      text: "A materialidade é julgada caso a caso",
      normalized_text: "a materialidade é julgada caso a caso",
      provenance: "user_authored",
      status: "accepted",
      provider: null,
      model: null,
      confidence: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      accepted_at: "2026-01-01T00:00:00Z",
    },
    relation: "supports",
    relation_id: "relation-1",
    relation_provenance: "user_authored",
    relation_status: "accepted",
    annotation_id: "annotation-1",
    document_id: "doc-1",
    document_title: "Artigo",
    page_number: 1,
    quote: "Trecho",
    comment: null,
    ...overrides,
  };
}

function conceptOverview(overrides: Partial<ConceptOverview> = {}): ConceptOverview {
  return {
    id: "concept-1",
    name: "Materialidade",
    annotation_count: 2,
    documents: [{ document_id: "doc-1", document_title: "Artigo", annotation_count: 2 }],
    associations: [],
    ...overrides,
  };
}

/** Abre um `<details>` da barra lateral pelo texto do resumo. */
function openSidebarDetails(title: string) {
  const summary = Array.from(document.querySelectorAll("summary")).find(
    (node) => node.textContent === title,
  );
  if (!summary) throw new Error(`o painel ${title} não está na barra lateral`);
  fireEvent.click(summary);
}

function selectQuote(quote = "Trecho") {
  const textNode = document.querySelector(".react-pdf__Page span")?.firstChild as Text;
  const range = document.createRange();
  range.setStart(textNode, 0);
  range.setEnd(textNode, quote.length);

  vi.spyOn(window, "getSelection").mockReturnValue({
    isCollapsed: false,
    rangeCount: 1,
    getRangeAt: () => range,
    removeAllRanges: vi.fn(),
    toString: () => quote,
  } as unknown as Selection);

  range.getClientRects = () =>
    [{ left: 20, top: 40, right: 120, bottom: 60, width: 100, height: 20 }] as unknown as DOMRectList;
  range.getBoundingClientRect = () =>
    ({ ...pageBounds, right: 120, bottom: 60, width: 100, height: 20 }) as DOMRect;

  fireEvent.mouseUp(document.querySelector(".document-stage") as Element);
}

let currentDetail: StudySessionDetail;
let titles: Record<string, string>;
let savedAnnotations: Annotation[];
let currentClaims: ClaimEvidence[];
let currentConcepts: ConceptOverview[];
let currentAIConfig: AIModelConfig | null;
let relationSequence: number;

async function startSession(
  options: { theme?: string | null; documents?: SessionDocument[]; suggestions?: ThemeSuggestion[]; openPanels?: boolean } = {},
) {
  const theme = options.theme ?? null;
  currentDetail = sessionDetail({
    id: "session-1",
    theme,
    theme_origin: theme ? "user" : null,
    documents: options.documents ?? [],
    suggestions: options.suggestions ?? [],
  });
  render(<App />);
  const input = await screen.findByLabelText("Tema da sessão (opcional)");
  if (theme) fireEvent.change(input, { target: { value: theme } });
  fireEvent.click(screen.getByRole("button", { name: "Começar sessão" }));
  await waitFor(() => expect(screen.getByText("Sessão de estudo")).toBeInTheDocument());
  if (options.openPanels !== false) {
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    fireEvent.click(screen.getByText("Tema e sugestões da sessão"));
  }
}

function composer() {
  return document.querySelector(".comment-composer");
}

describe("App", () => {
  beforeEach(() => {
    currentDetail = sessionDetail();
    titles = {};
    savedAnnotations = [];
    currentClaims = [];
    currentConcepts = [];
    currentAIConfig = null;
    relationSequence = 0;
    Object.values(api).forEach((fn) => fn.mockReset());
    readPageCount.mockReset();
    pdfDocument.props = null;
    pdfDocument.pageRendered = null;

    api.currentAccount.mockResolvedValue(testAccount);
    api.logout.mockResolvedValue(undefined);
    api.listDocuments.mockResolvedValue([]);
    api.listDocumentBookmarks.mockResolvedValue([]);
    api.deleteDocumentBookmark.mockResolvedValue(undefined);
    api.setDocumentBookmark.mockImplementation((documentId: string, pageNumber: number) => Promise.resolve({
      document_id: documentId, page_number: pageNumber,
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    }));
    api.listStudySessions.mockResolvedValue([]);
    api.getReadingProgress.mockResolvedValue({ documents: {}, sessions: {} });
    api.recordViewedPage.mockImplementation((sessionId: string, documentId: string) => Promise.resolve({
      study_session_id: sessionId, document_id: documentId,
      session: { progress: { viewed_pages: 1, total_pages: 4, percent: 25 }, documents: { [documentId]: { viewed_pages: 1, total_pages: 4, percent: 25 } } },
      document_global: { viewed_pages: 1, total_pages: 4, percent: 25 },
    }));
    api.listAnnotations.mockImplementation((documentId: string) =>
      Promise.resolve(savedAnnotations.filter((item) => item.document_id === documentId)),
    );
    // Afirmações e conceitos vêm do servidor já escopados ao dono; aqui o mock só reflete o
    // que foi gravado, incluindo o reaproveitamento da afirmação de mesmo texto.
    api.listClaims.mockImplementation(() => Promise.resolve([...currentClaims]));
    api.createClaim.mockImplementation((input: {
      annotation_id: string;
      text: string;
      relation: "supports" | "contradicts";
    }) => {
      const source = savedAnnotations.find((item) => item.id === input.annotation_id) ?? null;
      const normalized = input.text.trim().replace(/\s+/g, " ").toLowerCase();
      const already = currentClaims.find(
        (entry) =>
          entry.claim.normalized_text === normalized &&
          entry.annotation_id === input.annotation_id,
      );
      if (already) return Promise.resolve(already);
      const reused = currentClaims.find(
        (entry) => entry.claim.normalized_text === normalized,
      );
      const evidence = claimEvidence({
        claim: reused
          ? reused.claim
          : {
              ...claimEvidence().claim,
              id: `claim-${currentClaims.length + 1}`,
              text: input.text.trim(),
              normalized_text: normalized,
            },
        relation: input.relation,
        relation_id: `relation-${++relationSequence}`,
        annotation_id: input.annotation_id,
        document_id: source?.document_id ?? null,
        page_number: source?.page_number ?? null,
        quote: source?.quote ?? "",
      });
      currentClaims = [evidence, ...currentClaims];
      return Promise.resolve(evidence);
    });
    api.deleteClaim.mockImplementation((claimId: string) => {
      currentClaims = currentClaims.filter((entry) => entry.claim.id !== claimId);
      return Promise.resolve(undefined);
    });
    api.listConcepts.mockImplementation(() => Promise.resolve([...currentConcepts]));
    api.createConceptRelation.mockImplementation((sourceId: string, targetId: string) => {
      const source = currentConcepts.find((item) => item.id === sourceId);
      const target = currentConcepts.find((item) => item.id === targetId);
      const existing = source?.associations.find(
        (association) => association.outgoing && association.other_concept_id === targetId,
      );
      if (existing || !source || !target) {
        return Promise.resolve({ id: existing?.relation_id ?? "relation-existing" });
      }
      const relationId = `relation-${++relationSequence}`;
      source.associations = [
        ...source.associations,
        {
          relation_id: relationId,
          source_concept_id: sourceId,
          target_concept_id: targetId,
          other_concept_id: targetId,
          other_concept_name: target.name,
          outgoing: true,
          provenance: "user_authored",
          status: "accepted",
          created_at: "2026-01-01T00:00:00Z",
        },
      ];
      target.associations = [
        ...target.associations,
        {
          relation_id: relationId,
          source_concept_id: sourceId,
          target_concept_id: targetId,
          other_concept_id: sourceId,
          other_concept_name: source.name,
          outgoing: false,
          provenance: "user_authored",
          status: "accepted",
          created_at: "2026-01-01T00:00:00Z",
        },
      ];
      return Promise.resolve({ id: relationId });
    });
    api.deleteClaimRelation.mockImplementation((relationId: string) => {
      currentConcepts = currentConcepts.map((concept) => ({
        ...concept,
        associations: concept.associations.filter(
          (association) => association.relation_id !== relationId,
        ),
      }));
      return Promise.resolve({ id: relationId });
    });
    api.getQuestionSources.mockResolvedValue({
      annotation_id: "annotation-1", study_session_id: "session-1",
      status: "pending", version: 1, last_error: null, sources: [],
    });
    // A configuração de modelo vive no servidor e nunca devolve a chave: aqui a leitura
    // reflete o que foi gravado, e a gravação sem chave mantém a que já existia.
    api.getAIModelConfig.mockImplementation(() => Promise.resolve(currentAIConfig));
    api.saveAIModelConfig.mockImplementation((input: AIModelConfigInput) => {
      currentAIConfig = {
        provider: input.provider,
        model: input.model,
        base_url: input.base_url ?? null,
        enabled: input.enabled,
        has_key: Boolean(input.api_key) || (currentAIConfig?.has_key ?? false),
      };
      return Promise.resolve(currentAIConfig);
    });
    api.deleteAIModelConfig.mockImplementation(() => {
      currentAIConfig = null;
      return Promise.resolve(undefined);
    });
    api.startReadingSession.mockImplementation((input: { document_id: string; page_number: number | null; filename: string | null }) =>
      Promise.resolve({
        id: "reading-1",
        document_id: input.document_id,
        filename_snapshot: input.filename ?? "documento.pdf",
        started_at: "2026-01-01T00:00:00Z",
        ended_at: null,
        start_page: input.page_number,
        end_page: input.page_number,
        last_activity_at: "2026-01-01T00:00:00Z",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      }),
    );
    api.updateReadingSession.mockImplementation((sessionId: string) =>
      Promise.resolve({
        id: sessionId,
        document_id: "doc-1",
        filename_snapshot: "documento.pdf",
        started_at: "2026-01-01T00:00:00Z",
        ended_at: "2026-01-01T00:05:00Z",
        start_page: 1,
        end_page: 1,
        last_activity_at: "2026-01-01T00:05:00Z",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:05:00Z",
      }),
    );
    api.createStudySession.mockImplementation(() => Promise.resolve(currentDetail));
    api.getStudySession.mockImplementation(() => Promise.resolve(currentDetail));
    api.attachDocumentToSession.mockImplementation((_sessionId: string, documentId: string) => {
      const link = sessionDocument(documentId, titles[documentId] ?? documentId, currentDetail.documents.length);
      currentDetail = sessionDetail({ ...currentDetail, documents: [...currentDetail.documents, link] });
      return Promise.resolve(link);
    });
    api.detachDocumentFromSession.mockImplementation((_sessionId: string, documentId: string) => {
      currentDetail = sessionDetail({
        ...currentDetail,
        documents: currentDetail.documents.filter((item) => item.document_id !== documentId),
      });
      return Promise.resolve(undefined);
    });
    api.uploadDocument.mockImplementation((file: File) => {
      const title = file.name.replace(/\.pdf$/, "");
      titles[file.name] = title;
      return Promise.resolve(documentSummary(file.name, title));
    });
    api.setStudySessionTheme.mockImplementation((_sessionId: string, theme: string) => {
      currentDetail = sessionDetail({ ...currentDetail, theme, theme_origin: "user" });
      return Promise.resolve(currentDetail);
    });
    api.requestThemeSuggestion.mockImplementation(() => {
      currentDetail = sessionDetail({ ...currentDetail, suggestions: [suggestion()] });
      return Promise.resolve(suggestion());
    });
    api.acceptThemeSuggestion.mockImplementation(() => {
      currentDetail = sessionDetail({
        ...currentDetail,
        theme: "embeddings · recuperação",
        theme_origin: "ai_suggestion",
        suggestions: [suggestion({ status: "accepted", accepted_at: "2026-01-01T00:00:00Z" })],
      });
      return Promise.resolve(currentDetail);
    });
    api.rejectThemeSuggestion.mockImplementation(() => {
      currentDetail = sessionDetail({
        ...currentDetail,
        suggestions: [suggestion({ status: "rejected", rejected_at: "2026-01-01T00:00:00Z" })],
      });
      return Promise.resolve(currentDetail);
    });
    api.createAnnotation.mockImplementation((input: CreateAnnotationInput) =>
      Promise.resolve({
        author_type: "user",
        comment: input.comment,
        created_at: "2026-01-01T00:00:00Z",
        document_id: input.document_id,
        id: "annotation-1",
        page_number: input.page_number,
        passage_id: "a".repeat(64),
        passage_id_version: 1,
        position: { rects: [], textQuoteSelector: { exact: input.quote }, version: 1 },
        quote: input.quote,
        reading_session_id: input.reading_session_id,
        study_session_id: input.study_session_id,
        source: "user_selection",
        type: input.type,
        updated_at: "2026-01-01T00:00:00Z",
      } satisfies Annotation),
    );

    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:notely/documento"),
      revokeObjectURL: vi.fn(),
    });
    vi.stubGlobal("prompt", vi.fn());
    Element.prototype.getBoundingClientRect = () => pageBounds as DOMRect;
    readPageCount.mockResolvedValue(4);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows a verified source preview before explicit navigation", async () => {
    api.listAnnotations.mockResolvedValue([{
      id: "question-1", document_id: "doc-1", page_number: 1,
      type: "question", quote: "Trecho", comment: "Como funciona?",
      position: { version: 1, rects: [], textQuoteSelector: { exact: "Trecho" } },
      passage_id: "a".repeat(64), passage_id_version: 1,
      source: "user_selection", author_type: "user",
      reading_session_id: null, study_session_id: "session-1",
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    } satisfies Annotation]);
    api.getQuestionSources.mockResolvedValue({
      annotation_id: "question-1", study_session_id: "session-1", status: "ready",
      version: 1, last_error: null,
      sources: [{
        id: "source-1", document_id: "doc-2", document_title: "Outra fonte",
        page_number: 2, excerpt: "Trecho verificável", reason: "Termos em comum",
        rank: 1, provider: "lexical_search", model: "simple-tsvector-v1", available: true,
      }],
    });
    await startSession({
      documents: [sessionDocument("doc-1", "Origem", 0), sessionDocument("doc-2", "Outra fonte", 1)],
      openPanels: false,
    });
    await screen.findByText("Como funciona?");
    fireEvent.click(screen.getByRole("button", { name: "Anotações 1" }));
    fireEvent.click(screen.getByRole("button", { name: /^Ver fontes/ }));
    expect(await screen.findByText("Trecho verificável", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("tab", { selected: true })).toHaveTextContent("Origem");
    fireEvent.click(screen.getByRole("button", { name: /Outra fonte · página 2/ }));
    expect(await screen.findByText("Prévia · página 2")).toBeInTheDocument();
    expect(screen.getByRole("tab", { selected: true })).toHaveTextContent("Origem");
    fireEvent.click(screen.getByRole("button", { name: "Abrir no Reader" }));
    expect(screen.getByRole("tab", { selected: true })).toHaveTextContent("Outra fonte");
    expect(screen.getByLabelText("Ir para página")).toHaveValue(2);

    fireEvent.click(screen.getByRole("tab", { name: /Origem/ }));
    fireEvent.click(screen.getByRole("button", { name: "Anotações 1" }));
    fireEvent.click(screen.getByRole("button", { name: /^Ver fontes/ }));
    const sourceButton = await screen.findByRole("button", { name: /Outra fonte · página 2/ });
    fireEvent.click(screen.getByLabelText("Remover Outra fonte da sessão"));
    await waitFor(() => expect(sourceButton).toBeDisabled());
    expect(screen.getByText("Fonte indisponível nesta sessão")).toBeInTheDocument();
  });

  it("shows no source distinctly and can request a retry", async () => {
    api.listAnnotations.mockResolvedValue([{
      id: "question-2", document_id: "doc-1", page_number: 1,
      type: "question", quote: "Trecho", comment: "Outra dúvida",
      position: { version: 1, rects: [], textQuoteSelector: { exact: "Trecho" } },
      passage_id: "a".repeat(64), passage_id_version: 1,
      source: "user_selection", author_type: "user", reading_session_id: null,
      study_session_id: "session-1", created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    } satisfies Annotation]);
    api.getQuestionSources.mockImplementation(() => Promise.resolve({
      annotation_id: "question-2", study_session_id: "session-1",
      status: api.retryQuestionSources.mock.calls.length ? "pending" : "no_source",
      version: api.retryQuestionSources.mock.calls.length ? 2 : 1,
      last_error: null, sources: [],
    }));
    api.retryQuestionSources.mockResolvedValue({
      annotation_id: "question-2", study_session_id: "session-1",
      status: "pending", version: 2, last_error: null, sources: [],
    });
    await startSession({ documents: [sessionDocument("doc-1", "Origem", 0)], openPanels: false });
    await screen.findByText("Outra dúvida");
    fireEvent.click(screen.getByRole("button", { name: "Anotações 1" }));
    fireEvent.click(screen.getByRole("button", { name: /^Ver fontes/ }));
    expect(await screen.findByText("Nenhuma fonte útil encontrada nesta sessão.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    await waitFor(() => expect(api.retryQuestionSources).toHaveBeenCalledWith("session-1", "question-2"));
    expect(await screen.findByText("Procurando fontes nesta sessão…")).toBeInTheDocument();
  });

  it("keeps the question visible when curation fails", async () => {
    api.listAnnotations.mockResolvedValue([{
      id: "question-failed", document_id: "doc-1", page_number: 1,
      type: "question", quote: "Trecho", comment: "Dúvida preservada",
      position: { version: 1, rects: [], textQuoteSelector: { exact: "Trecho" } },
      passage_id: "a".repeat(64), passage_id_version: 1,
      source: "user_selection", author_type: "user", reading_session_id: null,
      study_session_id: "session-1", created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    } satisfies Annotation]);
    api.getQuestionSources.mockResolvedValue({
      annotation_id: "question-failed", study_session_id: "session-1",
      status: "failed", version: 1, last_error: "Provider indisponível", sources: [],
    });
    await startSession({ documents: [sessionDocument("doc-1", "Origem", 0)], openPanels: false });
    await screen.findByText("Dúvida preservada");
    fireEvent.click(screen.getByRole("button", { name: "Anotações 1" }));
    fireEvent.click(screen.getByRole("button", { name: /^Ver fontes/ }));
    expect(await screen.findByText("Curadoria indisponível. Sua dúvida continua salva.")).toBeInTheDocument();
    expect(screen.getByText("Dúvida preservada")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeInTheDocument();
  });

  it("starts a study session with the theme the user wrote", async () => {
    await startSession({ theme: "IFRS e recuperação" });

    expect(api.createStudySession).toHaveBeenCalledWith("IFRS e recuperação");
    expect(screen.getByText("IFRS e recuperação")).toBeInTheDocument();
    expect(screen.getByText("escrito por você")).toBeInTheDocument();
    expect(screen.getByText("Traga os PDFs deste tema.")).toBeInTheDocument();
  });

  it("keeps several documents in tabs and only renders the active one", async () => {
    await startSession();

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const first = new File(["%PDF-1.4"], "ifrs.pdf", { type: "application/pdf" });
    const second = new File(["%PDF-1.4"], "embeddings.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [first, second] } });

    await waitFor(() => expect(api.attachDocumentToSession).toHaveBeenCalledTimes(2));
    const tabs = await screen.findAllByRole("tab");
    await waitFor(() => expect(tabs.map((tab) => tab.textContent)).toEqual(["ifrs0%", "embeddings0%"]));
    expect(pdfDocument.props?.file).toBe("blob:notely/documento");

    fireEvent.click(tabs[1]);
    await waitFor(() => expect(api.listAnnotations).toHaveBeenCalledWith("embeddings.pdf"));
    expect(pdfDocument.props?.file).toBe("blob:notely/documento");
  });

  it("records a rendered page once and updates session and file progress", async () => {
    await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0)], openPanels: false });
    await waitFor(() => expect(screen.getByRole("tab", { name: "Artigo0%" })).toBeInTheDocument());
    await act(async () => { pdfDocument.pageRendered?.(); });
    await waitFor(() => expect(api.recordViewedPage).toHaveBeenCalledWith("session-1", "doc-1", 1));
    expect(screen.getByRole("tab", { name: "Artigo25%" })).toBeInTheDocument();
    expect(screen.getByText("25% · 1/4 páginas visualizadas")).toBeInTheDocument();
    await act(async () => { pdfDocument.pageRendered?.(); });
    expect(api.recordViewedPage).toHaveBeenCalledTimes(1);
  });

  it("keeps reading available when progress registration fails", async () => {
    api.recordViewedPage.mockRejectedValue(new Error("temporarily unavailable"));
    await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0)], openPanels: false });
    await act(async () => { pdfDocument.pageRendered?.(); });
    await waitFor(() => expect(screen.getByRole("tab", { name: "Artigo0%" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Próxima página" }));
    expect(screen.getByLabelText("Ir para página")).toHaveValue(2);
  });

  it("opens a document already ingested through the library", async () => {
    api.listDocuments.mockResolvedValue([documentSummary("doc-9", "Artigo antigo")]);
    await startSession();

    const select = await screen.findByLabelText("Documentos já ingeridos");
    fireEvent.change(select, { target: { value: "doc-9" } });
    fireEvent.submit(select.closest("form") as HTMLFormElement);

    await waitFor(() => expect(api.attachDocumentToSession).toHaveBeenCalledWith("session-1", "doc-9"));
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-9/content"));
  });

  it("asks for a theme suggestion and only applies it when accepted", async () => {
    await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0)] });
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-1/content"));

    fireEvent.click(screen.getByRole("button", { name: "Sugerir tema com IA" }));

    const card = await screen.findByText("Sugestão · heuristic/frequency-v1");
    expect(card).toBeInTheDocument();
    expect(screen.getByText("embeddings · recuperação")).toBeInTheDocument();
    expect(screen.getByText("não definido")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Aceitar" }));

    await waitFor(() => expect(api.acceptThemeSuggestion).toHaveBeenCalledWith("session-1", "suggestion-1"));
    await waitFor(() => expect(screen.getByText("aceito de uma sugestão")).toBeInTheDocument());
  });

  it("rejects a suggestion without touching the theme", async () => {
    await startSession({
      theme: "tema do usuário",
      documents: [sessionDocument("doc-1", "Artigo", 0)],
      suggestions: [suggestion()],
    });
    await waitFor(() => expect(screen.getByText("Sugestão · heuristic/frequency-v1")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Descartar" }));

    await waitFor(() => expect(api.rejectThemeSuggestion).toHaveBeenCalledWith("session-1", "suggestion-1"));
    await waitFor(() => expect(screen.queryByText("Sugestão · heuristic/frequency-v1")).not.toBeInTheDocument());
    expect(screen.getByText("tema do usuário")).toBeInTheDocument();
  });

  it("writes the annotation comment in the Reader instead of a browser dialog", async () => {
    await startSession({ theme: "tema", documents: [sessionDocument("doc-1", "Artigo", 0)] });
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-1/content"));

    selectQuote();
    fireEvent.click(await screen.findByTitle("Nota"));

    const textarea = within(composer() as HTMLElement).getByRole("textbox");
    expect(textarea).toHaveFocus();
    expect(window.prompt).not.toHaveBeenCalled();

    fireEvent.change(textarea, { target: { value: "  ideia para voltar depois  " } });
    fireEvent.click(within(composer() as HTMLElement).getByRole("button", { name: "Salvar" }));

    await waitFor(() => {
      expect(api.createAnnotation).toHaveBeenCalledWith(
        expect.objectContaining({ comment: "ideia para voltar depois", document_id: "doc-1", quote: "Trecho" }),
      );
    });
    expect(window.prompt).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText("ideia para voltar depois")).toBeInTheDocument());
  });

  it("requires the question text before saving a dúvida", async () => {
    await startSession({ theme: "tema", documents: [sessionDocument("doc-1", "Artigo", 0)] });
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-1/content"));

    selectQuote();
    fireEvent.click(await screen.findByTitle("Dúvida"));

    const save = within(composer() as HTMLElement).getByRole("button", { name: "Salvar" });
    expect(save).toBeDisabled();
    fireEvent.change(within(composer() as HTMLElement).getByRole("textbox"), {
      target: { value: "Isso se aplica aqui?" },
    });
    expect(save).toBeEnabled();
  });

  it("saves a highlight without opening the composer", async () => {
    await startSession({ theme: "tema", documents: [sessionDocument("doc-1", "Artigo", 0)] });
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-1/content"));

    selectQuote();
    fireEvent.click(await screen.findByTitle("Destacar"));

    await waitFor(() => {
      expect(api.createAnnotation).toHaveBeenCalledWith(expect.objectContaining({ comment: null, type: "highlight" }));
    });
    expect(composer()).toBeNull();
  });

  it("navigates pages and keeps the page per document", async () => {
    await startSession({
      theme: "tema",
      documents: [
        sessionDocument("doc-1", "Artigo", 0, 3),
        sessionDocument("doc-2", "Anexo", 1, 5),
      ],
    });
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-1/content"));

    const [previous, next] = Array.from(
      document.querySelectorAll(".page-controls button"),
    ) as HTMLButtonElement[];

    expect(document.querySelector(".page-controls span")?.textContent).toBe("/ 3");
    expect(previous.disabled).toBe(true);

    const stage = document.querySelector(".document-stage") as HTMLDivElement;
    stage.scrollTop = 160;
    fireEvent.click(next);
    await waitFor(() =>
      expect(document.querySelector(".page-controls span")?.textContent).toBe("/ 3"),
    );
    expect(stage.scrollTop).toBe(0);
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "2");
    expect(previous.disabled).toBe(false);

    // Cada documento guarda a própria página.
    fireEvent.click(screen.getAllByRole("tab")[1]);
    await waitFor(() =>
      expect(document.querySelector(".page-controls span")?.textContent).toBe("/ 5"),
    );

    fireEvent.click(screen.getAllByRole("tab")[0]);
    await waitFor(() =>
      expect(document.querySelector(".page-controls span")?.textContent).toBe("/ 3"),
    );
  });

  it("opens on the saved bookmark and changes it only when asked", async () => {
    api.listDocumentBookmarks.mockResolvedValue([
      { document_id: "doc-1", page_number: 3, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
      { document_id: "doc-2", page_number: 2, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
    ]);
    await startSession({ documents: [
      sessionDocument("doc-1", "Artigo", 0, 5),
      sessionDocument("doc-2", "Anexo", 1, 4),
    ] });
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "3");
    fireEvent.click(screen.getAllByRole("tab")[1]);
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "2");
    fireEvent.click(screen.getAllByRole("tab")[0]);
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "3");
    fireEvent.click(screen.getByRole("button", { name: "Próxima página" }));
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "4");
    expect(api.setDocumentBookmark).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Mover marcador para a página atual" }));
    await waitFor(() => expect(api.setDocumentBookmark).toHaveBeenCalledWith("doc-1", 4));
    expect(await screen.findByRole("button", { name: "Ir para a página marcada 4" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Página anterior" }));
    fireEvent.click(screen.getByRole("button", { name: "Ir para a página marcada 4" }));
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "4");
    fireEvent.click(screen.getByRole("button", { name: "Remover marcador" }));
    await waitFor(() => expect(api.deleteDocumentBookmark).toHaveBeenCalledWith("doc-1"));
    expect(screen.getByRole("button", { name: "Marcar página atual" })).toBeInTheDocument();
  });

  it("keeps reading when bookmark loading or saving fails", async () => {
    api.listDocumentBookmarks.mockRejectedValue(new Error("offline"));
    api.setDocumentBookmark.mockRejectedValue(new Error("offline"));
    await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0, 3)] });
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "1");
    fireEvent.click(screen.getByRole("button", { name: "Marcar página atual" }));
    await waitFor(() => expect(api.setDocumentBookmark).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /Ir para a página marcada/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Próxima página" }));
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "2");
  });

  it("keeps a bookmark visible when removal fails", async () => {
    api.listDocumentBookmarks.mockResolvedValue([{
      document_id: "doc-1", page_number: 2,
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    }]);
    api.deleteDocumentBookmark.mockRejectedValue(new Error("offline"));
    await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0, 3)] });
    fireEvent.click(screen.getByRole("button", { name: "Remover marcador" }));
    await waitFor(() => expect(api.deleteDocumentBookmark).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Ir para a página marcada 2" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Próxima página" }));
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "3");
  });

  it("keeps navigating after an annotation is saved", async () => {
    await startSession({ theme: "tema", documents: [sessionDocument("doc-1", "Artigo", 0, 3)] });
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-1/content"));

    selectQuote();
    fireEvent.click(await screen.findByTitle("Destacar"));
    await waitFor(() => expect(api.createAnnotation).toHaveBeenCalled());

    const [, next] = Array.from(
      document.querySelectorAll(".page-controls button"),
    ) as HTMLButtonElement[];
    fireEvent.click(next);

    await waitFor(() =>
      expect(document.querySelector(".page-controls span")?.textContent).toBe("/ 3"),
    );
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "2");
  });

  it("jumps to a valid page and rejects pages outside the document", async () => {
    await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0, 4)] });
    const input = screen.getByRole("spinbutton", { name: "Ir para página" });
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "3");
    const updated = screen.getByRole("spinbutton", { name: "Ir para página" });
    for (const value of ["0", "5", "2.5", ""]) {
      fireEvent.change(updated, { target: { value } });
      fireEvent.submit(updated.closest("form") as HTMLFormElement);
      expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "3");
    }
  });

  it("can enlarge the PDF beyond 180%", async () => {
    await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0)] });
    const enlarge = screen.getByRole("button", { name: "Aumentar zoom" });
    for (let index = 0; index < 8; index += 1) fireEvent.click(enlarge);
    expect(screen.getByText("190%")).toBeInTheDocument();
    expect(enlarge).toBeEnabled();
  });

  it("collapses the notes panel to give the PDF more space", async () => {
    await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0)], openPanels: false });
    const toggle = screen.getByRole("button", { name: "Anotações 0" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById("reader-notes")).not.toBeVisible();
    expect(document.querySelector(".react-pdf__Page")).toBeVisible();
    fireEvent.click(toggle);
    expect(document.getElementById("reader-notes")).toBeVisible();
  });

  it.each(["Próxima página", "Aumentar zoom"])(
    "discards an unsaved comment when using %s",
    async (control) => {
      await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0)] });
      selectQuote();
      fireEvent.click(await screen.findByTitle("Nota"));
      expect(composer()).not.toBeNull();

      fireEvent.click(screen.getByRole("button", { name: control }));

      await waitFor(() => expect(composer()).toBeNull());
      expect(screen.queryByTitle("Destacar")).not.toBeInTheDocument();
      expect(api.createAnnotation).not.toHaveBeenCalled();
      selectQuote();
      fireEvent.click(await screen.findByTitle("Destacar"));
      await waitFor(() => expect(api.createAnnotation).toHaveBeenCalledWith(
        expect.objectContaining({ page_number: control === "Próxima página" ? 2 : 1 }),
      ));
    },
  );

  it.each([
    ["onSourceError", "Não foi possível carregar o arquivo PDF."],
    ["onLoadError", "O PDF não pôde ser interpretado."],
  ] as const)("reports PDF %s failures in the Reader", async (callback, message) => {
    await startSession({ documents: [sessionDocument("doc-1", "Artigo", 0)] });
    await waitFor(() => expect(pdfDocument.props).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Próxima página" }));
    // Trigger the same callback react-pdf calls when fetching or parsing fails.
    act(() => pdfDocument.props?.[callback]());
    expect(screen.getByText(message)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Fechar sessão" }));
    expect(await screen.findByLabelText("Tema da sessão (opcional)")).toBeInTheDocument();
  });

  it("starts the reading session on the first real interaction, not on upload", async () => {
    await startSession({ theme: "tema", documents: [sessionDocument("doc-1", "Artigo", 0)] });
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-1/content"));

    // Abrir o documento não conta como leitura.
    expect(api.startReadingSession).not.toHaveBeenCalled();

    selectQuote();

    await waitFor(() => {
      expect(api.startReadingSession).toHaveBeenCalledWith({
        document_id: "doc-1",
        filename: "Artigo.pdf",
        page_number: 1,
      });
    });
  });

  it("sends the reading session with the annotation", async () => {
    await startSession({ theme: "tema", documents: [sessionDocument("doc-1", "Artigo", 0)] });
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-1/content"));

    selectQuote();
    await waitFor(() => expect(api.startReadingSession).toHaveBeenCalled());

    fireEvent.click(await screen.findByTitle("Destacar"));

    await waitFor(() => {
      expect(api.createAnnotation).toHaveBeenCalledWith(
        expect.objectContaining({ reading_session_id: "reading-1" }),
      );
    });
  });

  it("ends the reading session when the document tab is closed", async () => {
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0), sessionDocument("doc-2", "Outro", 1)],
    });
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-1/content"));

    selectQuote();
    await waitFor(() => expect(api.startReadingSession).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText("Remover Artigo da sessão"));

    await waitFor(() => expect(api.detachDocumentFromSession).toHaveBeenCalledWith("session-1", "doc-1"));
    await waitFor(() =>
      expect(api.updateReadingSession).toHaveBeenCalledWith("reading-1", { ended: true }),
    );
  });

  it("reports when the API is unavailable", async () => {
    api.listDocuments.mockRejectedValue(new TypeError("Failed to fetch"));
    api.listStudySessions.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<App />);

    await waitFor(() => {
      expect(
        screen.getByText("A API do Notely não está disponível. Confirme se os serviços estão em execução."),
      ).toBeInTheDocument();
    });
  });

  it("resumes an existing session from the library", async () => {
    const open = sessionDetail({
      id: "session-7",
      theme: "Sessão anterior",
      theme_origin: "user",
      documents: [sessionDocument("doc-4", "Capítulo", 0)],
    });
    api.listStudySessions.mockResolvedValue([open]);
    currentDetail = open;
    render(<App />);

    const item = await screen.findByText("Sessão anterior");
    fireEvent.click(item.closest("button") as HTMLButtonElement);

    await waitFor(() => expect(api.getStudySession).toHaveBeenCalledWith("session-7"));
    await waitFor(() => expect(pdfDocument.props?.file).toBe("/api/documents/doc-4/content"));
    expect(within(document.querySelector(".document-tabs") as HTMLElement).getByText("Capítulo")).toBeInTheDocument();
  });
  it("shows the login screen when there is no session", async () => {
    api.currentAccount.mockRejectedValue(new Error("sem sessão"));
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Notely" })).toBeInTheDocument();
    expect(screen.getByLabelText("Senha")).toBeInTheDocument();
    // Sem sessão o Reader não é montado nem busca dados.
    expect(api.listDocuments).not.toHaveBeenCalled();
  });

  it("returns to the login screen after signing out", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "Sair" }));

    await waitFor(() => expect(api.logout).toHaveBeenCalled());
    expect(await screen.findByRole("heading", { name: "Notely" })).toBeInTheDocument();
  });

  it("asserts from an annotated excerpt and lists the claim with its evidence", async () => {
    const record = annotation("doc-1", { quote: "A materialidade é julgada caso a caso", page_number: 2 });
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0, 5)],
      openPanels: false,
    });
    await screen.findByText("A materialidade é julgada caso a caso");
    fireEvent.click(screen.getByRole("button", { name: "Anotações 1" }));

    fireEvent.click(screen.getByRole("button", { name: /Afirmar a partir deste trecho/ }));
    fireEvent.change(screen.getByLabelText("Texto da afirmação"), {
      target: { value: "  A materialidade depende do julgamento profissional  " },
    });
    fireEvent.change(screen.getByLabelText("Relação com o trecho"), {
      target: { value: "contradicts" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar afirmação" }));

    await waitFor(() =>
      expect(api.createClaim).toHaveBeenCalledWith({
        annotation_id: record.id,
        text: "A materialidade depende do julgamento profissional",
        relation: "contradicts",
      }),
    );

    // A afirmação aparece na lista da sessão, com documento, página e trecho.
    openSidebarDetails("Afirmações");
    expect(
      await screen.findByText("A materialidade depende do julgamento profissional"),
    ).toBeInTheDocument();
    expect(screen.getByText("contradiz este trecho")).toBeInTheDocument();
    expect(screen.getByText("Evidência")).toBeInTheDocument();
    expect(screen.getByText(/página 2/)).toBeInTheDocument();
    expect(screen.getAllByText("A materialidade é julgada caso a caso").length).toBeGreaterThan(0);
  });

  it("reuses the claim when the same text is asserted again", async () => {
    const record = annotation("doc-1", { quote: "Trecho" });
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0)],
      openPanels: false,
    });
    await screen.findByText("Trecho");
    fireEvent.click(screen.getByRole("button", { name: "Anotações 1" }));

    const assertOnce = async () => {
      fireEvent.click(screen.getByRole("button", { name: /Afirmar a partir deste trecho/ }));
      fireEvent.change(screen.getByLabelText("Texto da afirmação"), {
        target: { value: "A materialidade é julgada caso a caso" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Salvar afirmação" }));
      await waitFor(() =>
        expect(api.createClaim).toHaveBeenCalledWith(
          expect.objectContaining({ annotation_id: record.id }),
        ),
      );
    };

    await assertOnce();
    await waitFor(() =>
      expect(screen.queryByLabelText("Texto da afirmação")).not.toBeInTheDocument(),
    );
    await assertOnce();

    openSidebarDetails("Afirmações");
    // Reafirmar o mesmo texto reaproveita a afirmação existente: uma linha, sem erro.
    await waitFor(() =>
      expect(screen.getAllByText("A materialidade é julgada caso a caso")).toHaveLength(1),
    );
    expect(api.createClaim).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("removes a claim without touching the annotation that was its evidence", async () => {
    const record = annotation("doc-1", { quote: "Trecho preservado" });
    currentClaims = [
      claimEvidence({
        annotation_id: record.id,
        document_id: "doc-1",
        quote: "Trecho preservado",
        page_number: 1,
      }),
    ];
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0)],
      openPanels: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 1" }));

    openSidebarDetails("Afirmações");
    const remove = await screen.findByRole("button", {
      name: /Remover a afirmação A materialidade é julgada caso a caso/,
    });
    fireEvent.click(remove);

    await waitFor(() => expect(api.deleteClaim).toHaveBeenCalledWith("claim-1"));
    await waitFor(() =>
      expect(
        screen.queryByText("A materialidade é julgada caso a caso"),
      ).not.toBeInTheDocument(),
    );
    // A anotação que servia de evidência continua no caderno.
    expect(screen.getByText("Trecho preservado")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Afirmar a partir deste trecho/ })).toBeEnabled();
  });

  it("marks a claim that lost its evidence instead of hiding it", async () => {
    currentClaims = [
      claimEvidence({
        relation: null,
        relation_id: null,
        relation_provenance: null,
        relation_status: null,
        annotation_id: null,
        document_id: null,
        document_title: null,
        page_number: null,
        quote: "",
      }),
    ];
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0)],
      openPanels: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Afirmações");

    expect(await screen.findByText("sem evidência")).toBeInTheDocument();
    expect(screen.getByText("A materialidade é julgada caso a caso")).toBeInTheDocument();
  });

  it("shows the failure and keeps reading when asserting fails", async () => {
    annotation("doc-1", { quote: "Trecho" });
    api.createClaim.mockRejectedValue(new ApiError(503, "Não foi possível gravar a afirmação."));
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0, 4)],
      openPanels: false,
    });
    await screen.findByText("Trecho");
    fireEvent.click(screen.getByRole("button", { name: "Anotações 1" }));

    fireEvent.click(screen.getByRole("button", { name: /Afirmar a partir deste trecho/ }));
    fireEvent.change(screen.getByLabelText("Texto da afirmação"), {
      target: { value: "Uma afirmação que não grava" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar afirmação" }));

    openSidebarDetails("Afirmações");
    // A falha aparece onde a ação aconteceu e no painel de afirmações.
    expect(
      await screen.findByText(
        "Não foi possível gravar a afirmação. O texto foi mantido para tentar de novo.",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(2);
    // O rascunho continua onde estava, e a leitura segue funcionando.
    expect(screen.getByLabelText("Texto da afirmação")).toHaveValue("Uma afirmação que não grava");
    expect(screen.getByText("Trecho")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Próxima página" }));
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "2");
    expect(api.createClaim).toHaveBeenCalledTimes(1);
  });

  it("lists only the claims of the documents in the open session", async () => {
    currentClaims = [
      claimEvidence({ document_id: "doc-1", document_title: "Artigo" }),
      claimEvidence({
        claim: { ...claimEvidence().claim, id: "claim-9", text: "Afirmação de outro documento" },
        document_id: "doc-9",
        document_title: "Outro documento",
      }),
    ];
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0)],
      openPanels: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Afirmações");

    expect(await screen.findByText("A materialidade é julgada caso a caso")).toBeInTheDocument();
    expect(screen.queryByText("Afirmação de outro documento")).not.toBeInTheDocument();
  });

  it("lists the concepts of the session with their excerpt count and the inference warning", async () => {
    currentConcepts = [
      conceptOverview(),
      conceptOverview({
        id: "concept-2",
        name: "Relevância",
        annotation_count: 1,
        documents: [
          { document_id: "doc-9", document_title: "Outro documento", annotation_count: 1 },
        ],
      }),
    ];
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0)],
      openPanels: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Conceitos e associações");

    expect(await screen.findByText("Materialidade")).toBeInTheDocument();
    expect(screen.getByText("2 trechos")).toBeInTheDocument();
    expect(
      screen.getByText(/Extraídos por modelo ou heurística de frequência/),
    ).toHaveTextContent("inferência, não conhecimento confirmado");
    // Conceito de documento fora da sessão não entra na lista.
    expect(screen.queryByText("Relevância")).not.toBeInTheDocument();
  });

  it("associates two concepts once and never duplicates the edge", async () => {
    currentConcepts = [
      conceptOverview(),
      conceptOverview({
        id: "concept-2",
        name: "Relevância",
        annotation_count: 1,
        documents: [{ document_id: "doc-1", document_title: "Artigo", annotation_count: 1 }],
      }),
    ];
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0)],
      openPanels: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Conceitos e associações");
    await screen.findByText("Materialidade");

    const select = async (name: string) => {
      const heading = Array.from(
        document.querySelectorAll(".concept-card .concept-heading strong"),
      ).find((node) => node.textContent === name);
      const card = heading?.closest(".concept-card") as HTMLElement;
      fireEvent.click(within(card).getByRole("button", { name: /^Seleciona/ }));
    };
    await select("Materialidade");
    await select("Relevância");

    expect(screen.getByText(/ordem do par é significativa hoje/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Associar conceitos" }));

    await waitFor(() =>
      expect(api.createConceptRelation).toHaveBeenCalledWith("concept-1", "concept-2"),
    );
    expect(await screen.findByText("Materialidade → Relevância")).toBeInTheDocument();

    // A seleção continua, e o par já associado não é oferecido de novo.
    expect(
      await screen.findByText("Estes dois conceitos já estão associados nesta ordem."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Associar conceitos" })).not.toBeInTheDocument();

    // Escolher os dois de novo também não oferece a aresta que já existe.
    await select("Materialidade");
    await select("Relevância");
    await select("Materialidade");
    await select("Relevância");
    expect(
      screen.getByText("Estes dois conceitos já estão associados nesta ordem."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Associar conceitos" })).not.toBeInTheDocument();
    expect(api.createConceptRelation).toHaveBeenCalledTimes(1);
  });

  it("removes an association keeping both concepts", async () => {
    const relation = {
      relation_id: "relation-7",
      source_concept_id: "concept-1",
      target_concept_id: "concept-2",
      other_concept_id: "concept-2",
      other_concept_name: "Relevância",
      outgoing: true,
      provenance: "user_authored" as const,
      status: "accepted" as const,
      created_at: "2026-01-01T00:00:00Z",
    };
    currentConcepts = [
      conceptOverview({ associations: [relation] }),
      conceptOverview({
        id: "concept-2",
        name: "Relevância",
        annotation_count: 1,
        documents: [{ document_id: "doc-1", document_title: "Artigo", annotation_count: 1 }],
        associations: [{ ...relation, outgoing: false, other_concept_id: "concept-1", other_concept_name: "Materialidade" }],
      }),
    ];
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0)],
      openPanels: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Conceitos e associações");

    expect(await screen.findByText("Materialidade → Relevância")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remover associação" }));

    await waitFor(() => expect(api.deleteClaimRelation).toHaveBeenCalledWith("relation-7"));
    await waitFor(() =>
      expect(screen.queryByText("Materialidade → Relevância")).not.toBeInTheDocument(),
    );
    // Os conceitos continuam onde estavam.
    expect(screen.getByText("Materialidade")).toBeInTheDocument();
    expect(screen.getByText("Relevância")).toBeInTheDocument();
  });

  it("keeps reading and annotating when the concept list is unavailable", async () => {
    annotation("doc-1", { quote: "Trecho" });
    api.listConcepts.mockRejectedValue(new ApiError(503, "Lista de conceitos indisponível."));
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0, 3)],
      openPanels: false,
    });
    await screen.findByText("Trecho");
    fireEvent.click(screen.getByRole("button", { name: "Anotações 1" }));
    openSidebarDetails("Conceitos e associações");

    expect(await screen.findByRole("alert")).toHaveTextContent("Lista de conceitos indisponível.");
    expect(screen.getByText("Trecho")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Próxima página" }));
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "2");
  });

  it("configures the model from the sidebar and never shows the key again", async () => {
    await startSession({ theme: "tema", openPanels: false });
    await waitFor(() => expect(api.getAIModelConfig).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Modelo de IA");

    // Sem configuração: a leitura responde 404, que vira ausência de modelo, e o
    // formulário aparece vazio.
    expect(await screen.findByText("Nenhum modelo de IA configurado.")).toBeInTheDocument();
    expect(screen.getByLabelText("Modelo")).toHaveValue("");
    expect(screen.getByLabelText("Chave da API")).toHaveValue("");

    fireEvent.change(screen.getByLabelText("Modelo"), {
      target: { value: "claude-sonnet-4-5" },
    });
    fireEvent.change(screen.getByLabelText("Chave da API"), {
      target: { value: "chave-ficticia-de-teste" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));

    await waitFor(() => expect(api.saveAIModelConfig).toHaveBeenCalledTimes(1));
    expect(api.saveAIModelConfig).toHaveBeenCalledWith({
      provider: "anthropic",
      model: "claude-sonnet-4-5",
      base_url: null,
      enabled: true,
      api_key: "chave-ficticia-de-teste",
    });

    // O painel passa a mostrar o que o servidor confirmou, só com o fato de existir chave.
    expect(await screen.findByText("gravada no servidor")).toBeInTheDocument();
    expect(screen.getByText("claude-sonnet-4-5")).toBeInTheDocument();
    expect(screen.getByLabelText("Chave da API")).toHaveValue("");
    expect(document.body.textContent).not.toContain("chave-ficticia-de-teste");
  });

  it("keeps the stored key when saving without filling the key field", async () => {
    currentAIConfig = {
      provider: "anthropic",
      model: "claude-sonnet-4-5",
      base_url: null,
      enabled: true,
      has_key: true,
    };
    await startSession({ theme: "tema", openPanels: false });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Modelo de IA");
    expect(await screen.findByText("claude-sonnet-4-5")).toBeInTheDocument();
    expect(screen.getByText("gravada no servidor")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Modelo"), {
      target: { value: "claude-opus-4-5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(api.saveAIModelConfig).toHaveBeenCalledTimes(1));
    expect(api.saveAIModelConfig.mock.calls[0][0].api_key).toBeUndefined();
    // Os campos atualizam e a chave continua gravada, sem ter sido redigitada.
    expect(await screen.findByText("claude-opus-4-5")).toBeInTheDocument();
    expect(screen.getByText("gravada no servidor")).toBeInTheDocument();
  });

  it("does not send openai_compatible without a base url", async () => {
    await startSession({ theme: "tema", openPanels: false });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Modelo de IA");
    await screen.findByText("Nenhum modelo de IA configurado.");

    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "openai_compatible" },
    });
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "llama3.1" } });
    const save = screen.getByRole("button", { name: "Cadastrar modelo" });
    expect(save).toBeDisabled();
    expect(screen.getByText(/exige a base URL antes de gravar/)).toBeInTheDocument();
    fireEvent.click(save);
    expect(api.saveAIModelConfig).not.toHaveBeenCalled();
  });

  it("removes the configuration after confirmation and returns to the empty state", async () => {
    currentAIConfig = {
      provider: "openai_compatible",
      model: "llama3.1",
      base_url: "http://localhost:11434/v1",
      enabled: true,
      has_key: true,
    };
    await startSession({ theme: "tema", openPanels: false });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Modelo de IA");
    expect(await screen.findByText("http://localhost:11434/v1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remover configuração" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(api.deleteAIModelConfig).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Remover configuração" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar remoção" }));

    await waitFor(() => expect(api.deleteAIModelConfig).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Nenhum modelo de IA configurado.")).toBeInTheDocument();
    // O formulário volta vazio depois que a configuração removida deixa de existir.
    await waitFor(() => expect(screen.getByLabelText("Modelo")).toHaveValue(""));
    // A leitura seguinte responde 404, que a tela trata como ausência de configuração.
    await expect(api.getAIModelConfig()).resolves.toBeNull();
  });

  it("shows the configuration failure and keeps reading", async () => {
    api.saveAIModelConfig.mockRejectedValue(
      new ApiError(503, "Não foi possível gravar a configuração."),
    );
    await startSession({
      theme: "tema",
      documents: [sessionDocument("doc-1", "Artigo", 0, 3)],
      openPanels: false,
    });
    fireEvent.click(screen.getByRole("button", { name: "Anotações 0" }));
    openSidebarDetails("Modelo de IA");
    await screen.findByText("Nenhum modelo de IA configurado.");

    fireEvent.change(screen.getByLabelText("Modelo"), {
      target: { value: "modelo-que-nao-grava" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));

    await waitFor(() => expect(api.saveAIModelConfig).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Não foi possível gravar a configuração.",
    );
    // O que foi digitado continua no formulário, e a leitura segue funcionando.
    expect(screen.getByLabelText("Modelo")).toHaveValue("modelo-que-nao-grava");
    fireEvent.click(screen.getByRole("button", { name: "Próxima página" }));
    expect(document.querySelector(".react-pdf__Page")).toHaveAttribute("data-page-number", "2");
  });
});
