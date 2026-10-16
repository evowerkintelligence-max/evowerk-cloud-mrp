import { useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { AuthLayout } from "@/components/AuthLayout";
import { Alert } from "@/components/ui/alert";

export function VerifyEmail() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [fetched, setStatus] = useState<"pending" | "ok" | "error">("pending");
  const status = token ? fetched : "error";

  useEffect(() => {
    if (!token) return;
    fetch("/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    })
      .then((res) => setStatus(res.ok ? "ok" : "error"))
      .catch(() => setStatus("error"));
  }, [token]);

  return (
    <AuthLayout title="Email verification">
      {status === "pending" && <p className="text-center text-sm text-muted-foreground">Verifying…</p>}
      {status === "ok" && (
        <div className="space-y-4">
          <Alert variant="success">Your email is verified.</Alert>
          <Link to="/login" className="block text-center text-sm font-medium text-primary hover:underline">
            Continue to log in
          </Link>
        </div>
      )}
      {status === "error" && <Alert variant="destructive">This verification link is invalid or expired.</Alert>}
    </AuthLayout>
  );
}
