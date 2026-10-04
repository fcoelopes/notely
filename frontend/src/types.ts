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
  passage_id: string;
  passage_id_version: number;
  source: "user_selection" | "native_pdf" | "import";
  author_type: "user";
  reading_session_id: string | null;
  study_session_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReadingSession {
  id: string;
  document_id: string;
  filename_snapshot: string;
  started_at: string;
  ended_at: string | null;
  start_page: number | null;
  end_page: number | null;
  last_activity_at: string;
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

export interface DocumentSummary extends DocumentRecord {
  mime_type: string;
  created_at: string;
  updated_at: string;
}

export type ThemeOrigin = "user" | "ai_suggestion";

export type SuggestionStatus = "pending" | "accepted" | "rejected";

export interface StudySession {
  id: string;
  theme: string | null;
  theme_origin: ThemeOrigin | null;
  theme_updated_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionDocument {
  id: string;
  study_session_id: string;
  document_id: string;
  position: number;
  added_at: string;
  title: string;
  filename: string;
  page_count: number;
}

export interface ThemeSuggestion {
  id: string;
  suggestion_type: string;
  subject_type: string;
  subject_id: string;
  status: SuggestionStatus;
  payload: { theme?: string; rationale?: string | null };
  provider: string;
  model: string;
  created_at: string;
  accepted_at: string | null;
  rejected_at: string | null;
}

export interface StudySessionDetail extends StudySession {
  documents: SessionDocument[];
  suggestions: ThemeSuggestion[];
}

export interface SelectionDraft {
  pageNumber: number;
  quote: string;
  rects: NormalizedRect[];
  toolbarX: number;
  toolbarY: number;
}


export interface CuratedSource {
  id: string;
  document_id: string;
  document_title: string;
  page_number: number;
  excerpt: string;
  reason: string;
  rank: number;
  provider: string;
  model: string;
  available: boolean;
}

export interface QuestionSources {
  annotation_id: string;
  study_session_id: string;
  status: "pending" | "ready" | "no_source" | "failed";
  version: number;
  last_error: string | null;
  sources: CuratedSource[];
}
