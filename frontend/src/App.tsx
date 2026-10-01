import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { AnnotationOverlay } from "./components/AnnotationOverlay";
import { AnnotationPanel } from "./components/AnnotationPanel";
import { EmptyReader } from "./components/EmptyReader";
import { SelectionToolbar } from "./components/SelectionToolbar";
import { ApiError, createAnnotation, listAnnotations, uploadDocument } from "./lib/api";
import { normalizeRects } from "./lib/geometry";
import type { Annotation, AnnotationType, SelectionDraft } from "./types";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface OpenFile {
  file: File;
  url: string;
}

function App() {
  const [openFile, setOpenFile] = useState<OpenFile | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1.1);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [selection, setSelection] = useState<SelectionDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pageRef = useRef<HTMLDivElement>(null);

  useEffect(() => () => {
    if (openFile) URL.revokeObjectURL(openFile.url);
  }, [openFile]);

  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelection(null);
    };
    window.addEventListener("keydown", dismiss);
    return () => window.removeEventListener("keydown", dismiss);
  }, []);

  const currentAnnotations = useMemo(
    () => annotations.filter((annotation) => annotation.page_number === pageNumber),
    [annotations, pageNumber],
  );

  const handleFile = useCallback((file: File) => {
    setBusy(true);
    setError(null);
    setAnnotations([]);
    setDocumentId(null);
    setPageNumber(1);
    setNumPages(0);
    setOpenFile((current) => {
      if (current) URL.revokeObjectURL(current.url);
      return { file, url: URL.createObjectURL(file) };
    });
  }, []);

  const handleDocumentLoad = useCallback(async (pdf: PDFDocumentProxy) => {
    if (!openFile) return;
    setNumPages(pdf.numPages);
    setBusy(true);
    setError(null);
    try {
      const record = await uploadDocument(openFile.file, pdf.numPages);
      const saved = await listAnnotations(record.id);
      setDocumentId(record.id);
      setAnnotations(saved);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.message);
      } else {
        setError("A API do Notely não está disponível. Confirme se os serviços estão em execução.");
      }
    } finally {
      setBusy(false);
    }
  }, [openFile]);

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
  }, [pageNumber]);

  const saveSelection = useCallback(async (type: AnnotationType) => {
    if (!selection || !documentId) return;
    let comment: string | null = null;
    if (["note", "question", "disagreement"].includes(type)) {
      const promptLabel = type === "question" ? "Qual é a sua dúvida?" : "Adicione seu comentário:";
      comment = window.prompt(promptLabel)?.trim() || null;
      if (type === "question" && !comment) return;
    }

    setSaving(true);
    setError(null);
    try {
      const saved = await createAnnotation({
        document_id: documentId,
        page_number: selection.pageNumber,
        type,
        quote: selection.quote,
        comment,
        position: {
          version: 1,
          rects: selection.rects,
          textQuoteSelector: { exact: selection.quote },
        },
      });
      setAnnotations((current) => [...current, saved]);
      setSelection(null);
      window.getSelection()?.removeAllRanges();
    } catch {
      setError("Não foi possível salvar a anotação. Sua seleção foi mantida para tentar novamente.");
    } finally {
      setSaving(false);
    }
  }, [documentId, selection]);

  if (!openFile) {
    return (
      <div className="app-shell">
        <Header />
        {error && <StatusBanner message={error} />}
        <EmptyReader busy={busy} onSelect={handleFile} />
      </div>
    );
  }

  return (
    <div className="app-shell app-shell--reading">
      <Header onClose={() => setOpenFile(null)} />
      {error && <StatusBanner message={error} />}
      <div className="reader-layout">
        <section className="reader-column">
          <div className="reader-toolbar">
            <div className="document-meta">
              <span className="document-dot" />
              <div>
                <strong>{openFile.file.name.replace(/\.pdf$/i, "")}</strong>
                <small>{busy ? "Verificando e armazenando…" : documentId ? "Verificado e salvo" : "Não persistido"}</small>
              </div>
            </div>
            <div className="page-controls" aria-label="Navegação do documento">
              <button disabled={pageNumber <= 1} onClick={() => setPageNumber((page) => page - 1)}>←</button>
              <span><b>{pageNumber}</b> / {numPages || "–"}</span>
              <button disabled={pageNumber >= numPages} onClick={() => setPageNumber((page) => page + 1)}>→</button>
            </div>
            <div className="zoom-controls" aria-label="Zoom">
              <button disabled={scale <= 0.7} onClick={() => setScale((value) => value - 0.1)}>−</button>
              <span>{Math.round(scale * 100)}%</span>
              <button disabled={scale >= 1.8} onClick={() => setScale((value) => value + 0.1)}>+</button>
            </div>
          </div>

          <div className="document-stage" onMouseUp={captureSelection}>
            <Document
              file={openFile.url}
              loading={<div className="document-loading">Preparando as páginas…</div>}
              onLoadError={() => { setBusy(false); setError("O PDF não pôde ser interpretado."); }}
              onLoadSuccess={handleDocumentLoad}
            >
              <div className="page-shell" ref={pageRef}>
                <Page pageNumber={pageNumber} renderAnnotationLayer renderTextLayer scale={scale} />
                <AnnotationOverlay annotations={currentAnnotations} />
                {selection && documentId && (
                  <SelectionToolbar draft={selection} onChoose={saveSelection} saving={saving} />
                )}
              </div>
            </Document>
          </div>
        </section>

        <AnnotationPanel annotations={annotations} onGoToPage={setPageNumber} pageNumber={pageNumber} />
      </div>
    </div>
  );
}

function Header({ onClose }: { onClose?: () => void }) {
  return (
    <header className="site-header">
      <a className="brand" href="/" onClick={(event) => { if (onClose) { event.preventDefault(); onClose(); } }}>
        <span className="brand-mark">N</span>
        <span>notely</span>
      </a>
      <p>Leia. Anote. Conecte.</p>
      {onClose && <button className="close-reader" onClick={onClose}>Fechar documento</button>}
    </header>
  );
}

function StatusBanner({ message }: { message: string }) {
  return <div className="status-banner" role="alert">{message}</div>;
}

export { App };
