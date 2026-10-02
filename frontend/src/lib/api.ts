import type {
  Annotation,
  AnnotationPosition,
  AnnotationType,
  DocumentRecord,
  DocumentSummary,
  SessionDocument,
  StudySession,
  StudySessionDetail,
  ThemeSuggestion,
} from "../types";

const API_ROOT = import.meta.env.VITE_API_ROOT ?? "";

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
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
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
  });
  return parseResponse<DocumentRecord>(response);
}

export function documentContentUrl(documentId: string): string {
  return `${API_ROOT}/api/documents/${documentId}/content`;
}

export function listDocuments(): Promise<DocumentSummary[]> {
  return request("/api/documents");
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
