import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch } from "@/api/client";
import { PageHeader } from "@/components/PageHeader";
import { Field } from "@/components/Field";
import { useAuthStore } from "@/store/authStore";
import { useCurrentUser } from "@/lib/currentUser";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { errorMessage } from "@/lib/utils";

export function Account() {
  const user = useCurrentUser();
  const clear = useAuthStore((s) => s.clear);
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onDelete(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await apiFetch("/auth/me", { method: "DELETE", body: JSON.stringify({ password }) });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(errorMessage(body, "Could not delete account"));
        return;
      }
      clear();
      navigate("/login");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <PageHeader title="Account" description="Manage your account and data." />

      <Card>
        <CardHeader>
          <CardTitle>Account details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          <div className="text-muted-foreground">Signed in as</div>
          <div className="font-medium text-foreground">{user.data?.name ?? "—"}</div>
        </CardContent>
      </Card>

      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle className="text-destructive">Delete account</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            This permanently removes your login and personal data (email) and signs you out
            everywhere. Business records you created stay with your organization for continuity —
            see our <Link to="/privacy" className="underline">Privacy Policy</Link> for details. This
            cannot be undone.
          </p>
          {!confirming ? (
            <Button type="button" variant="destructive" onClick={() => setConfirming(true)}>
              Delete my account
            </Button>
          ) : (
            <form onSubmit={onDelete} className="space-y-4">
              <Field label="Confirm your password" htmlFor="delete-password">
                <Input
                  id="delete-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoFocus
                />
              </Field>
              {error && <Alert variant="destructive">{error}</Alert>}
              <div className="flex gap-2">
                <Button type="submit" variant="destructive" loading={submitting}>
                  Permanently delete account
                </Button>
                <Button type="button" variant="outline" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
