import { useState } from "react";
import { apiClient } from "../api/AceApiClient";

const CONNECTION_EXAMPLE = `{
  "team_key": "clinical_care",
  "name": "clinical_sharepoint",
  "connection_type": "sharepoint",
  "description": "Clinical Care policy site",
  "config": {"tenant_id": "…", "client_id": "…", "hostname": "contoso.sharepoint.com"},
  "secrets": {"client_secret": "…"}
}`;

const INGEST_EXAMPLE = `{
  "source_name": "policies",
  "team_key": "clinical_care",
  "connection": "clinical_sharepoint",
  "location": {"site_path": "/sites/policies", "drive_name": "Documents", "folder_path": ""},
  "chunking": {"strategy": "hierarchical", "max_tokens": 512, "overlap": 64},
  "embedding": {"deployment": "", "vectors": "both"},
  "access": {"agents": ["policy_procedure"], "roles": ["nurse", "developer"]}
}`;

/** Data Onboarding — the team story in one panel:
 *  ① register your connection → ② ingest into your source → ③ your agent
 *  answers from it. Same API the kit CLI uses. */
export function DataOnboarding({ onClose }: { onClose: () => void }) {
  const [connectionJson, setConnectionJson] = useState(CONNECTION_EXAMPLE);
  const [ingestJson, setIngestJson] = useState(INGEST_EXAMPLE);
  const [log, setLog] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const append = (line: string) => setLog((prev) => [...prev, line]);

  const registerConnection = async () => {
    setBusy(true);
    try {
      const payload = JSON.parse(connectionJson) as Record<string, unknown>;
      const result = await apiClient.registerConnection(payload);
      append(`✔ Connection registered: ${String(result.name)} (${String(result.connection_type)})`);
    } catch (error) {
      append(`✖ Connection failed: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const ingest = async () => {
    setBusy(true);
    try {
      const payload = JSON.parse(ingestJson) as Record<string, unknown>;
      const accepted = await apiClient.ingestSource(payload);
      append(`✔ Ingestion started: job ${accepted.job_id}`);
      for (let attempt = 0; attempt < 60; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        const job = await apiClient.ingestJob(accepted.job_id);
        if (job.status === "completed" || job.status === "failed") {
          append(`Job ${accepted.job_id}: ${job.status} — ${JSON.stringify(job.detail)}`);
          return;
        }
        append(`Job ${accepted.job_id}: ${job.status}…`);
      }
      append(`Job ${accepted.job_id}: still running — check later.`);
    } catch (error) {
      append(`✖ Ingestion failed: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const listSources = async () => {
    try {
      const sources = await apiClient.knowledgeSources();
      if (sources.length === 0) {
        append("No knowledge sources registered yet.");
        return;
      }
      for (const source of sources) {
        append(
          `• ${String(source.source_name)} (team ${String(source.owner_team_key)}) → ` +
            `agents [${(source.agents as string[]).join(", ")}], roles [${(
              source.roles as string[]
            ).join(", ")}]`,
        );
      }
    } catch (error) {
      append(`✖ Listing failed: ${String(error)}`);
    }
  };

  return (
    <div className="canvas" style={{ overflowY: "auto" }}>
      <div className="canvas-header">
        <h2>Data Onboarding</h2>
        <button onClick={onClose}>✕</button>
      </div>
      <p style={{ padding: "0 1rem" }}>
        ① Register your team's connection → ② ingest into your source (choose
        chunking, embedding, and which agents + roles may read it) → ③ your
        agent answers from it. Secrets are encrypted at rest and never shown
        back.
      </p>
      <div style={{ padding: "0 1rem" }}>
        <h3>1 — Connection</h3>
        <textarea
          rows={8}
          style={{ width: "100%", fontFamily: "monospace" }}
          value={connectionJson}
          onChange={(event) => setConnectionJson(event.target.value)}
        />
        <button disabled={busy} onClick={() => void registerConnection()}>
          Register connection
        </button>
        <h3>2 — Ingest</h3>
        <textarea
          rows={9}
          style={{ width: "100%", fontFamily: "monospace" }}
          value={ingestJson}
          onChange={(event) => setIngestJson(event.target.value)}
        />
        <button disabled={busy} onClick={() => void ingest()}>
          Start ingestion
        </button>{" "}
        <button disabled={busy} onClick={() => void listSources()}>
          List sources
        </button>
        <h3>Log</h3>
        <pre style={{ whiteSpace: "pre-wrap" }}>{log.join("\n") || "—"}</pre>
      </div>
    </div>
  );
}
