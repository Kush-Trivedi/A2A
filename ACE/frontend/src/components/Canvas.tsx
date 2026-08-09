import ReactMarkdown from "react-markdown";
import type { Artifact, ArtifactPart } from "../types";

function DataTable({ data }: { data: Record<string, unknown> }) {
  const rows = Object.entries(data);
  if (!rows.length) return null;
  return (
    <table className="canvas-table">
      <tbody>
        {rows.map(([key, value]) => (
          <tr key={key}>
            <td className="canvas-key">{key}</td>
            <td>{typeof value === "object" ? JSON.stringify(value) : String(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PartView({ part }: { part: ArtifactPart }) {
  if (part.kind === "data" && part.data) return <DataTable data={part.data} />;
  if (part.kind === "text" && part.text) return <ReactMarkdown>{part.text}</ReactMarkdown>;
  if (part.kind === "url" && part.url)
    return (
      <a href={part.url} target="_blank" rel="noreferrer">
        {part.filename || part.url}
      </a>
    );
  return <pre>{JSON.stringify(part, null, 2)}</pre>;
}

export function Canvas({
  artifacts,
  sources,
  onClose,
}: {
  artifacts: Artifact[];
  sources: string[];
  onClose: () => void;
}) {
  return (
    <aside className="canvas">
      <div className="canvas-header">
        <span>Canvas</span>
        <button onClick={onClose} className="canvas-close" title="Close">
          ✕
        </button>
      </div>
      {artifacts.map((artifact) => (
        <section key={artifact.artifact_id} className="canvas-artifact">
          <h3>{artifact.name || "Result"}</h3>
          {artifact.description && <p className="canvas-desc">{artifact.description}</p>}
          {artifact.parts.map((part, index) => (
            <PartView key={index} part={part} />
          ))}
        </section>
      ))}
      {sources.length > 0 && (
        <section className="canvas-sources">
          <h4>Sources</h4>
          <ul>
            {sources.map((source) => (
              <li key={source}>{source}</li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}
