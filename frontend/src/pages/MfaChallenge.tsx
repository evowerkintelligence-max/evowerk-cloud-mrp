import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

interface EnrollResponse {
  secret: string;
  provisioning_uri: string;
}

export function MfaChallenge() {
  const [code, setCode] = useState("");
  const [status, setStatus] = useState<"idle" | "ok" | "error">("idle");

  const enroll = useQuery<EnrollResponse>({
    queryKey: ["mfa-enroll"],
    queryFn: async () => {
      const res = await apiFetch("/auth/mfa/enroll", { method: "POST" });
      if (!res.ok) throw new Error("Failed to start MFA enrollment");
      return res.json();
    },
  });

  async function verify() {
    const res = await apiFetch("/auth/mfa/verify", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    setStatus(res.ok ? "ok" : "error");
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
