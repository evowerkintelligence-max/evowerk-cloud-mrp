import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "../store/authStore";
import { AuthLayout } from "@/components/AuthLayout";

export function OAuthCallback() {
  const setTokens = useAuthStore((s) => s.setTokens);
  const navigate = useNavigate();

  useEffect(() => {
    // Tokens arrive in the URL fragment (not the query string) so they never
    // hit server access logs or a Referer header - see
    // backend/app/api/routes/oauth.py's _redirect_with_tokens.
    const params = new URLSearchParams(window.location.hash.slice(1));
    const accessToken = params.get("access_token");
    const refreshToken = params.get("refresh_token");
    const mfaPendingToken = params.get("mfa_pending_token");
    if (accessToken && refreshToken) {
      setTokens(accessToken, refreshToken);
      navigate("/", { replace: true });
    } else if (mfaPendingToken) {
      navigate(`/oauth/mfa-verify?pending_token=${encodeURIComponent(mfaPendingToken)}`, { replace: true });
    } else {
      navigate("/login", { replace: true });
    }
  }, [navigate, setTokens]);

  return (
    <AuthLayout title="Signing you in…">
      <p className="text-center text-sm text-muted-foreground">One moment…</p>
    </AuthLayout>
  );
}
