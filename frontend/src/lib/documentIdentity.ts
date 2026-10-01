const KEY_PREFIX = "notely:document:";

export function rememberDocumentId(sha256: string, documentId: string): void {
  localStorage.setItem(`${KEY_PREFIX}${sha256}`, documentId);
}

export function recallDocumentId(sha256: string): string | null {
  return localStorage.getItem(`${KEY_PREFIX}${sha256}`);
}

