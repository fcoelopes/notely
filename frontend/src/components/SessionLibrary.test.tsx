import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionLibrary } from "./SessionLibrary";
import type { DocumentSummary, StudySession } from "../types";

const sessions = [{ id: "s1", theme: "Recuperação de informação", theme_origin: "user" }] as StudySession[];
const documents = [{ id: "d1", title: "Introdução", filename: "artigo.pdf", page_count: 3 }] as DocumentSummary[];
function setup(busy = false) {
  const onResumeSession = vi.fn();
  const onStartSession = vi.fn();
  render(<SessionLibrary documents={documents} sessions={sessions} busy={busy} onResumeSession={onResumeSession} onStartSession={onStartSession} />);
  return { onResumeSession, onStartSession };
}
afterEach(cleanup);
describe("SessionLibrary", () => {
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
  it("prevents duplicate session operations while busy", () => {
    const callbacks = setup(true);
    expect(screen.getByRole("button", { name: /Recuperação de informação/ })).toBeDisabled();
    fireEvent.submit(screen.getByLabelText("Tema da sessão (opcional)").closest("form")!);
    expect(callbacks.onStartSession).not.toHaveBeenCalled();
  });
});
