import { Link } from "react-router-dom";
import { BrandMark } from "@/components/BrandMark";

const LAST_UPDATED = "31 July 2026";

export function PrivacyPolicy() {
  return (
    <div className="relative min-h-screen">
      <div className="app-backdrop" />
      <div className="relative mx-auto max-w-3xl px-6 py-16">
        <Link to="/" className="mb-8 flex w-fit items-center gap-2.5">
          <BrandMark className="size-8 text-foreground" title="Evowerk" />
          <span className="text-lg font-bold uppercase tracking-[0.14em] text-foreground">Evowerk</span>
        </Link>

        <h1 className="text-3xl font-semibold tracking-tight">Privacy Policy</h1>
        <p className="mt-1 text-sm text-muted-foreground">Last updated {LAST_UPDATED}</p>

        <div className="prose prose-sm mt-8 max-w-none space-y-6 text-sm leading-relaxed text-muted-foreground [&_h2]:mt-8 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-foreground [&_li]:mt-1 [&_p]:mt-2 [&_ul]:list-disc [&_ul]:pl-5">
          <p>
            Evowerk Cloud MRP ("Evowerk", "we") is manufacturing planning software. This policy
            describes what personal data the software processes and why. Evowerk Cloud MRP is
            deployed on infrastructure your organization controls (your own AWS or Azure account) —
            Evowerk itself does not host your data. If your organization is the one operating a
            deployment, your organization is the data controller and this policy describes how the
            software itself handles data; your organization's own privacy notice to its end users and
            employees governs the rest.
          </p>

          <h2>What we collect</h2>
          <ul>
            <li><strong>Account data:</strong> the email address and password (stored as a salted hash, never in plain text) you register with, plus your role within your organization.</li>
            <li><strong>Business data you enter:</strong> items, BOMs, work orders, purchase/sales orders, and similar records, including any customer or supplier contact details (name, email, phone) you choose to store.</li>
            <li><strong>Shop-floor operator data:</strong> if your organization uses badge/RFID or QR check-in, operator names and check-in/out timestamps are recorded against machines. This identifies your employees, not Evowerk's.</li>
            <li><strong>Content you submit to AI features:</strong> questions typed into "Ask your data", images uploaded to Computer Vision, and report content sent for AI narration.</li>
            <li><strong>Security and audit logs:</strong> login events, IP addresses, and the actions listed above, kept to detect abuse and support account recovery.</li>
          </ul>

          <h2>AI features</h2>
          <p>
            "Ask your data", Computer Vision (defect detection, PPE compliance, object counting), and
            AI report narration are powered by <strong>Google Gemini</strong>. When you use these
            features, your question, uploaded image, or report content is sent to Google's API to
            generate a response. Responses are AI-generated — always labeled as such in the product —
            and should be verified before you rely on them for a decision.
          </p>

          <h2>Who we share data with</h2>
          <ul>
            <li><strong>Google (Gemini API)</strong> — processes content you submit to the AI features above.</li>
            <li><strong>Amazon Web Services (SES)</strong> — sends transactional email (password reset, email verification) on our behalf.</li>
            <li>
              Your own infrastructure provider (AWS or Azure) — since Evowerk Cloud MRP is deployed
              into your organization's own cloud account, your database and file storage are hosted
              there, under your organization's control, not Evowerk's.
            </li>
          </ul>
          <p>We do not sell personal data, and we do not use it for advertising.</p>

          <h2>International transfers</h2>
          <p>
            Google and AWS SES may process data outside the EU/UK (typically in the United States).
            Where that applies, transfers rely on those providers' Standard Contractual Clauses or
            equivalent safeguards.
          </p>

          <h2>How long we keep it</h2>
          <p>
            Account and business data is kept for as long as your account is active. Audit/security
            logs are retained for as long as needed to investigate abuse or support your organization's
            own record-keeping requirements. Deleting your account (below) removes your personal
            login data immediately.
          </p>

          <h2>Your rights</h2>
          <p>
            You can delete your account and personal data at any time from{" "}
            <Link to="/account" className="underline">Account settings</Link> — this removes your
            login email and disables your access immediately. For access, correction, or export
            requests, contact your organization's administrator, or email us at{" "}
            <a href="mailto:privacy@evowerkintelligence.com" className="underline">privacy@evowerkintelligence.com</a>.
          </p>

          <h2>Cookies and tracking</h2>
          <p>
            We don't use analytics, advertising, or tracking cookies. Your session is kept in your
            browser's local session storage, not cookies, and is used only to keep you signed in.
          </p>

          <h2>Security</h2>
          <p>
            Passwords are hashed with Argon2, multi-factor authentication secrets are encrypted at
            rest, and all data in transit is encrypted via TLS.
          </p>

          <h2>Contact</h2>
          <p>
            Evowerk Intelligence (Pvt) Ltd · Bengaluru, India. Questions about this policy:{" "}
            <a href="mailto:privacy@evowerkintelligence.com" className="underline">privacy@evowerkintelligence.com</a>.
          </p>
        </div>
      </div>
    </div>
  );
}
