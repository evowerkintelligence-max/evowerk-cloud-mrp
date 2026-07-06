import { FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router-dom";

export function Register() {
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const res = await fetch("/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ org_name: orgName, email, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setError(body.detail ?? "Registration failed");
      return;
    }
    navigate("/login");
  }

  return (
    <form onSubmit={onSubmit}>
      <h1>Create your organization</h1>
      <label>
        Organization name
        <input value={orgName} onChange={(e) => setOrgName(e.target.value)} required />
      </label>
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
          minLength={12}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      <button type="submit">Register</button>
      <p>
        Already have an account? <Link to="/login">Log in</Link>
      </p>
    </form>
  );
}
