import { FormEvent, useState } from "react";
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
  const setTokens = useAuthStore((s) => s.setTokens);
  const navigate = useNavigate();

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
      <div className="mt-6 space-y-2">
        <div className="relative">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t border-border" />
          </div>
          <div className="relative flex justify-center text-xs uppercase">
            <span className="bg-background px-2 text-muted-foreground">Or continue with</span>
          </div>
        </div>
        <a href="/auth/google/authorize" className="block">
          <Button type="button" variant="outline" className="w-full">
            Continue with Google
          </Button>
        </a>
        <a href="/auth/microsoft/authorize" className="block">
          <Button type="button" variant="outline" className="w-full">
            Continue with Microsoft
          </Button>
        </a>
      </div>
    </AuthLayout>
  );
}
