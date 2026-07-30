import { Link } from "react-router-dom";
import { BrandMark } from "@/components/BrandMark";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      <div className="space-y-3 text-sm leading-relaxed text-muted-foreground">{children}</div>
    </section>
  );
}

const SUB_PROCESSORS = [
  {
    name: "Google (Gemini API)",
    purpose: "Generates answers for the \"Ask\" data-chat feature. Your question and the relevant production data needed to answer it are sent to Gemini when you use this feature.",
    location: "United States",
  },
  {
    name: "Amazon Web Services (SES)",
    purpose: "Delivers transactional emails: password reset links and email verification.",
    location: "Region set by whoever deployed this instance",
  },
];

export function Privacy() {
  return (
    <div className="relative min-h-screen">
      <div className="app-backdrop" />
      <div className="relative mx-auto max-w-3xl px-6 py-12">
        <div className="mb-10 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <BrandMark className="size-8 text-foreground" title="Evowerk" />
            <span className="text-sm font-bold uppercase tracking-[0.16em] text-foreground">
              Evowerk Cloud MRP
            </span>
          </div>
          <Link to="/login" className="text-sm font-medium text-primary hover:underline">
            Back to log in
          </Link>
        </div>

        <div className="mb-10">
          <h1 className="text-3xl font-semibold tracking-tight text-foreground">Privacy policy</h1>
          <p className="mt-2 text-sm text-muted-foreground">Last updated 30 July 2026.</p>
        </div>

        <div className="space-y-10">
          <Section title="Who this policy covers">
            <p>
              Evowerk Cloud MRP is software built by Evowerk Intelligence (Pvt) Ltd, an Indian
              private limited company registered at 60, New Bagalur Layout, Sagyapuram, Bengaluru,
              Karnataka 560084, India. Each customer deploys their own copy of this software on
              infrastructure they own (AWS or Azure), and Evowerk does not host or have access to
              what runs there.
            </p>
            <p>
              This means: if your organization deployed this instance, your organization is the
              data controller for the data described below, and Evowerk is the software provider.
              If you are using a demo or trial instance operated directly by Evowerk, Evowerk is
              the data controller for that instance. Either way, this page describes what the
              software itself collects and does with your data when you use it.
            </p>
          </Section>

          <Section title="What we collect">
            <p><strong className="text-foreground">Account data:</strong> your email address, a hashed (never plaintext) password, your role, and, if you enable two-factor authentication, an encrypted MFA secret.</p>
            <p><strong className="text-foreground">Business data you or your organization enter:</strong> bills of materials, orders, inventory, supplier and customer contact details (name, email, phone), operator names on the shop floor, and any datasets uploaded for analytics.</p>
            <p><strong className="text-foreground">Security logs:</strong> an audit trail of account and security-relevant actions (login, logout, MFA changes, and similar), including the IP address and timestamp of each event.</p>
          </Section>

          <Section title="AI features">
            <p>
              The "Ask" feature answers natural-language questions about your production data using
              Google's Gemini API. When you use it, your question and the relevant data needed to
              answer it are sent to Gemini and are subject to Google's own data handling terms.
              Responses shown to you in this feature are AI-generated.
            </p>
          </Section>

          <Section title="Who we share data with">
            <p>We do not sell your data. The following processors handle data on our behalf, only for the purposes below:</p>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-left text-sm">
                <thead className="bg-secondary/40 text-xs uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="px-4 py-2 font-medium">Processor</th>
                    <th className="px-4 py-2 font-medium">Purpose</th>
                    <th className="px-4 py-2 font-medium">Location</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {SUB_PROCESSORS.map((p) => (
                    <tr key={p.name}>
                      <td className="px-4 py-3 align-top font-medium text-foreground">{p.name}</td>
                      <td className="px-4 py-3 align-top">{p.purpose}</td>
                      <td className="px-4 py-3 align-top">{p.location}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="International transfers">
            <p>
              Google and AWS may process data outside the EU/UK, including in the United States.
              Where this applies, we rely on the processors' own standard contractual clauses or
              equivalent safeguards for that transfer.
            </p>
          </Section>

          <Section title="How long we keep data">
            <p>Account data is kept while your account is active and deleted when you delete your account (see below).</p>
            <p>Security audit logs are kept for 12 months, then removed.</p>
            <p>Business records your organization enters, such as orders and inventory history, are retained according to your organization's own record-keeping needs and are not automatically deleted, since they are operational business records rather than personal data about you individually.</p>
          </Section>

          <Section title="Your rights">
            <p>You can:</p>
            <ul className="ml-5 list-disc space-y-1">
              <li>See what account data we hold about you by contacting us below.</li>
              <li>Correct inaccurate account details from within the app.</li>
              <li>Delete your account and its personal data at any time, yourself, from Settings.</li>
              <li>Object to or ask us to restrict a particular use of your data.</li>
              <li>Ask for a copy of your data in a portable format.</li>
            </ul>
          </Section>

          <Section title="Children">
            <p>This is workplace software for manufacturing operations. It is not directed at, and is not knowingly used by, children.</p>
          </Section>

          <Section title="Contact">
            <p>
              For any privacy question or request, contact Evowerk Intelligence (Pvt) Ltd at{" "}
              <a href="mailto:zeeshan@evowerkintelligence.com" className="font-medium text-primary hover:underline">
                zeeshan@evowerkintelligence.com
              </a>
              .
            </p>
          </Section>
        </div>
      </div>
    </div>
  );
}
