import { FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { AuthLayout } from "@/components/AuthLayout";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { errorMessage } from "@/lib/utils";

// pydantic 422 responses send `detail` as an array of validation error
// objects instead of a string; join their messages so Alert never receives
// a non-string child.
function extractErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => d?.msg).filter(Boolean);
    if (msgs.length) return msgs.join(" ");
  }
  return fallback;
}

export function Register() {
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_name: orgName, email, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(errorMessage(body, "Registration failed"));
        return;
      }
      navigate("/login");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Create your organization"
      subtitle="Spin up a new Evowerk MRP workspace"
      footer={
        <>
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-primary hover:underline">
            Log in
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <Field label="Organization name" htmlFor="org">
          <Input id="org" value={orgName} onChange={(e) => setOrgName(e.target.value)} required />
        </Field>
        <Field label="Email" htmlFor="email">
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </Field>
        <Field label="Password" htmlFor="password" hint="At least 12 characters.">
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={12}
          />
        </Field>
        {error && <Alert variant="destructive">{error}</Alert>}
        <Button type="submit" className="w-full" loading={submitting}>
          Register
        </Button>
      </form>
    </AuthLayout>
  );
}
