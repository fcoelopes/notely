import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ClaimPanel } from "./ClaimPanel";
import { ConceptPanel } from "./ConceptPanel";
import type { ClaimEvidence, ConceptOverview } from "../types";

function claim(overrides: Partial<ClaimEvidence> = {}): ClaimEvidence {
  return {
    claim: {
      id: "claim-1",
      text: "A materialidade é julgada caso a caso",
      normalized_text: "a materialidade é julgada caso a caso",
      provenance: "user_authored",
      status: "accepted",
      provider: null,
      model: null,
      confidence: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      accepted_at: "2026-01-01T00:00:00Z",
    },
    relation: "supports",
    relation_id: "relation-1",
    relation_provenance: "user_authored",
    relation_status: "accepted",
    annotation_id: "annotation-1",
    document_id: "doc-1",
    document_title: "IAS 1 - apresentação",
    page_number: 6,
    quote: "A materialidade depende da natureza ou magnitude das informações",
    comment: null,
    ...overrides,
  };
}

function concept(overrides: Partial<ConceptOverview> = {}): ConceptOverview {
  return {
    id: "concept-1",
    name: "Materialidade",
    annotation_count: 2,
    documents: [
      { document_id: "doc-1", document_title: "IAS 1 - apresentação", annotation_count: 2 },
    ],
    associations: [],
    ...overrides,
  };
}

function renderClaimPanel(props: Partial<Parameters<typeof ClaimPanel>[0]> = {}) {
  const onReload = vi.fn();
  const onRemove = vi.fn();
  render(
    <ClaimPanel
      claims={[]}
      busy={false}
      error={null}
      onReload={onReload}
      onRemove={onRemove}
      {...props}
    />,
  );
  return { onReload, onRemove };
}

function renderConceptPanel(props: Partial<Parameters<typeof ConceptPanel>[0]> = {}) {
  const onReload = vi.fn();
  const onAssociate = vi.fn();
  const onRemoveAssociation = vi.fn();
  render(
    <ConceptPanel
      concepts={[]}
      documentIds={["doc-1"]}
      busy={false}
      error={null}
      onReload={onReload}
      onAssociate={onAssociate}
      onRemoveAssociation={onRemoveAssociation}
      {...props}
    />,
  );
  return { onReload, onAssociate, onRemoveAssociation };
}

afterEach(cleanup);

describe("ClaimPanel", () => {
  it("guides the first use when there is no claim yet", () => {
    renderClaimPanel();
    expect(screen.getByText("Nenhuma afirmação ainda.")).toBeInTheDocument();
    expect(screen.getByText(/use Afirmar para dizer o que ele sustenta ou contradiz/)).toBeInTheDocument();
  });

  it("shows the claim with document, page and excerpt", () => {
    renderClaimPanel({ claims: [claim()] });
    expect(screen.getByText("A materialidade é julgada caso a caso")).toBeInTheDocument();
    expect(screen.getByText("sustenta este trecho")).toBeInTheDocument();
    expect(
      screen.getByText("IAS 1 - apresentação", { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByText(/página 6/)).toBeInTheDocument();
    expect(
      screen.getByText("A materialidade depende da natureza ou magnitude das informações"),
    ).toBeInTheDocument();
  });

  it("keeps a claim that lost its evidence marked instead of hiding it", () => {
    renderClaimPanel({
      claims: [
        claim({
          relation: null,
          relation_id: null,
          relation_provenance: null,
          relation_status: null,
          annotation_id: null,
          document_id: null,
          document_title: null,
          page_number: null,
          quote: "",
        }),
      ],
    });
    expect(screen.getByText("A materialidade é julgada caso a caso")).toBeInTheDocument();
    expect(screen.getByText("sem evidência")).toBeInTheDocument();
    expect(screen.getByText(/continua sendo sua e segue listada aqui/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Remover a afirmação/ })).toBeEnabled();
  });

  it("removes the claim by its text and shows the API failure without hiding the claim", () => {
    const first = renderClaimPanel({ claims: [claim()] });
    fireEvent.click(screen.getByRole("button", { name: /Remover a afirmação A materialidade/ }));
    expect(first.onRemove).toHaveBeenCalledWith("claim-1");

    cleanup();
    const second = renderClaimPanel({ claims: [claim()], error: "Não foi possível remover." });
    expect(screen.getByRole("alert")).toHaveTextContent("Não foi possível remover.");
    expect(screen.getByText("A materialidade é julgada caso a caso")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Atualizar" }));
    expect(second.onReload).toHaveBeenCalled();
  });
});

describe("ConceptPanel", () => {
  it("guides the first use and says the concepts are inference", () => {
    renderConceptPanel();
    expect(
      screen.getByText(/Extraídos por modelo ou heurística de frequência/),
    ).toHaveTextContent("inferência, não conhecimento confirmado");
    expect(screen.getByText("Nenhum conceito ainda.")).toBeInTheDocument();
  });

  it("shows the excerpt count and the documents of the session", () => {
    renderConceptPanel({
      concepts: [
        concept(),
        concept({
          id: "concept-2",
          name: "Relevância",
          annotation_count: 1,
          documents: [
            {
              document_id: "doc-1",
              document_title: "IAS 1 - apresentação",
              annotation_count: 1,
            },
          ],
        }),
      ],
    });
    expect(screen.getByText("Materialidade")).toBeInTheDocument();
    expect(screen.getByText("2 trechos")).toBeInTheDocument();
    expect(screen.getByText("1 trecho")).toBeInTheDocument();
    expect(screen.getAllByText("IAS 1 - apresentação").length).toBeGreaterThan(0);
  });

  it("states that the pair order matters and asks before creating the association", () => {
    const callbacks = renderConceptPanel({
      concepts: [concept(), concept({ id: "concept-2", name: "Relevância" })],
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Selecionar" })[0]);
    expect(screen.queryByRole("button", { name: "Associar conceitos" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Selecionar" }));

    expect(screen.getByText(/ordem do par é significativa hoje/)).toBeInTheDocument();
    expect(screen.getByText(/associar na ordem inversa cria outra associação/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Associar conceitos" }));
    expect(callbacks.onAssociate).toHaveBeenCalledWith("concept-1", "concept-2");
  });

  it("does not offer an association that already exists in that order", () => {
    renderConceptPanel({
      concepts: [
        concept({
          associations: [
            {
              relation_id: "relation-9",
              source_concept_id: "concept-1",
              target_concept_id: "concept-2",
              other_concept_id: "concept-2",
              other_concept_name: "Relevância",
              outgoing: true,
              provenance: "user_authored",
              status: "accepted",
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
        }),
        concept({ id: "concept-2", name: "Relevância" }),
      ],
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Selecionar" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Selecionar" }));
    expect(
      screen.getByText("Estes dois conceitos já estão associados nesta ordem."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Associar conceitos" })).not.toBeInTheDocument();
  });

  it("lists the existing association once and removes it keeping the concepts", () => {
    const callbacks = renderConceptPanel({
      concepts: [
        concept({
          associations: [
            {
              relation_id: "relation-9",
              source_concept_id: "concept-1",
              target_concept_id: "concept-2",
              other_concept_id: "concept-2",
              other_concept_name: "Relevância",
              outgoing: true,
              provenance: "user_authored",
              status: "accepted",
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
        }),
        concept({
          id: "concept-2",
          name: "Relevância",
          associations: [
            {
              relation_id: "relation-9",
              source_concept_id: "concept-1",
              target_concept_id: "concept-2",
              other_concept_id: "concept-1",
              other_concept_name: "Materialidade",
              outgoing: false,
              provenance: "user_authored",
              status: "accepted",
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
        }),
      ],
    });
    expect(screen.getAllByText("Materialidade → Relevância")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Remover associação" }));
    expect(callbacks.onRemoveAssociation).toHaveBeenCalledWith("relation-9");
    // Remover a associação não remove os conceitos.
    expect(screen.getByText("Materialidade")).toBeInTheDocument();
    expect(screen.getByText("Relevância")).toBeInTheDocument();
  });
});
