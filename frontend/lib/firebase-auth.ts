import type { Auth, User } from "firebase/auth";

const EMAIL_KEY = "folioEmailForSignIn";

export type TokenResult =
  | { kind: "token"; token: string }
  | { kind: "cancel" }
  | { kind: "unavailable" }
  | { kind: "message"; message: string };

export type SendLinkResult =
  | { kind: "sent" }
  | { kind: "unavailable" }
  | { kind: "message"; message: string };

export type CurrentUserResult = TokenResult | { kind: "none" };

type WebConfig = {
  apiKey: string;
  authDomain: string;
  projectId: string;
  appId: string;
  storageBucket?: string;
  messagingSenderId?: string;
};

let authPromise: Promise<Auth | null> | null = null;

function present(value: string | undefined): value is string {
  return typeof value === "string" && value !== "";
}

export function readFirebaseConfig(): WebConfig | null {
  const apiKey = process.env.NEXT_PUBLIC_FIREBASE_API_KEY;
  const authDomain = process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN;
  const projectId = process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
  const appId = process.env.NEXT_PUBLIC_FIREBASE_APP_ID;
  if (!present(apiKey) || !present(authDomain) || !present(projectId) || !present(appId)) {
    return null;
  }
  const config: WebConfig = { apiKey, authDomain, projectId, appId };
  const storageBucket = process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET;
  const messagingSenderId = process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID;
  if (present(storageBucket)) config.storageBucket = storageBucket;
  if (present(messagingSenderId)) config.messagingSenderId = messagingSenderId;
  return config;
}

function loadAuth(): Promise<Auth | null> {
  authPromise ??= createAuth();
  return authPromise;
}

async function createAuth(): Promise<Auth | null> {
  const config = readFirebaseConfig();
  if (config === null) return null;
  try {
    const { getApp, getApps, initializeApp } = await import("firebase/app");
    const { getAuth } = await import("firebase/auth");
    const app = getApps().length > 0 ? getApp() : initializeApp(config);
    return getAuth(app);
  } catch {
    return null;
  }
}

function firebaseCode(error: unknown): string | null {
  if (typeof error !== "object" || error === null) return null;
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

function noticeFor(error: unknown): string | null {
  const code = firebaseCode(error);
  if (code === "auth/popup-closed-by-user" || code === "auth/cancelled-popup-request") {
    return null;
  }
  if (code === "auth/popup-blocked") {
    return "Allow the sign-in window, then try again.";
  }
  if (code === "auth/invalid-email" || code === "auth/missing-email") {
    return "Enter an email address.";
  }
  if (
    code === "auth/invalid-action-code" ||
    code === "auth/expired-action-code" ||
    code === "auth/invalid-oob-code"
  ) {
    return "This sign-in link has expired. Send a new one.";
  }
  return "This sign-in could not be started.";
}

async function idToken(user: User): Promise<TokenResult> {
  try {
    const token = await user.getIdToken();
    if (token === "") {
      return { kind: "message", message: "This sign-in could not be started." };
    }
    return { kind: "token", token };
  } catch {
    return { kind: "message", message: "This sign-in could not be started." };
  }
}

export function readStoredEmail(): string | null {
  try {
    return window.localStorage.getItem(EMAIL_KEY);
  } catch {
    return null;
  }
}

export function storeEmail(email: string): void {
  try {
    window.localStorage.setItem(EMAIL_KEY, email);
  } catch {
    // localStorage can be blocked. The address can be typed again on return.
  }
}

export function clearStoredEmail(): void {
  try {
    window.localStorage.removeItem(EMAIL_KEY);
  } catch {
    return;
  }
}

export async function signInWithGoogle(): Promise<TokenResult> {
  const auth = await loadAuth();
  if (auth === null) return { kind: "unavailable" };
  try {
    const { GoogleAuthProvider, signInWithPopup } = await import("firebase/auth");
    const provider = new GoogleAuthProvider();
    provider.setCustomParameters({ prompt: "select_account" });
    const credential = await signInWithPopup(auth, provider);
    return idToken(credential.user);
  } catch (error) {
    const message = noticeFor(error);
    if (message === null) return { kind: "cancel" };
    return { kind: "message", message };
  }
}

export async function sendEmailLink(email: string): Promise<SendLinkResult> {
  const auth = await loadAuth();
  if (auth === null) return { kind: "unavailable" };
  const trimmed = email.trim();
  try {
    const { sendSignInLinkToEmail } = await import("firebase/auth");
    await sendSignInLinkToEmail(auth, trimmed, {
      url: window.location.origin,
      handleCodeInApp: true,
    });
    storeEmail(trimmed);
    return { kind: "sent" };
  } catch (error) {
    const message = noticeFor(error);
    return {
      kind: "message",
      message: message ?? "This sign-in could not be started.",
    };
  }
}

export async function completeEmailLink(
  email: string,
  href: string,
): Promise<TokenResult> {
  const auth = await loadAuth();
  if (auth === null) return { kind: "unavailable" };
  try {
    const { isSignInWithEmailLink, signInWithEmailLink } = await import("firebase/auth");
    if (!isSignInWithEmailLink(auth, href)) {
      return {
        kind: "message",
        message: "This sign-in link has expired. Send a new one.",
      };
    }
    const credential = await signInWithEmailLink(auth, email.trim(), href);
    clearStoredEmail();
    return idToken(credential.user);
  } catch (error) {
    const message = noticeFor(error);
    return {
      kind: "message",
      message: message ?? "This sign-in could not be started.",
    };
  }
}

export async function emailLinkHref(): Promise<string | null> {
  const auth = await loadAuth();
  if (auth === null) return null;
  const { isSignInWithEmailLink } = await import("firebase/auth");
  const href = window.location.href;
  return isSignInWithEmailLink(auth, href) ? href : null;
}

export async function currentIdToken(): Promise<CurrentUserResult> {
  const auth = await loadAuth();
  if (auth === null) return { kind: "unavailable" };
  const { onAuthStateChanged } = await import("firebase/auth");
  const user = await new Promise<User | null>((resolve) => {
    const stop = onAuthStateChanged(auth, (next) => {
      stop();
      resolve(next);
    });
  });
  if (user === null) return { kind: "none" };
  return idToken(user);
}

export async function signOutFirebase(): Promise<void> {
  const auth = await loadAuth();
  if (auth === null) return;
  try {
    const { signOut } = await import("firebase/auth");
    await signOut(auth);
  } catch {
    return;
  }
}
