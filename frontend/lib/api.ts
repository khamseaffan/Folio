export type Account = {
  email: string;
  display_name: string | null;
};

export type ApiValue<T> = {
  kind: "value";
  value: T;
};

export type ApiDown = {
  kind: "down";
};

export type ApiMessage = {
  kind: "message";
  message: string;
  unauthorized: boolean;
  refused: boolean;
};

export type ApiResult<T> = ApiValue<T> | ApiDown | ApiMessage;

const FALLBACK = "This could not be completed.";

type HttpResult = {
  status: number;
  payload: unknown;
};

function apiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  const raw =
    typeof configured === "string" && configured.trim() !== ""
      ? configured.trim()
      : "http://localhost:8000";
  return raw.replace(/\/+$/, "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function accountFrom(body: unknown): Account | null {
  if (!isRecord(body)) return null;
  const email = body.email;
  const displayName = body.display_name;
  if (typeof email !== "string") return null;
  if (displayName !== null && typeof displayName !== "string") return null;
  return { email, display_name: displayName };
}

function errorSentence(body: unknown): string | null {
  if (!isRecord(body)) return null;
  const error = body.error;
  if (typeof error !== "string" || error.trim() === "") return null;
  return error;
}

function isDownBody(body: unknown): boolean {
  return isRecord(body) && body.status === "down";
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (text.trim() === "") return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

async function call(
  path: string,
  method: "GET" | "POST",
  body?: string,
): Promise<HttpResult | ApiMessage> {
  const headers = new Headers();
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  try {
    const response = await fetch(`${apiBase()}${path}`, {
      method,
      credentials: "include",
      headers,
      cache: "no-store",
      ...(body !== undefined ? { body } : {}),
    });
    return { status: response.status, payload: await readBody(response) };
  } catch {
    return {
      kind: "message",
      message: FALLBACK,
      unauthorized: false,
      refused: false,
    };
  }
}

function classify<T>(
  http: HttpResult,
  parse: (body: unknown) => T | null,
): ApiResult<T> {
  if (http.status === 503 && isDownBody(http.payload)) {
    return { kind: "down" };
  }
  if (http.status >= 200 && http.status < 300) {
    const value = parse(http.payload);
    if (value === null) {
      return {
        kind: "message",
        message: FALLBACK,
        unauthorized: false,
        refused: false,
      };
    }
    return { kind: "value", value };
  }
  return {
    kind: "message",
    message: errorSentence(http.payload) ?? FALLBACK,
    unauthorized: http.status === 401,
    refused: true,
  };
}

export async function getReady(): Promise<ApiResult<{ status: "ok" }>> {
  const result = await call("/ready", "GET");
  if ("kind" in result) return result;
  return classify(result, (body) => {
    if (isRecord(body) && body.status === "ok") return { status: "ok" as const };
    return null;
  });
}

export async function getSession(): Promise<ApiResult<Account>> {
  const result = await call("/session", "GET");
  if ("kind" in result) return result;
  return classify(result, accountFrom);
}

export async function postSignIn(idToken: string): Promise<ApiResult<Account>> {
  const result = await call(
    "/sign-in",
    "POST",
    JSON.stringify({ id_token: idToken }),
  );
  if ("kind" in result) return result;
  return classify(result, accountFrom);
}

export async function postDisplayName(
  displayName: string,
): Promise<ApiResult<Account>> {
  const result = await call(
    "/display-name",
    "POST",
    JSON.stringify({ display_name: displayName }),
  );
  if ("kind" in result) return result;
  return classify(result, accountFrom);
}

export async function postSignOut(): Promise<ApiResult<true>> {
  const result = await call("/sign-out", "POST");
  if ("kind" in result) return result;
  if (result.status === 204) return { kind: "value", value: true };
  if (result.status === 503 && isDownBody(result.payload)) return { kind: "down" };
  return {
    kind: "message",
    message: errorSentence(result.payload) ?? FALLBACK,
    unauthorized: result.status === 401,
    refused: result.status >= 400,
  };
}
