import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { AuthLayout } from "@/components/AuthLayout";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(false);
    try {
      await fetch("/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      // Always show the same confirmation regardless of outcome - the
      // endpoint itself never reveals whether the email is registered
      // (see backend/tests/test_password_reset.py), so the UI shouldn't either.
      setSubmitted(true);
    } catch {
      // A real network failure (offline, DNS, backend down) is not the same
      // as "the backend responded but won't say if the email exists" - this
      // doesn't leak account existence, so it's fine to surface it distinctly.
      setError(true);
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <AuthLayout title="Check your email" subtitle="If an account exists for that address, a reset link is on its way.">
        <Link to="/login" className="text-sm font-medium text-primary hover:underline">
          Back to log in
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Reset your password"
      subtitle="Enter your email and we'll send you a reset link"
      footer={
        <Link to="/login" className="font-medium text-primary hover:underline">
          Back to log in
        </Link>
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
        {error && <Alert variant="destructive">Something went wrong. Please try again.</Alert>}
        <Button type="submit" className="w-full" loading={submitting}>
          Send reset link
        </Button>
      </form>
    </AuthLayout>
  );
}
