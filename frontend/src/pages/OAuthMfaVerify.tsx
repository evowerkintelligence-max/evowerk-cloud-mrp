import { FormEvent, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuthStore } from "../store/authStore";
import { AuthLayout } from "@/components/AuthLayout";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { errorMessage } from "@/lib/utils";

export function OAuthMfaVerify() {
  const location = useLocation();
  // Passed via router state (not a URL query param) from OAuthCallback, so
  // this short-lived token never sits in the visible URL/browser history.
  const pendingToken = (location.state as { pendingToken?: string } | null)?.pendingToken ?? "";
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const setTokens = useAuthStore((s) => s.setTokens);
  const navigate = useNavigate();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/auth/oauth/mfa-verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pending_token: pendingToken, code }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(errorMessage(body, "Invalid code. Try again."));
        return;
      }
      const data = await res.json();
      setTokens(data.access_token, data.refresh_token);
      navigate("/");
    } finally {
      setSubmitting(false);
    }
  }

  if (!pendingToken) {
    return (
      <AuthLayout title="Invalid link" subtitle="This sign-in link is missing its token.">
        <p className="text-center text-sm text-muted-foreground">Please sign in again.</p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Two-factor authentication" subtitle="Enter the 6-digit code from your authenticator app.">
      <form onSubmit={onSubmit} className="space-y-4">
        <Field label="Authenticator code" htmlFor="code">
          <Input
            id="code"
            inputMode="numeric"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
          />
        </Field>
        {error && <Alert variant="destructive">{error}</Alert>}
        <Button type="submit" className="w-full" loading={submitting}>
          Verify
        </Button>
      </form>
    </AuthLayout>
  );
}
