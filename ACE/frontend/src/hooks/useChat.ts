import { useCallback, useMemo, useRef, useState } from "react";
import { apiClient } from "../api/AceApiClient";
import { ChatStreamController } from "../api/ChatStreamController";
import type { Artifact, Refusal, StreamEvent } from "../types";

export interface TurnView {
  id: string;
  role: "user" | "assistant";
  content: string;
  agentId?: string;
  refusal?: Refusal;
  streaming?: boolean;
}

export function useChat() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<TurnView[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const controller = useMemo(() => new ChatStreamController(apiClient), []);
  const counter = useRef(0);

  const nextId = () => `turn-${++counter.current}`;

  const loadSession = useCallback(async (id: string) => {
    const result = await apiClient.messages(id);
    setSessionId(id);
    setArtifacts([]);
    setSources([]);
    setCanvasOpen(false);
    setTurns(
      result.messages.map((message) => ({
        id: message.id,
        role: message.role === "user" ? "user" : "assistant",
        content: message.content,
        agentId: (message.metadata?.agent_id as string) || undefined,
        refusal: (message.metadata?.refusal as Refusal) || undefined,
      })),
    );
  }, []);

  const newSession = useCallback(() => {
    setSessionId(null);
    setTurns([]);
    setArtifacts([]);
    setSources([]);
    setCanvasOpen(false);
  }, []);

  const send = useCallback(
    async (message: string, agent: string | null) => {
      if (!message.trim() || busy) return;
      setBusy(true);
      const userId = nextId();
      const assistantId = nextId();
      setTurns((prev) => [
        ...prev,
        { id: userId, role: "user", content: message },
        { id: assistantId, role: "assistant", content: "", streaming: true },
      ]);

      const patchAssistant = (patch: Partial<TurnView>) =>
        setTurns((prev) =>
          prev.map((turn) => (turn.id === assistantId ? { ...turn, ...patch } : turn)),
        );

      const handleEvent = (event: StreamEvent) => {
        if (event.event === "meta") {
          setSessionId(event.data.session_id);
          setSources(event.data.sources);
          patchAssistant({ agentId: event.data.agent_id });
        } else if (event.event === "token") {
          setTurns((prev) =>
            prev.map((turn) =>
              turn.id === assistantId
                ? { ...turn, content: turn.content + event.data.text }
                : turn,
            ),
          );
        } else if (event.event === "artifact") {
          setArtifacts((prev) => [...prev, event.data]);
          setCanvasOpen(true);
        } else if (event.event === "refusal") {
          patchAssistant({ refusal: event.data, content: event.data.message });
        } else if (event.event === "error") {
          patchAssistant({ content: `Something went wrong: ${event.data.message}` });
        }
      };

      try {
        await controller.stream(
          { message, agent, session_id: sessionId },
          handleEvent,
        );
      } catch (error) {
        patchAssistant({ content: `Request failed: ${String(error)}` });
      } finally {
        patchAssistant({ streaming: false });
        setBusy(false);
      }
    },
    [busy, controller, sessionId],
  );

  return {
    sessionId,
    turns,
    artifacts,
    sources,
    canvasOpen,
    setCanvasOpen,
    busy,
    send,
    loadSession,
    newSession,
  };
}
