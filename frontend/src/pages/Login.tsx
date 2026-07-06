import { FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuthStore } from "../store/authStore";

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setTokens = useAuthStore((s) => s.setTokens);
  const navigate = useNavigate();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
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
      setError(body.detail ?? "Login failed");
      return;
    }
    const data = await res.json();
    if (data.mfa_required) {
      setMfaRequired(true);
      return;
    }
    setTokens(data.access_token, data.refresh_token);
    navigate("/");
  }

  return (
    <form onSubmit={onSubmit}>
      <h1>Log in</h1>
      <label>
        Email
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      {mfaRequired && (
        <label>
          Authenticator code
          <input value={totpCode} onChange={(e) => setTotpCode(e.target.value)} required />
        </label>
      )}
      {error && <p role="alert">{error}</p>}
      <button type="submit">{mfaRequired ? "Verify" : "Log in"}</button>
      <p>
        Need an account? <Link to="/register">Register</Link>
      </p>
    </form>
  );
}
