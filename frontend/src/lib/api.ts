import type {
  Annotation,
  AnnotationPosition,
  AnnotationType,
  DocumentRecord,
} from "../types";

const API_ROOT = import.meta.env.VITE_API_ROOT ?? "";

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(response.status, body?.detail ?? "Não foi possível concluir a operação.");
  }
  return response.json() as Promise<T>;
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
