import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

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
      <div>
        <h1>Confirm your password</h1>
        <p>MFA is already enabled on this account. Re-enter your password to replace it.</p>
        <label>
          Password
          <input
            type="password"
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && enroll.refetch()}
          />
        </label>
        <button onClick={() => enroll.refetch()}>Continue</button>
      </div>
    );
  }

  if (enroll.isLoading) return <p>Loading...</p>;
  if (enroll.isError || !enroll.data) return <p>Could not start MFA enrollment.</p>;

  return (
    <div>
      <h1>Enable two-factor authentication</h1>
      <p>Scan this in your authenticator app, or enter the secret manually:</p>
      <code>{enroll.data.secret}</code>
      <p>
        <a href={enroll.data.provisioning_uri}>{enroll.data.provisioning_uri}</a>
      </p>
      <label>
        Enter the 6-digit code to confirm
        <input value={code} onChange={(e) => setCode(e.target.value)} />
      </label>
      <button onClick={verify}>Confirm</button>
      {status === "ok" && <p>MFA enabled.</p>}
      {status === "error" && <p role="alert">Invalid code, try again.</p>}
    </div>
  );
}
