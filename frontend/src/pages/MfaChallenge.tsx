import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { AuthLayout } from "@/components/AuthLayout";
import { Field } from "@/components/Field";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";

interface EnrollResponse {
  secret: string;
  provisioning_uri: string;
}

export function MfaChallenge() {
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [needsPassword, setNeedsPassword] = useState(false);
  const [status, setStatus] = useState<"idle" | "ok" | "error">("idle");

  const enroll = useQuery<EnrollResponse>({
    queryKey: ["mfa-enroll", needsPassword ? password : null],
    queryFn: async () => {
      const res = await apiFetch("/auth/mfa/enroll", {
        method: "POST",
        body: JSON.stringify(needsPassword ? { password } : {}),
      });
      if (res.status === 403) {
        setNeedsPassword(true);
        throw new Error("Password confirmation required");
      }
      if (!res.ok) throw new Error("Failed to start MFA enrollment");
      return res.json();
    },
    enabled: !needsPassword || password.length > 0,
    retry: false,
  });

  async function verify() {
    const res = await apiFetch("/auth/mfa/verify", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    setStatus(res.ok ? "ok" : "error");
  }

  if (needsPassword && !password) {
    return (
      <AuthLayout
        title="Confirm your password"
        subtitle="MFA is already enabled — re-enter your password to replace it."
      >
        <div className="space-y-4">
          <Field label="Password" htmlFor="mfa-pw">
            <Input
              id="mfa-pw"
              type="password"
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && enroll.refetch()}
            />
          </Field>
          <Button className="w-full" onClick={() => enroll.refetch()}>
            Continue
          </Button>
        </div>
      </AuthLayout>
    );
  }

  if (enroll.isLoading) {
    return (
      <AuthLayout title="Two-factor authentication" subtitle="Starting enrollment…">
        <p className="text-center text-sm text-muted-foreground">Loading…</p>
      </AuthLayout>
    );
  }

  if (enroll.isError || !enroll.data) {
    return (
      <AuthLayout title="Two-factor authentication">
        <Alert variant="destructive">Could not start MFA enrollment.</Alert>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Enable two-factor authentication"
      subtitle="Scan the secret in your authenticator app, then confirm with a code."
    >
      <div className="space-y-4">
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">Secret key</p>
          <code className="block break-all rounded-md border border-border bg-background/60 px-3 py-2 font-mono text-sm text-primary">
            {enroll.data.secret}
          </code>
          <a
            href={enroll.data.provisioning_uri}
            className="block break-all text-xs text-muted-foreground hover:text-primary"
          >
            {enroll.data.provisioning_uri}
          </a>
        </div>
        <Field label="6-digit code" htmlFor="mfa-code">
          <Input
            id="mfa-code"
            inputMode="numeric"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </Field>
        <Button className="w-full" onClick={verify}>
          Confirm
        </Button>
        {status === "ok" && <Alert variant="success">MFA enabled.</Alert>}
        {status === "error" && <Alert variant="destructive">Invalid code, try again.</Alert>}
      </div>
    </AuthLayout>
  );
}
