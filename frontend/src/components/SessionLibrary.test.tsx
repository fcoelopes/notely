import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionLibrary } from "./SessionLibrary";
import type { DocumentSummary, StudySession } from "../types";

const sessions = [{ id: "s1", theme: "Recuperação de informação", theme_origin: "user" }] as StudySession[];
const documents = [{ id: "d1", title: "Introdução", filename: "artigo.pdf", page_count: 3 }] as DocumentSummary[];
function setup(
  busy = false,
  progress = null as import("../types").ReadingProgressSnapshot | null,
  data = { sessions, documents },
) {
  const onResumeSession = vi.fn();
  const onStartSession = vi.fn();
  render(<SessionLibrary documents={data.documents} sessions={data.sessions} busy={busy} progress={progress} onResumeSession={onResumeSession} onStartSession={onStartSession} />);
  return { onResumeSession, onStartSession };
}
afterEach(cleanup);
describe("SessionLibrary", () => {
  it("shows persisted session and global document percentages with page counts", () => {
    setup(false, {
      sessions: { s1: { progress: { viewed_pages: 2, total_pages: 3, percent: 67 }, documents: { d1: { viewed_pages: 2, total_pages: 3, percent: 67 } } } },
      documents: { d1: { viewed_pages: 2, total_pages: 3, percent: 67 } },
    });
    expect(screen.getAllByText("67% · 2/3 páginas visualizadas")).toHaveLength(2);
  });
  it("filters by theme without accents and reports no results", () => {
    setup();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "recuperacao" } });
    expect(screen.getByText("Recuperação de informação")).toBeInTheDocument();
    expect(screen.getByText("Nenhum documento encontrado.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "inexistente" } });
    expect(screen.getByText("Nenhuma sessão encontrada.")).toBeInTheDocument();
  });
  it("switches collections and searches by filename", () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "Documentos 1" }));
    expect(screen.queryByText("Sessões abertas")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "artigo.pdf" } });
    expect(screen.getByText("Introdução")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Documentos 1" })).toHaveAttribute("aria-current", "page");
  });
  it("resumes an existing session and trims a new theme", () => {
    const callbacks = setup();
    fireEvent.click(screen.getByRole("button", { name: /Recuperação de informação/ }));
    expect(callbacks.onResumeSession).toHaveBeenCalledWith("s1");
    fireEvent.change(screen.getByLabelText("Tema da sessão (opcional)"), { target: { value: "  Leitura  " } });
    fireEvent.click(screen.getByRole("button", { name: "Começar sessão" }));
    expect(callbacks.onStartSession).toHaveBeenCalledWith("Leitura");
  });
  it("keeps the origin of an accepted suggested theme visible", () => {
    setup(false, null, {
      sessions: [{ ...sessions[0], theme_origin: "ai_suggestion" }], documents,
    });
    expect(screen.getByText("tema aceito de uma sugestão")).toBeInTheDocument();
  });

  it("keeps the overview short and paginates the complete session collection", () => {
    const manySessions = Array.from({ length: 12 }, (_, index) => ({
      ...sessions[0], id: `session-${index}`, theme: `Sessão ${index}`,
      created_at: `2026-01-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
      updated_at: `2026-01-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
    }));
    setup(false, null, { sessions: manySessions, documents });
    expect(screen.getByText("Sessão 11")).toBeInTheDocument();
    expect(screen.queryByText("Sessão 8")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Ver todas as sessões/ }));
    expect(screen.getByText("Sessão 4")).toBeInTheDocument();
    expect(screen.queryByText("Sessão 3")).not.toBeInTheDocument();
    expect(screen.getByText("Página 1 de 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Próxima página de sessões" }));
    expect(screen.getByText("Sessão 3")).toBeInTheDocument();
    expect(screen.queryByText("Sessão 11")).not.toBeInTheDocument();
    expect(screen.getByText("Página 2 de 2")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Sessão 11" } });
    expect(screen.getByText("Sessão 11")).toBeInTheDocument();
    expect(screen.queryByText("Página 2 de 2")).not.toBeInTheDocument();
  });

  it("paginates documents and can order the collection by title", () => {
    const manyDocuments = Array.from({ length: 12 }, (_, index) => ({
      ...documents[0], id: `document-${index}`, title: `Arquivo ${String(index).padStart(2, "0")}`,
      filename: `arquivo-${index}.pdf`,
      created_at: `2026-01-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
    }));
    setup(false, null, { sessions, documents: manyDocuments });
    expect(screen.queryByText("Arquivo 08")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Ver todos os documentos/ }));
    expect(screen.getByText("Arquivo 04")).toBeInTheDocument();
    expect(screen.queryByText("Arquivo 03")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Ordenar biblioteca"), { target: { value: "title" } });
    expect(screen.getByText("Arquivo 00")).toBeInTheDocument();
    expect(screen.queryByText("Arquivo 08")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Próxima página de documentos" }));
    expect(screen.getByText("Arquivo 08")).toBeInTheDocument();
  });

  it("finds an unnamed session by the title of a linked PDF", () => {
    const unnamed = [{ ...sessions[0], theme: null, theme_origin: null }];
    setup(false, {
      sessions: { s1: { progress: { viewed_pages: 0, total_pages: 3, percent: 0 }, documents: { d1: { viewed_pages: 0, total_pages: 3, percent: 0 } } } },
      documents: { d1: { viewed_pages: 0, total_pages: 3, percent: 0 } },
    }, { sessions: unnamed, documents });
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "introducao" } });
    expect(screen.getByText("Sem tema definido")).toBeInTheDocument();
    expect(screen.getByText(/1 PDF · Introdução/)).toBeInTheDocument();
  });

  it("prevents duplicate session operations while busy", () => {
    const callbacks = setup(true);
    expect(screen.getByRole("button", { name: /Recuperação de informação/ })).toBeDisabled();
    fireEvent.submit(screen.getByLabelText("Tema da sessão (opcional)").closest("form")!);
    expect(callbacks.onStartSession).not.toHaveBeenCalled();
  });
});
