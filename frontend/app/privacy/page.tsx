import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy policy · Folio",
  description: "What Folio stores about you, who handles it, and how to delete it.",
};

export default function PrivacyPage() {
  return (
    <main className="doc">
      <Link className="doc-back" href="/">
        Back to Folio
      </Link>
      <h1>Privacy policy</h1>
      <p className="doc-date">Last updated November 15, 2026</p>

      <p>
        Folio is a private assistant at folio.khamseaffan.com. It is run by Affan Khamse. This page
        says what Folio stores about you, who handles it, and how to remove it. Questions go to
        khamseaffan@gmail.com.
      </p>

      <h2>Who can use Folio</h2>
      <p>
        Folio is invite only. Only approved email addresses can sign in. Each person has a private
        account, and no one else can open its conversations, notes, files, or reports.
      </p>

      <h2>What Folio stores</h2>
      <ul>
        <li>Your email address and the display name you choose.</li>
        <li>Your conversations with the assistant.</li>
        <li>
          Notes the assistant keeps about you and how you like to work, which you can read in your
          profile.
        </li>
        <li>Your schedules, and the research reports it writes for you, with their sources.</li>
        <li>Files you upload or import, when you choose to.</li>
        <li>
          Usage counts: how many tokens each request used, tied to your email address, so the usage
          limit can be enforced.
        </li>
      </ul>

      <h2>How it is used</h2>
      <p>
        Only to run the assistant for you. Folio shows no ads, does not sell your data, and does not
        use it to build profiles of you.
      </p>

      <h2>Who else handles your data</h2>
      <p>These services process data so that Folio can work.</p>
      <ul>
        <li>
          <strong>The language model provider</strong> receives your messages, and any file or note
          the assistant needs, to write a reply. Speech you dictate is sent to a speech-to-text
          model, and the audio is discarded once the text exists.
        </li>
        <li>
          <strong>A web search service</strong> receives the search words the assistant writes for a
          research task. They can include words from your question. Pages opened for research are
          requested without your name, email, or account.
        </li>
        <li>
          <strong>Google</strong> provides sign-in (Firebase Authentication) and hosts the
          application and its database and file storage on Google Cloud, in the United States.
        </li>
        <li>
          <strong>Vercel</strong> serves the website.
        </li>
      </ul>

      <h2>Google Drive</h2>
      <p>
        Connecting Google Drive is optional. When you connect it, Folio asks only for access to the
        files you choose in Google&apos;s file picker. It cannot see the rest of your Drive.
      </p>
      <ul>
        <li>Folio stores a private, read-only copy of each file you pick. It never changes Drive.</li>
        <li>
          The sign-in token that lets Folio talk to Google is encrypted, belongs to your account, and
          is used for nothing else.
        </li>
        <li>
          You can disconnect Drive in Folio at any time. That removes the token. The copies you
          imported stay until you delete them or delete your account.
        </li>
      </ul>
      <p>
        Folio&apos;s use and transfer of information received from Google APIs adheres to the{" "}
        <a
          href="https://developers.google.com/terms/api-services-user-data-policy"
          target="_blank"
          rel="noopener noreferrer"
        >
          Google API Services User Data Policy
        </a>
        , including the Limited Use requirements.
      </p>

      <h2>Keeping and deleting your data</h2>
      <p>
        You can delete your account in Settings. Your conversations, notes, files, reports,
        schedules, and sign-in details are deleted at once and cannot be restored. Token usage
        counts and any usage limit stay with your email address, so that deleting an account does
        not reset a limit.
      </p>

      <h2>Security</h2>
      <p>
        Data is encrypted in transit and at rest by Google Cloud. Sessions end after 24 hours of
        inactivity. Integration tokens are encrypted with Google Cloud Key Management. The person
        who runs Folio can see usage totals and stop an account, and cannot read another
        person&apos;s conversations, files, or notes through the product.
      </p>

      <h2>Children</h2>
      <p>Folio is not made for children, and it is not offered to them.</p>

      <h2>Changes</h2>
      <p>If this policy changes, the date at the top changes with it.</p>
    </main>
  );
}
