export type AnnotationType =
  | "highlight"
  | "note"
  | "question"
  | "important"
  | "disagreement"
  | "relation";

export interface NormalizedRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface AnnotationPosition {
  version: 1;
  rects: NormalizedRect[];
  textQuoteSelector: { exact: string };
}

export interface Annotation {
  id: string;
  document_id: string;
  page_number: number;
  type: AnnotationType;
  quote: string;
  comment: string | null;
  position: AnnotationPosition;
  source: "user_selection" | "native_pdf" | "import";
  author_type: "user";
  created_at: string;
  updated_at: string;
}

export interface DocumentRecord {
  id: string;
  sha256: string;
  title: string;
  filename: string;
  page_count: number;
  storage_uri: string;
}

export interface SelectionDraft {
  pageNumber: number;
  quote: string;
  rects: NormalizedRect[];
  toolbarX: number;
  toolbarY: number;
}

