export type AnnotationType =
  | "highlight"
  | "note"
  | "question"
  | "important"
  | "disagreement"
  | "relation";

export type SessionStage = "mfa_pending" | "active";

export interface Account {
  id: string;
  email: string;
  totp_enabled: boolean;
}

export interface SessionOutcome {
  stage: SessionStage;
  /** Com segundo fator ativo a sessão ainda não vale: falta o código. */
  mfa_required: boolean;
  user: Account;
}

export interface Enrollment {
  secret: string;
  uri: string;
}

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

export interface DocumentBookmark {
  document_id: string;
  page_number: number;
  created_at: string;
  updated_at: string;
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

export interface PageProgress {
  viewed_pages: number;
  total_pages: number;
  percent: number;
}

export interface SessionProgress {
  progress: PageProgress;
  documents: Record<string, PageProgress>;
}

export interface ReadingProgressSnapshot {
  documents: Record<string, PageProgress>;
  sessions: Record<string, SessionProgress>;
}

export interface ReadingProgressUpdate {
  study_session_id: string;
  document_id: string;
  session: SessionProgress;
  document_global: PageProgress;
}

/** Como uma evidência se liga a uma afirmação: o trecho sustenta ou contradiz. */
export type ClaimEvidenceRelation = "supports" | "contradicts";

export type ClaimRelationKind =
  | ClaimEvidenceRelation
  | "answered_by"
  | "related_to";

/** De onde a relação veio. Inferência nunca se apresenta como autoral. */
export type RelationProvenance = "ai_inference" | "user_confirmed" | "user_authored";

export type RelationStatus = "suggested" | "accepted" | "rejected";

export interface Claim {
  id: string;
  text: string;
  normalized_text: string;
  provenance: RelationProvenance;
  status: RelationStatus;
  provider: string | null;
  model: string | null;
  confidence: number | null;
  created_at: string;
  updated_at: string;
  accepted_at: string | null;
}

/**
 * Afirmação com a evidência que a sustenta. `relation` é nula quando a afirmação ficou sem
 * evidência: o dado autoral continua sendo do usuário e não sai da lista por isso.
 */
export interface ClaimEvidence {
  claim: Claim;
  relation: ClaimEvidenceRelation | null;
  relation_id: string | null;
  relation_provenance: RelationProvenance | null;
  relation_status: RelationStatus | null;
  annotation_id: string | null;
  document_id: string | null;
  document_title: string | null;
  page_number: number | null;
  quote: string;
  comment: string | null;
}

export interface ClaimRelationRecord {
  id: string;
  relation: ClaimRelationKind;
  provenance: RelationProvenance;
  status: RelationStatus;
  annotation_id: string | null;
  claim_id: string | null;
  question_annotation_id: string | null;
  answer_annotation_id: string | null;
  source_concept_id: string | null;
  target_concept_id: string | null;
  created_at: string;
  accepted_at: string | null;
}

export interface ConceptReference {
  document_id: string;
  document_title: string;
  annotation_count: number;
}

/** Associação `RELATED_TO` vista de uma das pontas. O par é ordenado. */
export interface ConceptAssociation {
  relation_id: string;
  source_concept_id: string;
  target_concept_id: string;
  other_concept_id: string;
  other_concept_name: string;
  outgoing: boolean;
  provenance: RelationProvenance;
  status: RelationStatus;
  created_at: string;
}

/**
 * Conceito extraído por modelo ou heurística: inferência, não conhecimento confirmado.
 * A contagem e os documentos são o lastro que a pessoa tem para decidir uma associação.
 */
export interface ConceptOverview {
  id: string;
  name: string;
  annotation_count: number;
  documents: ConceptReference[];
  associations: ConceptAssociation[];
}

/** Provedores aceitos pela API. `openai_compatible` cobre Ollama, LM Studio e gateways. */
export type AIModelProvider = "anthropic" | "openai_compatible";

/**
 * Configuração de modelo do dono. A chave nunca chega ao cliente: a API devolve apenas
 * `has_key`, o fato de existir uma chave gravada e cifrada em repouso.
 */
export interface AIModelConfig {
  provider: AIModelProvider;
  model: string;
  base_url: string | null;
  enabled: boolean;
  has_key: boolean;
}

/** O que a tela envia ao gravar. Sem `api_key`, a chave já gravada é mantida. */
export interface AIModelConfigInput {
  provider: AIModelProvider;
  model: string;
  api_key?: string;
  base_url?: string | null;
  enabled: boolean;
}

/**
 * Modelo oferecido pelo provedor, como a listagem devolve: `id` é o que se grava e
 * `display_name` só aparece quando o provedor manda um nome legível.
 */
export interface AIModelOption {
  id: string;
  display_name: string | null;
}

/**
 * Pedido da lista de modelos. Sem `api_key`, o servidor usa a chave já gravada; a chave
 * digitada vale só para a chamada e não é gravada.
 */
export interface AIModelListInput {
  provider: AIModelProvider;
  api_key?: string;
  base_url?: string | null;
}
