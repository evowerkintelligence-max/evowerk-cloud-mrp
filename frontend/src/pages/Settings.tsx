import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Link as LinkIcon } from "lucide-react";
import { apiFetch } from "@/api/client";
import { useAuthStore } from "@/store/authStore";
import { PageHeader } from "@/components/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";

export function Settings() {
  const navigate = useNavigate();
  const clear = useAuthStore((s) => s.clear);

  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiFetch("/auth/delete-account", {
        method: "POST",
        body: JSON.stringify({ password, totp_code: totpCode || undefined }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail ?? "Could not delete your account");
      }
      clear();
      navigate("/login");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Manage your account and how your data is handled."
      />

      <Card>
        <CardHeader>
          <CardTitle>Your data</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <p>
            See what we collect, who we share it with, and how long we keep it in the{" "}
            <a href="/privacy" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium text-primary hover:underline">
              privacy policy <LinkIcon className="size-3.5" />
            </a>
            .
          </p>
        </CardContent>
      </Card>

      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle className="text-destructive">Delete account</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            This permanently deletes your user account, log you out of every device, and cannot be
            undone. Records your organization needs to keep for its own business history (past
            orders, audit logs) are not affected.
          </p>

          {!confirming ? (
            <Button variant="destructive" onClick={() => setConfirming(true)}>
              Delete my account
            </Button>
          ) : (
            <form onSubmit={onSubmit} className="max-w-sm space-y-4">
              <Field label="Confirm your password" htmlFor="delete-password">
                <Input
                  id="delete-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </Field>
              <Field label="Two-factor code (if enabled)" htmlFor="delete-totp">
                <Input
                  id="delete-totp"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                />
              </Field>
              {error && <Alert variant="destructive">{error}</Alert>}
              <div className="flex gap-2">
                <Button type="submit" variant="destructive" loading={submitting}>
                  Permanently delete
                </Button>
                <Button type="button" variant="outline" onClick={() => setConfirming(false)} disabled={submitting}>
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
