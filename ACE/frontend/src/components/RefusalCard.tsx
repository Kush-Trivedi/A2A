import type { Refusal } from "../types";

export function RefusalCard({ refusal }: { refusal: Refusal }) {
  return (
    <div className="refusal-card">
      <div className="refusal-title">Access needed</div>
      <p>{refusal.message}</p>
      {refusal.contact_email && (
        <a href={`mailto:${refusal.contact_email}`} className="refusal-contact">
          Contact {refusal.team_name || "the owning team"}
        </a>
      )}
    </div>
  );
}
