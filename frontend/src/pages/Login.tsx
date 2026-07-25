import { FormEvent, useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuthStore } from "../store/authStore";
import { AuthLayout } from "@/components/AuthLayout";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { errorMessage } from "@/lib/utils";

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [providers, setProviders] = useState({ google: false, microsoft: false });
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent">("idle");
  const setTokens = useAuthStore((s) => s.setTokens);
  const navigate = useNavigate();

  useEffect(() => {
    fetch("/auth/providers")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => data && setProviders(data))
      .catch(() => {});
  }, []);

  async function onResendVerification() {
    if (!email) {
      setError("Enter your email above first, then resend the verification link.");
      return;
    }
    setResendState("sending");
    // The endpoint always returns 202 regardless of whether the account
    // exists or is already verified, so there's nothing to branch on -
    // a network failure gets the same "sent" state as a real send.
    try {
      await fetch("/auth/request-verify-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
    } catch {
      // ignored - see above
    }
    setResendState("sent");
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          totp_code: mfaRequired ? totpCode : undefined,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(errorMessage(body, "Login failed"));
        return;
      }
      const data = await res.json();
      if (data.mfa_required) {
        setMfaRequired(true);
        return;
      }
      setTokens(data.access_token, data.refresh_token);
      navigate("/");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Welcome back"
      subtitle="Sign in to your Evowerk workspace"
      footer={
        <>
          Need an account?{" "}
          <Link to="/register" className="font-medium text-primary hover:underline">
            Register
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <Field label="Email" htmlFor="email">
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </Field>
        <Field
          label={
            <div className="flex items-center justify-between">
              <span>Password</span>
              <Link to="/forgot-password" className="text-xs font-medium text-primary hover:underline">
                Forgot password?
              </Link>
            </div>
          }
          htmlFor="password"
        >
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </Field>
        {mfaRequired && (
          <Field label="Authenticator code" htmlFor="totp" hint="Enter the 6-digit code from your app.">
            <Input
              id="totp"
              inputMode="numeric"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              required
            />
          </Field>
        )}
        {error && <Alert variant="destructive">{error}</Alert>}
        <Button type="submit" className="w-full" loading={submitting}>
          {mfaRequired ? "Verify" : "Log in"}
        </Button>
      </form>
      {resendState === "sent" ? (
        <Alert variant="success" className="mt-4">
          Check your email — if that account exists and isn't verified yet, a new verification link is on its way.
        </Alert>
      ) : (
        <button
          type="button"
          onClick={onResendVerification}
          disabled={resendState === "sending"}
          className="mt-4 block w-full text-center text-xs font-medium text-primary hover:underline disabled:opacity-50"
        >
          Resend verification email
        </button>
      )}
      {(providers.google || providers.microsoft) && (
        <div className="mt-6 space-y-2">
          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t border-border" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-background px-2 text-muted-foreground">Or continue with</span>
            </div>
          </div>
          {providers.google && (
            <a href="/auth/google/authorize" className="block">
              <Button type="button" variant="outline" className="w-full">
                Continue with Google
              </Button>
            </a>
          )}
          {providers.microsoft && (
            <a href="/auth/microsoft/authorize" className="block">
              <Button type="button" variant="outline" className="w-full">
                Continue with Microsoft
              </Button>
            </a>
          )}
        </div>
      )}
    </AuthLayout>
  );
}
