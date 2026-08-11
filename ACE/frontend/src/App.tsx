import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { apiClient, UnauthenticatedError } from "./api/AceApiClient";
import { Canvas } from "./components/Canvas";
import { DataOnboarding } from "./components/DataOnboarding";
import { RefusalCard } from "./components/RefusalCard";
import { useChat } from "./hooks/useChat";
import type { AgentSummary, SessionSummary, UserProfile } from "./types";

function SignIn() {
  return (
    <main className="signin">
      <h1>ACE Assistant</h1>
      <p>Sign in with your organization account to continue.</p>
      <a className="signin-button" href={apiClient.loginUrl()}>
        Sign in with Microsoft
      </a>
    </main>
  );
}

export default function App() {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const chat = useChat();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (async () => {
      try {
        const me = await apiClient.me();
        setUser(me.user);
        const [agentList, sessionList] = await Promise.all([
          apiClient.agents(),
          apiClient.sessions(),
        ]);
        setAgents(agentList);
        setSessions(sessionList);
      } catch (error) {
        if (!(error instanceof UnauthenticatedError)) console.error(error);
      } finally {
        setAuthChecked(true);
      }
    })();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.turns]);

  if (!authChecked) return <main className="signin">Loading…</main>;
  if (!user) return <SignIn />;

  const submit = async () => {
    const message = draft.trim();
    if (!message) return;
    setDraft("");
    await chat.send(message, agentId);
    setSessions(await apiClient.sessions());
  };

  return (
    <div className="layout">
      <aside className="sessions">
        <div className="sessions-header">
          <span>{user.display_name || user.email}</span>
          <button onClick={() => chat.newSession()}>+ New chat</button>
        </div>
        <ul>
          {sessions.map((session) => (
            <li key={session.id}>
              <button
                className={session.id === chat.sessionId ? "active" : ""}
                onClick={() => chat.loadSession(session.id)}
              >
                {session.title || "Untitled"}
              </button>
            </li>
          ))}
        </ul>
        <div className="agent-picker">
          <label>Assistant</label>
          <select
            value={agentId ?? ""}
            onChange={(event) => setAgentId(event.target.value || null)}
          >
            <option value="">Auto (routed by question)</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id} title={agent.description}>
                {agent.display_name}
              </option>
            ))}
          </select>
          <button
            style={{ marginTop: "0.5rem" }}
            onClick={() => setOnboardingOpen(true)}
          >
            Data Onboarding
          </button>
        </div>
      </aside>

      <main className="chat">
        <div className="thread">
          {chat.turns.length === 0 && (
            <div className="empty-state">
              Ask a question — you will be routed to the assistants your role can
              access.
            </div>
          )}
          {chat.turns.map((turn) => (
            <div key={turn.id} className={`bubble ${turn.role}`}>
              {turn.agentId && <span className="agent-chip">{turn.agentId}</span>}
              {turn.refusal ? (
                <RefusalCard refusal={turn.refusal} />
              ) : turn.disambiguation ? (
                <div>
                  <p>{turn.disambiguation.message}</p>
                  <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                    {turn.disambiguation.candidates.map((candidate) => (
                      <button
                        key={candidate.agent_key}
                        disabled={chat.busy}
                        onClick={() =>
                          void chat.send(
                            turn.disambiguation?.question ?? "",
                            candidate.agent_key,
                          )
                        }
                      >
                        {candidate.display_name}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <ReactMarkdown>{turn.content || (turn.streaming ? "…" : "")}</ReactMarkdown>
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
        <div className="composer">
          <label
            className="upload-button"
            title={
              chat.sessionId
                ? "Upload a file (docx, pptx, xlsx, pdf…) to ask about it"
                : "Send a message first to start a session, then upload"
            }
          >
            📎
            <input
              type="file"
              hidden
              disabled={!chat.sessionId}
              onChange={async (event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (!file || !chat.sessionId) return;
                try {
                  const result = await apiClient.uploadFile(chat.sessionId, file);
                  window.alert(
                    `Uploaded ${file.name} (${result.chunk_count} sections indexed). ` +
                      "Ask the File Q&A Agent about it.",
                  );
                } catch (error) {
                  window.alert(`Upload failed: ${String(error)}`);
                }
              }}
            />
          </label>
          <textarea
            value={draft}
            placeholder="Ask anything…"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          <button disabled={chat.busy || !draft.trim()} onClick={() => void submit()}>
            Send
          </button>
        </div>
      </main>

      {chat.canvasOpen && (
        <Canvas
          artifacts={chat.artifacts}
          sources={chat.sources}
          onClose={() => chat.setCanvasOpen(false)}
        />
      )}
      {onboardingOpen && <DataOnboarding onClose={() => setOnboardingOpen(false)} />}
    </div>
  );
}
