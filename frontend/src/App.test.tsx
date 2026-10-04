import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Annotation, DocumentSummary, SessionDocument, StudySessionDetail, ThemeSuggestion } from "./types";

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

const pdfDocument = vi.hoisted(() => ({ props: null as DocumentProps | null }));
const api = vi.hoisted(() => ({
  acceptThemeSuggestion: vi.fn(),
  startReadingSession: vi.fn(),
  updateReadingSession: vi.fn(),
  attachDocumentToSession: vi.fn(),
  createAnnotation: vi.fn(),
  createStudySession: vi.fn(),
  detachDocumentFromSession: vi.fn(),
  getStudySession: vi.fn(),
  getQuestionSources: vi.fn(),
  retryQuestionSources: vi.fn(),
  listAnnotations: vi.fn(),
  listDocuments: vi.fn(),
  listStudySessions: vi.fn(),
  rejectThemeSuggestion: vi.fn(),
  requestThemeSuggestion: vi.fn(),
  setStudySessionTheme: vi.fn(),
  uploadDocument: vi.fn(),
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
    Page: (props: { pageNumber: number }) => (
      <div className="react-pdf__Page" data-page-number={props.pageNumber}>
        <span>Trecho selecionável do documento</span>
      </div>
    ),
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
    Object.values(api).forEach((fn) => fn.mockReset());
    readPageCount.mockReset();
    pdfDocument.props = null;

    api.listDocuments.mockResolvedValue([]);
    api.listStudySessions.mockResolvedValue([]);
    api.listAnnotations.mockResolvedValue([]);
    api.getQuestionSources.mockResolvedValue({
      annotation_id: "annotation-1", study_session_id: "session-1",
      status: "pending", version: 1, last_error: null, sources: [],
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
    expect(tabs.map((tab) => tab.textContent)).toEqual(["ifrs4 p.", "embeddings4 p."]);
    expect(pdfDocument.props?.file).toBe("blob:notely/documento");

    fireEvent.click(tabs[1]);
    await waitFor(() => expect(api.listAnnotations).toHaveBeenCalledWith("embeddings.pdf"));
    expect(pdfDocument.props?.file).toBe("blob:notely/documento");
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
});
