import { pdfjs } from "react-pdf";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

/**
 * Conta as páginas no cliente para registrar o documento antes de renderizá-lo:
 * a API exige page_count no upload e a biblioteca abre PDFs sem reler o arquivo local.
 */
export async function readPageCount(file: File): Promise<number> {
  const data = new Uint8Array(await file.arrayBuffer());
  const task = pdfjs.getDocument({ data });
  try {
    const pdf = await task.promise;
    return pdf.numPages;
  } finally {
    await task.destroy();
  }
}
