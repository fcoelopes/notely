interface Props {
  busy: boolean;
  onSelect: (file: File) => void;
}

export function EmptyReader({ busy, onSelect }: Props) {
  return (
    <main className="empty-reader">
      <section className="empty-copy">
        <p className="eyebrow">Seu espaço de leitura</p>
        <h1>Leia devagar.<br />Guarde o que importa.</h1>
        <p className="empty-description">
          Abra um PDF, selecione trechos e transforme sua leitura em uma memória que você pode reencontrar.
        </p>
        <label className={`file-button ${busy ? "is-busy" : ""}`}>
          <input
            accept="application/pdf,.pdf"
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onSelect(file);
            }}
            type="file"
          />
          <span>{busy ? "Preparando…" : "Abrir um PDF"}</span>
          <span aria-hidden="true" className="file-button-arrow">↗</span>
        </label>
        <small className="privacy-note">O arquivo é verificado antes de ser armazenado com segurança.</small>
      </section>

      <section className="empty-preview" aria-hidden="true">
        <div className="preview-page">
          <span className="preview-kicker">Notas sobre a leitura</span>
          <span className="preview-title" />
          <span className="preview-line preview-line--long" />
          <span className="preview-line" />
          <span className="preview-line preview-line--short" />
          <span className="preview-highlight" />
          <span className="preview-line preview-line--long" />
          <span className="preview-line" />
          <span className="preview-note">uma ideia para voltar depois</span>
        </div>
      </section>
    </main>
  );
}
