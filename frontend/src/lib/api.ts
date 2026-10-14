import type {
  Account,
  Annotation,
  AnnotationPosition,
  AnnotationType,
  DocumentRecord,
  DocumentBookmark,
  DocumentSummary,
  Enrollment,
  ReadingSession,
  ReadingProgressSnapshot,
  ReadingProgressUpdate,
  QuestionSources,
  SessionDocument,
  SessionOutcome,
  StudySession,
  StudySessionDetail,
  ThemeSuggestion,
} from "../types";

const API_ROOT = import.meta.env.VITE_API_ROOT ?? "";

type UnauthorizedHandler = () => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

/**
 * Avisa a aplicação quando a sessão deixa de valer, para que ela volte ao login.
 * Rotas de autenticação ficam de fora: nelas um 401 é credencial errada, não sessão
 * expirada.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    throw new ApiError(response.status, detailMessage(body?.detail));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function detailMessage(detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  return "Não foi possível concluir a operação.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    // A sessão vai em cookie HttpOnly: sem isso o navegador não a envia.
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    unauthorizedHandler?.();
  }
  return parseResponse<T>(response);
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function uploadDocument(file: File, pageCount: number): Promise<DocumentRecord> {
  const form = new FormData();
  form.append("file", file);
  form.append("page_count", String(pageCount));
  const response = await fetch(`${API_ROOT}/api/documents/upload`, {
    method: "POST",
    body: form,
    credentials: "include",
  });
  return parseResponse<DocumentRecord>(response);
}

export function listDocumentBookmarks(): Promise<DocumentBookmark[]> {
  return request("/api/document-bookmarks");
}

export function setDocumentBookmark(
  documentId: string, pageNumber: number,
): Promise<DocumentBookmark> {
  return request(`/api/documents/${documentId}/bookmark`, {
    method: "PUT",
    body: JSON.stringify({ page_number: pageNumber }),
  });
}

export function deleteDocumentBookmark(documentId: string): Promise<void> {
  return request(`/api/documents/${documentId}/bookmark`, { method: "DELETE" });
}

export function documentContentUrl(documentId: string): string {
  return `${API_ROOT}/api/documents/${documentId}/content`;
}

export function listDocuments(): Promise<DocumentSummary[]> {
  return request("/api/documents");
}

export function startReadingSession(input: {
  document_id: string;
  page_number: number | null;
  filename: string | null;
}): Promise<ReadingSession> {
  return request("/api/reading-sessions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateReadingSession(
  sessionId: string,
  input: { page_number?: number | null; ended?: boolean },
): Promise<ReadingSession> {
  return request(`/api/reading-sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}

/**
 * Encerramento best-effort quando a página está sendo descarregada: o Reader não tem
 * tempo de esperar uma resposta, então a requisição sai com keepalive.
 */
export function endReadingSessionBeacon(sessionId: string): void {
  void fetch(`${API_ROOT}/api/reading-sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ended: true }),
    keepalive: true,
  }).catch(() => undefined);
}

export function listAnnotations(documentId: string): Promise<Annotation[]> {
  return request(`/api/documents/${documentId}/annotations`);
}

export function createAnnotation(input: {
  document_id: string;
  page_number: number;
  type: AnnotationType;
  quote: string;
  comment: string | null;
  position: AnnotationPosition;
  reading_session_id: string | null;
  study_session_id: string | null;
}): Promise<Annotation> {
  return request("/api/annotations", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createStudySession(theme: string | null): Promise<StudySession> {
  return request("/api/study-sessions", {
    method: "POST",
    body: JSON.stringify({ theme }),
  });
}

export function listStudySessions(): Promise<StudySession[]> {
  return request("/api/study-sessions");
}

export function getStudySession(sessionId: string): Promise<StudySessionDetail> {
  return request(`/api/study-sessions/${sessionId}`);
}

export function attachDocumentToSession(
  sessionId: string,
  documentId: string,
): Promise<SessionDocument> {
  return request(`/api/study-sessions/${sessionId}/documents`, {
    method: "POST",
    body: JSON.stringify({ document_id: documentId }),
  });
}

export function detachDocumentFromSession(
  sessionId: string,
  documentId: string,
): Promise<void> {
  return request(`/api/study-sessions/${sessionId}/documents/${documentId}`, {
    method: "DELETE",
  });
}

export function setStudySessionTheme(sessionId: string, theme: string): Promise<StudySession> {
  return request(`/api/study-sessions/${sessionId}/theme`, {
    method: "PUT",
    body: JSON.stringify({ theme }),
  });
}

export function requestThemeSuggestion(sessionId: string): Promise<ThemeSuggestion> {
  return request(`/api/study-sessions/${sessionId}/theme-suggestions`, { method: "POST" });
}

export function acceptThemeSuggestion(
  sessionId: string,
  suggestionId: string,
): Promise<StudySession> {
  return request(
    `/api/study-sessions/${sessionId}/theme-suggestions/${suggestionId}/accept`,
    { method: "POST" },
  );
}

export function rejectThemeSuggestion(
  sessionId: string,
  suggestionId: string,
): Promise<ThemeSuggestion> {
  return request(
    `/api/study-sessions/${sessionId}/theme-suggestions/${suggestionId}/reject`,
    { method: "POST" },
  );
}

export function getQuestionSources(sessionId: string, annotationId: string): Promise<QuestionSources> {
  return request(`/api/study-sessions/${sessionId}/questions/${annotationId}/sources`);
}

export function retryQuestionSources(sessionId: string, annotationId: string): Promise<QuestionSources> {
  return request(`/api/study-sessions/${sessionId}/questions/${annotationId}/sources/retry`, {
    method: "POST",
  });
}

export function getReadingProgress(): Promise<ReadingProgressSnapshot> {
  return request("/api/reading-progress");
}

export function recordViewedPage(
  sessionId: string, documentId: string, pageNumber: number,
): Promise<ReadingProgressUpdate> {
  return request(
    `/api/study-sessions/${sessionId}/documents/${documentId}/viewed-pages/${pageNumber}`,
    { method: "PUT" },
  );
}

/** Sessão: a conta autenticada vem do cookie, nunca de estado no cliente. */
export function currentAccount(): Promise<Account> {
  return request("/api/auth/me");
}

export function login(email: string, password: string): Promise<SessionOutcome> {
  return request("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function submitMfaCode(code: string): Promise<SessionOutcome> {
  return request("/api/auth/mfa", { method: "POST", body: JSON.stringify({ code }) });
}

export function submitRecoveryCode(code: string): Promise<SessionOutcome> {
  return request("/api/auth/recovery", { method: "POST", body: JSON.stringify({ code }) });
}

export function logout(): Promise<void> {
  return request("/api/auth/logout", { method: "POST" });
}

export function startMfaEnrollment(): Promise<Enrollment> {
  return request("/api/auth/mfa/enroll", { method: "POST" });
}

export function confirmMfaEnrollment(code: string): Promise<{ recovery_codes: string[] }> {
  return request("/api/auth/mfa/confirm", { method: "POST", body: JSON.stringify({ code }) });
}

export function disableMfa(password: string): Promise<void> {
  return request("/api/auth/mfa/disable", { method: "POST", body: JSON.stringify({ password }) });
}
