// Compatibility shim: the onboarding container was folded into the dashboard,
// which now resolves the user's team itself via GET /admin/my-team.
export { default as TeamAgentOnboarding, default } from "./CollaborativeMultiTeamDashboard";
